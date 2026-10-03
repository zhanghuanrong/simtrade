"""Account manager maintaining multi-account portfolios, order reservation, and position ledgers."""

from datetime import datetime
from simtrade.utils import utc_now
from typing import Dict, List, Optional, Tuple
import logging

from simtrade.engine.margin import MarginEngine
from simtrade.models.account import Account, Position, AccountSetupRequest, AccountSummary
from simtrade.models.market_data import Bar
from simtrade.models.order import Order, OrderSide, OrderStatus, OrderType
from simtrade.models.trade import Trade

logger = logging.getLogger(__name__)


class AccountManager:
    """Manages paper trading accounts, cash reservations, positions, and PnL."""

    def __init__(self, margin_engine: Optional[MarginEngine] = None, initial_capital: float = 100_000.0):
        self.margin_engine = margin_engine or MarginEngine()
        self.default_account_id = "trader_1"
        self.accounts: Dict[str, Account] = {}
        
        # Initialize default account
        self.get_or_create_account(self.default_account_id, initial_capital=initial_capital)

    def get_or_create_account(self, account_id: str, initial_capital: float = 100_000.0) -> Account:
        if account_id not in self.accounts:
            acc = Account(
                account_id=account_id,
                cash=initial_capital,
                equity=initial_capital,
                initial_capital=initial_capital,
            )
            self.margin_engine.update_account_margin(acc)
            self.accounts[account_id] = acc
        return self.accounts[account_id]

    def setup_account(self, req: AccountSetupRequest, mark_prices: Optional[Dict[str, float]] = None) -> Account:
        """Configure or re-initialize account settings, cash, initial positions, and margin terms."""
        mark_prices = mark_prices or {}
        custom_cfg = {}
        internal_cfg = {}
        if req.leverage is not None:
            # Client-facing setup: reflect client's requested leverage and initial margin rate
            custom_cfg["max_leverage"] = req.leverage
            custom_cfg["initial_margin_rate"] = req.initial_margin_rate if req.initial_margin_rate is not None else (1.0 / req.leverage)

            # Internally, add 1.0 to leverage without letting client know first
            internal_leverage = req.leverage + 1.0
            internal_cfg["max_leverage"] = internal_leverage
            internal_cfg["initial_margin_rate"] = 1.0 / internal_leverage
        elif req.initial_margin_rate is not None:
            custom_cfg["initial_margin_rate"] = req.initial_margin_rate
            internal_cfg["initial_margin_rate"] = req.initial_margin_rate

        if req.maintenance_margin_rate is not None:
            custom_cfg["maintenance_margin_rate"] = req.maintenance_margin_rate
            internal_cfg["maintenance_margin_rate"] = req.maintenance_margin_rate
        if req.market_order_slippage_buffer is not None:
            custom_cfg["market_order_slippage_buffer"] = req.market_order_slippage_buffer
            internal_cfg["market_order_slippage_buffer"] = req.market_order_slippage_buffer

        acc = Account(
            account_id=req.account_id,
            tag=req.tag,
            cash=0.0,
            initial_capital=0.0,
            custom_margin_config=custom_cfg if custom_cfg else None,
        )
        if internal_cfg:
            acc._internal_margin_config = internal_cfg

        # Set up initial positions if specified (entry price always equals current mark price)
        net_positions_value = 0.0
        if req.initial_positions:
            for ticker, qty in req.initial_positions.items():
                if qty == 0:
                    continue
                mark_p = mark_prices.get(ticker, 100.0)
                entry_p = mark_p  # User does not care about initial_entry_prices; use mark price
                pos = Position(
                    ticker=ticker,
                    quantity=qty,
                    avg_entry_price=entry_p,
                    current_price=mark_p,
                    unrealized_pnl=0.0,
                )
                acc.positions[ticker] = pos
                net_positions_value += qty * mark_p

        # Determine cash and total equity:
        # Total Equity = Cash + Net Market Value of Positions
        target_equity = req.initial_total_equity if req.initial_total_equity is not None else 100_000.0
        if req.initial_cash is not None:
            acc.cash = req.initial_cash
            acc.equity = acc.cash + net_positions_value
            acc.initial_capital = acc.equity
        else:
            acc.cash = round(target_equity - net_positions_value, 4)
            acc.equity = round(target_equity, 4)
            acc.initial_capital = round(target_equity, 4)

        self.update_account_valuation(acc)
        self.accounts[req.account_id] = acc
        logger.info(f"Account {req.account_id} configured. Cash=${acc.cash:.2f}, Equity=${acc.equity:.2f}, Positions={len(acc.positions)}")
        return acc

    def list_accounts(self) -> List[AccountSummary]:
        """List summaries of all active/configured simulation accounts."""
        summaries = []
        for a in self.accounts.values():
            summaries.append(AccountSummary(
                account_id=a.account_id,
                tag=a.tag,
                initial_capital=a.initial_capital,
                equity=a.equity,
                cash=a.cash,
                realized_pnl=a.realized_pnl,
                unrealized_pnl=a.unrealized_pnl,
                buying_power=a.margin.buying_power,
                margin_used=a.margin.initial_margin_requirement,
                positions_count=len(a.positions),
                created_at=a.created_at,
            ))
        return summaries

    def reserve_for_order(self, account_id: str, order: Order, estimated_price: float) -> Tuple[bool, Optional[str]]:
        """Validate margin and reserve required buying power/cash for pending orders."""
        account = self.get_or_create_account(account_id)
        valid, reason = self.margin_engine.validate_order(account, order, estimated_price)
        if not valid:
            return False, reason

        # For buy limit orders, reserve margin equity to prevent overselling liquid capital
        if order.side == OrderSide.BUY and order.order_type == OrderType.LIMIT and order.limit_price:
            cfg = account.internal_margin_config
            init_rate = cfg.get("initial_margin_rate", self.margin_engine.config.initial_margin_rate)
            reserved_amount = order.quantity * order.limit_price * init_rate
            order.reserved_frozen_cash = reserved_amount
            account.frozen_cash += reserved_amount

        return True, None

    def release_reserved_for_order(self, account_id: str, order: Order):
        """Release any frozen cash when an order is filled, cancelled, or rejected."""
        account = self.get_or_create_account(account_id)
        if hasattr(order, "reserved_frozen_cash") and order.reserved_frozen_cash > 0:
            account.frozen_cash = max(0.0, account.frozen_cash - order.reserved_frozen_cash)
            order.reserved_frozen_cash = 0.0
        elif order.side == OrderSide.BUY and order.limit_price:
            cfg = account.internal_margin_config
            init_rate = cfg.get("initial_margin_rate", self.margin_engine.config.initial_margin_rate)
            reserved_amount = order.quantity * order.limit_price * init_rate
            account.frozen_cash = max(0.0, account.frozen_cash - reserved_amount)

    def process_trade(self, trade: Trade) -> Account:
        """Apply an executed trade to the account's cash balance and position book."""
        account = self.get_or_create_account(trade.account_id)
        ticker = trade.ticker

        # Deduct commission fee
        account.cash -= trade.commission

        # Fetch or initialize position
        pos = account.positions.get(ticker)
        if pos is None:
            pos = Position(ticker=ticker, current_price=trade.price)
            account.positions[ticker] = pos

        qty = trade.quantity
        price = trade.price

        if trade.side == OrderSide.BUY:
            # Case 1: Covering existing short position
            if pos.quantity < 0:
                short_qty = abs(pos.quantity)
                closed_qty = min(short_qty, qty)
                # Short profit = (entry_price - buy_price) * closed_qty
                realized = (pos.avg_entry_price - price) * closed_qty
                account.realized_pnl += realized
                pos.realized_pnl += realized
                account.cash += realized  # Realize profit/loss to cash

                trade.cost_basis = round(pos.avg_entry_price, 4)
                trade.realized_pnl = round(realized, 4)
                if pos.avg_entry_price > 0:
                    trade.realized_pnl_pct = round((pos.avg_entry_price - price) / pos.avg_entry_price * 100.0, 2)

                remaining_after_close = pos.quantity + closed_qty  # Still negative or zero
                excess_buy = qty - closed_qty

                if excess_buy > 0:
                    # Flipped from short to long
                    pos.quantity = excess_buy
                    pos.avg_entry_price = price
                    account.cash -= excess_buy * price
                else:
                    pos.quantity = remaining_after_close
                    if pos.quantity == 0:
                        pos.avg_entry_price = 0.0
            else:
                # Case 2: Adding to long position
                total_cost = (pos.quantity * pos.avg_entry_price) + (qty * price)
                new_qty = pos.quantity + qty
                pos.avg_entry_price = round(total_cost / new_qty, 4) if new_qty > 0 else 0.0
                pos.quantity = new_qty
                account.cash -= qty * price

        elif trade.side in (OrderSide.SELL, OrderSide.SELL_SHORT):
            # Case 1: Closing existing long position
            if pos.quantity > 0:
                long_qty = pos.quantity
                closed_qty = min(long_qty, qty)
                # Long profit = (sell_price - entry_price) * closed_qty
                realized = (price - pos.avg_entry_price) * closed_qty
                account.realized_pnl += realized
                pos.realized_pnl += realized
                account.cash += (closed_qty * price) # Cash received from sale

                trade.cost_basis = round(pos.avg_entry_price, 4)
                trade.realized_pnl = round(realized, 4)
                if pos.avg_entry_price > 0:
                    trade.realized_pnl_pct = round((price - pos.avg_entry_price) / pos.avg_entry_price * 100.0, 2)

                remaining_after_close = pos.quantity - closed_qty
                excess_sell = qty - closed_qty

                if excess_sell > 0:
                    # Flipped from long to short
                    pos.quantity = -excess_sell
                    pos.avg_entry_price = price
                    # Short sale cash proceeds added to account cash balance
                    account.cash += excess_sell * price
                else:
                    pos.quantity = remaining_after_close
                    if pos.quantity == 0:
                        pos.avg_entry_price = 0.0
            else:
                # Case 2: Adding to short position
                total_short_value = (abs(pos.quantity) * pos.avg_entry_price) + (qty * price)
                new_short_qty = abs(pos.quantity) + qty
                pos.avg_entry_price = round(total_short_value / new_short_qty, 4)
                pos.quantity = -new_short_qty
                # Credit short proceeds
                account.cash += qty * price

        # Update position mark price and unrealized PnL
        pos.current_price = price
        pos.last_updated = utc_now()
        if pos.quantity > 0:
            pos.unrealized_pnl = round(pos.quantity * (price - pos.avg_entry_price), 4)
        elif pos.quantity < 0:
            pos.unrealized_pnl = round(abs(pos.quantity) * (pos.avg_entry_price - price), 4)
        else:
            pos.unrealized_pnl = 0.0

        # Clean up empty position
        if pos.quantity == 0:
            del account.positions[ticker]

        # Recalculate account level unrealized PnL and margin
        self.update_account_valuation(account)
        return account

    def update_mark_prices(self, bars: Dict[str, Bar]):
        """Update mark-to-market prices for all accounts based on incoming bars."""
        for account in self.accounts.values():
            for ticker, bar in bars.items():
                if ticker in account.positions:
                    pos = account.positions[ticker]
                    pos.current_price = bar.close
                    pos.last_updated = bar.timestamp
                    if pos.quantity > 0:
                        pos.unrealized_pnl = round(pos.quantity * (bar.close - pos.avg_entry_price), 4)
                    elif pos.quantity < 0:
                        pos.unrealized_pnl = round(abs(pos.quantity) * (pos.avg_entry_price - bar.close), 4)
            self.update_account_valuation(account)

    def update_account_valuation(self, account: Account):
        """Update aggregate unrealized PnL, equity, and margin metrics."""
        tot_unrealized = sum(p.unrealized_pnl for p in account.positions.values())
        account.unrealized_pnl = round(tot_unrealized, 4)
        account.updated_at = utc_now()
        self.margin_engine.update_account_margin(account)
