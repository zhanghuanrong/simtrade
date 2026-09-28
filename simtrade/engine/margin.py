"""Margin and risk management engine enforcing leverage, maintenance, and liquidation rules."""

from datetime import datetime
from simtrade.utils import utc_now
from typing import Dict, List, Optional, Tuple
import logging

from simtrade.config import MarginConfig
from simtrade.models.account import Account, MarginMetrics, MarginCallEvent, Position
from simtrade.models.order import Order, OrderSide, OrderStatus, OrderType

logger = logging.getLogger(__name__)


class MarginEngine:
    """Calculates margin requirements, enforces risk limits, and manages liquidations."""

    def __init__(self, config: Optional[MarginConfig] = None):
        self.config = config or MarginConfig()

    def update_account_margin(self, account: Account) -> MarginMetrics:
        """Recalculate complete margin metrics for the given account."""
        total_long_value = 0.0
        total_short_value = 0.0

        for pos in account.positions.values():
            if pos.quantity > 0:
                total_long_value += pos.quantity * pos.current_price
            elif pos.quantity < 0:
                total_short_value += abs(pos.quantity) * pos.current_price

        gross_market_value = round(total_long_value + total_short_value, 4)
        
        # In a standard margin account:
        # Equity = Cash + Long Market Value - Short Market Value
        equity = round(account.cash + total_long_value - total_short_value, 4)
        account.equity = equity
        account.borrowed_margin = max(0.0, -account.cash)

        # Calculate initial margin requirements:
        init_req_long = total_long_value * self.config.initial_margin_rate
        init_req_short = total_short_value * self.config.short_initial_margin_rate
        total_init_req = round(init_req_long + init_req_short, 4)

        # Calculate maintenance margin requirements:
        maint_req_long = total_long_value * self.config.maintenance_margin_rate
        maint_req_short = total_short_value * self.config.short_maintenance_margin_rate
        total_maint_req = round(maint_req_long + maint_req_short, 4)

        # Margin excess (headroom above maintenance margin):
        margin_excess = round(equity - total_maint_req, 4)

        # Buying power calculation:
        # Uncommitted equity available for new positions = max(0, equity - total_init_req)
        # Buying power = uncommitted equity / initial_margin_rate
        uncommitted_equity = max(0.0, equity - total_init_req)
        buying_power = round(uncommitted_equity / self.config.initial_margin_rate, 4)

        # Current leverage
        leverage = round(gross_market_value / max(equity, 1.0), 2) if gross_market_value > 0 else 0.0

        # Margin call condition: Equity < Maintenance Margin Requirement
        is_margin_call = equity < total_maint_req and gross_market_value > 0
        margin_call_amount = round(total_maint_req - equity, 4) if is_margin_call else 0.0

        metrics = MarginMetrics(
            total_long_value=round(total_long_value, 4),
            total_short_value=round(total_short_value, 4),
            gross_market_value=gross_market_value,
            initial_margin_requirement=total_init_req,
            maintenance_margin_requirement=total_maint_req,
            margin_excess=margin_excess,
            buying_power=buying_power,
            leverage=leverage,
            is_margin_call=is_margin_call,
            margin_call_amount=margin_call_amount,
        )
        account.margin = metrics
        return metrics

    def validate_order(self, account: Account, order: Order, estimated_price: float) -> Tuple[bool, Optional[str]]:
        """Validate whether an order meets margin and risk requirements before acceptance."""
        notional = order.quantity * estimated_price
        
        # Check shorting policy
        if order.side == OrderSide.SELL_SHORT and not self.config.allow_short:
            return False, "Short selling is disabled by server margin policy"

        # Determine if closing existing position or opening new
        pos = account.positions.get(order.ticker)
        current_qty = pos.quantity if pos else 0.0

        is_opening = False
        if order.side == OrderSide.BUY:
            # If current position is short, buy covers short (closing), any excess opens long
            if current_qty < 0:
                short_to_cover = min(abs(current_qty), order.quantity)
                excess_buy = order.quantity - short_to_cover
                if excess_buy > 0:
                    is_opening = True
                    notional = excess_buy * estimated_price
            else:
                is_opening = True
        elif order.side in (OrderSide.SELL, OrderSide.SELL_SHORT):
            # If current position is long, sell closes long, excess opens short
            if current_qty > 0:
                long_to_sell = min(current_qty, order.quantity)
                excess_short = order.quantity - long_to_sell
                if excess_short > 0:
                    is_opening = True
                    notional = excess_short * estimated_price
            else:
                is_opening = True

        if is_opening:
            # Margin requirement for this new order
            req_rate = self.config.initial_margin_rate if order.side == OrderSide.BUY else self.config.short_initial_margin_rate
            required_margin = notional * req_rate

            # Available uncommitted equity considering frozen cash
            available_equity = account.equity - account.margin.initial_margin_requirement - account.frozen_cash
            if available_equity < required_margin:
                return False, (
                    f"Insufficient margin: Required initial margin ${required_margin:.2f}, "
                    f"available margin ${available_equity:.2f} (Max Buying Power: ${account.margin.buying_power:.2f})"
                )

        return True, None

    def accrue_financing_fees(self, account: Account, minutes_elapsed: int = 1) -> float:
        """Accrue margin debit interest and short borrowing fees per simulated time step."""
        # 252 trading days * 6.5 hours * 60 minutes = 98,280 trading minutes/year
        minutes_per_year = 252.0 * 390.0
        fraction_of_year = minutes_elapsed / minutes_per_year

        fee_total = 0.0

        # Margin interest on cash debit
        if account.cash < 0:
            interest = abs(account.cash) * self.config.annual_margin_interest_rate * fraction_of_year
            account.cash -= interest
            fee_total += interest

        # Short borrow fee
        if account.margin.total_short_value > 0:
            borrow_fee = account.margin.total_short_value * self.config.annual_borrow_fee_rate * fraction_of_year
            account.cash -= borrow_fee
            fee_total += borrow_fee

        if fee_total > 0:
            self.update_account_margin(account)

        return round(fee_total, 4)

    def check_and_generate_liquidations(self, account: Account, mark_prices: Dict[str, float]) -> List[Order]:
        """If account is in margin call and auto-liquidation is enabled, generate closing market orders."""
        if not self.config.auto_liquidate_on_call:
            return []

        self.update_account_margin(account)
        if not account.margin.is_margin_call:
            return []

        logger.warning(
            f"Margin call triggered for account {account.account_id}: Equity=${account.equity:.2f} < "
            f"MaintReq=${account.margin.maintenance_margin_requirement:.2f}. Initiating liquidation."
        )

        liquidation_orders: List[Order] = []

        # Sort positions by highest risk / largest absolute market value
        sorted_positions = sorted(
            account.positions.values(),
            key=lambda p: abs(p.quantity * mark_prices.get(p.ticker, p.current_price)),
            reverse=True,
        )

        for pos in sorted_positions:
            if pos.quantity == 0:
                continue
            
            # If long, create MARKET SELL; if short, create MARKET BUY
            side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
            qty_to_close = abs(pos.quantity)

            liq_order = Order(
                account_id=account.account_id,
                ticker=pos.ticker,
                side=side,
                order_type=OrderType.MARKET,
                quantity=qty_to_close,
                status=OrderStatus.PENDING,
                client_order_id=f"LIQ_{pos.ticker}_{int(utc_now().timestamp())}",
            )
            liquidation_orders.append(liq_order)

            # Apply liquidation penalty if configured
            if self.config.liquidation_penalty_rate > 0:
                est_p = mark_prices.get(pos.ticker, pos.current_price)
                penalty = qty_to_close * est_p * self.config.liquidation_penalty_rate
                account.cash -= penalty
                logger.info(f"Applied liquidation penalty: ${penalty:.2f} on {pos.ticker}")

        return liquidation_orders
