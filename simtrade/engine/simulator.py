"""Central simulation engine orchestrating clock, market data feeds, matching, and risk."""

import asyncio
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional
import logging

from simtrade.config import MarginConfig, MatchingConfig, SimulationConfig
from simtrade.engine.account_mgr import AccountManager
from simtrade.engine.clock import SimClock
from simtrade.engine.feeder import DataFeeder
from simtrade.engine.margin import MarginEngine
from simtrade.engine.matcher import MatchingEngine
from simtrade.models.account import Account
from simtrade.models.market_data import Bar
from simtrade.models.order import Order, OrderCreate, OrderSide, OrderStatus, OrderType
from simtrade.models.trade import Trade
from simtrade.reporting.ledger import EventLedger
from simtrade.reporting.pass_store import PassRecord, PassStore, PassSummary
from simtrade.utils import is_regular_trading_hours, utc_now

logger = logging.getLogger(__name__)


class Simulator:
    """Core simulation server coordinator."""

    def __init__(
        self,
        sim_config: Optional[SimulationConfig] = None,
        matching_config: Optional[MatchingConfig] = None,
        margin_config: Optional[MarginConfig] = None,
    ):
        self.sim_config = sim_config or SimulationConfig()
        self.matching_config = matching_config or MatchingConfig()
        self.margin_config = margin_config or MarginConfig()

        # Auto-detect default parquet if present
        data_file_to_use = self.sim_config.data_file
        if not data_file_to_use:
            default_p = Path("data/1m_20260817_now.parquet")
            if default_p.exists():
                data_file_to_use = str(default_p)

        # Initialize data feeder first
        self.feeder = DataFeeder(
            tickers=self.sim_config.tickers,
            data_file=data_file_to_use,
            data_dir=self.sim_config.data_dir,
            generate_synthetic=self.sim_config.generate_synthetic_if_missing,
        )
        self.sim_config.tickers = self.feeder.tickers

        # Engine subcomponents
        start_ts = self.feeder.start_time
        if start_ts and start_ts.time() == time(9, 31):
            start_ts = start_ts - timedelta(minutes=1)
        self.clock = SimClock(
            start_time=start_ts,
            speed_multiplier=self.sim_config.speed_multiplier,
            timeline=self.feeder.timeline,
        )
        self.matcher = MatchingEngine(config=self.matching_config)
        self.margin_engine = MarginEngine(config=self.margin_config)
        self.account_mgr = AccountManager(margin_engine=self.margin_engine)
        self.ledger = EventLedger()
        self.pass_store = PassStore(storage_dir="reports")

        # Current state
        self.latest_bars: Dict[str, Bar] = {}
        self.all_trades: List[Trade] = []
        self._loop_task: Optional[asyncio.Task] = None
        self._broadcast_callbacks: List[Callable[[str, Any], Coroutine[Any, Any, None]]] = []
        self.account_bar_cursors: Dict[str, datetime] = {}

    def register_broadcast_callback(self, callback: Callable[[str, Any], Coroutine[Any, Any, None]]):
        """Register an async callback for broadcasting events to WebSocket clients."""
        self._broadcast_callbacks.append(callback)

    async def _broadcast(self, event_type: str, data: Any):
        for cb in self._broadcast_callbacks:
            try:
                await cb(event_type, data)
            except Exception as e:
                logger.error(f"Error in broadcast callback {event_type}: {e}")

    def drain_unseen_bars(self, account_id: str = "trader_1") -> List[Dict[str, Any]]:
        """
        Drain all market bar events that occurred since the account's last interaction.
        Updates the high-water-mark cursor to current virtual simulation time.
        """
        cur_sim = self.clock.sim_current_time
        start_cursor = self.account_bar_cursors.get(account_id)
        if start_cursor is None:
            if self.feeder.timeline:
                start_cursor = self.feeder.timeline[0]
            else:
                start_cursor = None

        if hasattr(self.feeder, "get_bars_between"):
            unseen = self.feeder.get_bars_between(start_cursor, cur_sim)
        else:
            unseen = []

        self.account_bar_cursors[account_id] = cur_sim
        return unseen

    def submit_order(self, order_create: OrderCreate, account_id: str = "trader_1") -> Order:
        """Submit a new order from a client."""
        from simtrade.utils import to_eastern_time, is_regular_trading_hours
        virtual_sim_time = to_eastern_time(self.clock.now())
        wall_now = utc_now()
        unseen_bars = self.drain_unseen_bars(account_id)

        # 1. Enforce Regular Trading Hours (RTH) only (09:30 - 16:00 ET, Mon-Fri)
        is_rth, rth_reason = is_regular_trading_hours(virtual_sim_time)
        if not is_rth:
            order = Order(
                account_id=account_id,
                ticker=order_create.ticker,
                side=order_create.side,
                order_type=order_create.order_type,
                quantity=order_create.quantity,
                limit_price=order_create.limit_price,
                stop_price=order_create.stop_price,
                time_in_force=order_create.time_in_force,
                client_order_id=order_create.client_order_id,
                trading_time=virtual_sim_time,
                sim_created_at=virtual_sim_time,
                wall_received_at=wall_now,
                sim_updated_at=virtual_sim_time,
                created_at=virtual_sim_time,
                server_received_at=wall_now,
                updated_at=virtual_sim_time,
                status=OrderStatus.REJECTED,
                reject_reason=rth_reason,
                unseen_bars=unseen_bars,
            )
            self.matcher.all_orders[order.order_id] = order
            self.ledger.record("ORDER_REJECTED", account_id, virtual_sim_time, {
                "order_id": order.order_id,
                "ticker": order.ticker,
                "reason": rth_reason,
                "trading_time": virtual_sim_time.isoformat(),
            })
            return order

        order = Order(
            account_id=account_id,
            ticker=order_create.ticker,
            side=order_create.side,
            order_type=order_create.order_type,
            quantity=order_create.quantity,
            limit_price=order_create.limit_price,
            stop_price=order_create.stop_price,
            time_in_force=order_create.time_in_force,
            client_order_id=order_create.client_order_id,
            trading_time=virtual_sim_time,
            sim_created_at=virtual_sim_time,
            wall_received_at=wall_now,
            sim_updated_at=virtual_sim_time,
            created_at=virtual_sim_time,
            server_received_at=wall_now,
            updated_at=virtual_sim_time,
            unseen_bars=unseen_bars,
        )

        # Estimate execution price for margin check
        order_bar = self.latest_bars.get(order.ticker)
        if not order_bar and hasattr(self, "feeder") and self.feeder and self.feeder.timeline:
            eff_bars = self.feeder.get_bars_for_time(virtual_sim_time)
            order_bar = eff_bars.get(order.ticker)
        if not order_bar and hasattr(self, "feeder") and self.feeder:
            cur_bars = self.feeder.get_bars_for_time(self.clock.current_time)
            order_bar = cur_bars.get(order.ticker)
            if not order_bar and self.feeder.timeline:
                first_bars = self.feeder.get_bars_for_time(self.feeder.timeline[0])
                order_bar = first_bars.get(order.ticker)
        if order_bar:
            if order.order_type == OrderType.MARKET:
                base_price = order_bar.open
            elif order.order_type == OrderType.LIMIT and order.limit_price:
                base_price = min(order.limit_price, order_bar.open) if order.side == OrderSide.BUY else max(order.limit_price, order_bar.open)
            else:
                base_price = order.limit_price or order_bar.close
        else:
            fallback = self.feeder.get_latest_price(order.ticker) if hasattr(self.feeder, "get_latest_price") else None
            base_price = order.limit_price or fallback or 100.0

        if order.order_type == OrderType.MARKET:
            account = self.account_mgr.get_or_create_account(account_id)
            cfg = account.internal_margin_config
            buffer_rate = cfg.get("market_order_slippage_buffer", self.margin_config.market_order_slippage_buffer)
            if order.side in (OrderSide.BUY, OrderSide.SELL_SHORT):
                est_price = base_price * (1.0 + buffer_rate)
            else:
                est_price = base_price
        else:
            est_price = base_price

        # Validate with risk & margin engine
        valid, reject_reason = self.account_mgr.reserve_for_order(account_id, order, est_price)
        if not valid:
            order.status = OrderStatus.REJECTED
            order.reject_reason = reject_reason
            self.matcher.all_orders[order.order_id] = order
            self.ledger.record("ORDER_REJECTED", account_id, virtual_sim_time, {
                "order_id": order.order_id,
                "ticker": order.ticker,
                "reason": reject_reason,
            })
            return order

        # Register in matching engine
        self.matcher.add_order(order)
        self.ledger.record("ORDER_ACCEPTED", account_id, virtual_sim_time, {
            "order_id": order.order_id,
            "ticker": order.ticker,
            "side": order.side.value,
            "quantity": order.quantity,
            "type": order.order_type.value,
            "limit_price": order.limit_price,
        })

        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast("ORDER_UPDATE", order.model_dump(mode="json")))
        except RuntimeError:
            pass

        return order

    def cancel_order(self, order_id: str, account_id: str = "trader_1") -> Optional[Order]:
        """Cancel an active order."""
        order = self.matcher.cancel_order(order_id)
        if order:
            self.account_mgr.release_reserved_for_order(account_id, order)
            self.ledger.record("ORDER_CANCELLED", account_id, self.clock.current_time, {
                "order_id": order.order_id,
                "ticker": order.ticker,
            })
        return order

    async def step_to(self, target_time: datetime, account_id: str = "trader_1") -> Dict[str, Any]:
        """
        Advance simulation virtual time to target_time in America/New_York (ET).
        - For each elapsed 1-minute interval [t, t+1m):
          1. Matches pending orders against bar for interval [t, t+1m) (timestamped at t).
          2. Filled trades are timestamped at t (1m before).
          3. Advances clock to t+1m.
          4. Takes snapshot at t+1m.
        - Returns executed trades, updated orders, bars, cross_auction (if at open), and account.
        """
        from datetime import time, timedelta
        from simtrade.utils import to_eastern_time
        target_ts = to_eastern_time(target_time)
        current_ts = to_eastern_time(self.clock.current_time)

        if target_ts <= current_ts:
            acc = self.account_mgr.get_or_create_account(account_id)
            return {
                "status": "ignored",
                "reason": f"target_time is less than or equal to current simulation time ({current_ts})",
                "bars_processed": 0,
                "current_time": self.clock.current_time.isoformat(),
                "prev_time": current_ts.isoformat(),
                "trades": [],
                "orders": [],
                "bars": {},
                "account": acc.model_dump(mode="json"),
            }

        all_new_trades: List[Trade] = []
        updated_orders_map: Dict[str, Order] = {}
        last_bars: Dict[str, Bar] = {}
        bars_processed = 0

        # Step forward minute-by-minute while within regular trading hours
        while current_ts < target_ts:
            c_time = current_ts.time()

            # If at or after market close (16:00 ET), we do not process regular bars.
            # Jump directly across the gap to target_ts.
            if c_time >= time(16, 0) or c_time < time(9, 30):
                self.clock.set_time(target_ts, reason="GAP_JUMP")
                current_ts = target_ts
                last_bars = {}
                break

            interval_end = min(current_ts + timedelta(minutes=1), target_ts)

            # Fetch 1m bar for elapsed interval [current_ts, interval_end)
            bars = self.feeder.get_bars_for_time(interval_end)
            if bars:
                self.latest_bars.update(bars)
                last_bars = bars

            # 1. Match pending orders against this 1m bar
            for ticker, bar in bars.items():
                matches = self.matcher.match_bar(bar)
                for order, trade in matches:
                    # Treat order as filled 1m before (at current_ts)
                    trade.sim_timestamp = current_ts
                    trade.timestamp = current_ts
                    self.all_trades.append(trade)
                    all_new_trades.append(trade)
                    updated_orders_map[order.order_id] = order

                    self.account_mgr.process_trade(trade)
                    self.account_mgr.release_reserved_for_order(trade.account_id, order)

                    self.ledger.record(
                        "ORDER_FILLED" if order.status == OrderStatus.FILLED else "ORDER_PARTIALLY_FILLED",
                        trade.account_id,
                        current_ts,
                        {
                            "order_id": order.order_id,
                            "trade_id": trade.trade_id,
                            "ticker": trade.ticker,
                            "price": trade.price,
                            "quantity": trade.quantity,
                            "commission": trade.commission,
                        },
                    )

            # 2. Advance clock to interval_end
            self.clock.set_time(interval_end, reason="STEP_1M")
            current_ts = interval_end
            bars_processed += 1

            # 3. Update mark prices and check margin
            if bars:
                self.account_mgr.update_mark_prices(bars)

            for account in self.account_mgr.accounts.values():
                mark_prices = {t: b.close for t, b in self.latest_bars.items()}
                liq_orders = self.margin_engine.check_and_generate_liquidations(account, mark_prices)
                for liq_order in liq_orders:
                    self.matcher.add_order(liq_order)
                    self.ledger.record("MARGIN_LIQUIDATION_SUBMITTED", account.account_id, current_ts, {
                        "order_id": liq_order.order_id,
                        "ticker": liq_order.ticker,
                        "quantity": liq_order.quantity,
                    })

                positions_breakdown = {
                    ticker: round(pos.market_value, 2)
                    for ticker, pos in account.positions.items()
                    if abs(pos.quantity) > 1e-6
                }
                self.ledger.record_snapshot(
                    account_id=account.account_id,
                    sim_time=current_ts,
                    cash=account.cash,
                    equity=account.equity,
                    realized_pnl=account.realized_pnl,
                    unrealized_pnl=account.unrealized_pnl,
                    gross_market_value=account.margin.gross_market_value,
                    leverage=account.margin.leverage,
                    positions=positions_breakdown,
                    reserved_cash=round(account.frozen_cash, 2),
                )

            # Check if default account had margin call
            acc = self.account_mgr.get_or_create_account(account_id)
            if acc.margin.is_margin_call:
                bars_payload = {t: b.model_dump(mode="json") for t, b in last_bars.items()}
                unseen_bars = self.drain_unseen_bars(account_id)
                liq_info = {
                    "status": "LIQUIDATION_TRIGGERED",
                    "message": f"Account {account_id} equity (${acc.equity:.2f}) dropped below maintenance margin (${acc.margin.maintenance_margin_requirement:.2f})",
                    "current_time": self.clock.current_time.isoformat(),
                    "account_id": account_id,
                    "deficit": acc.margin.margin_call_amount,
                    "bars_processed": bars_processed,
                    "trades": [t.model_dump(mode="json") for t in all_new_trades],
                    "orders": [o.model_dump(mode="json") for o in updated_orders_map.values()],
                    "bars": bars_payload,
                    "unseen_bars": unseen_bars,
                    "account": acc.model_dump(mode="json"),
                }
                logger.warning(f"step_to halted at {self.clock.current_time} due to liquidation on account {account_id}")
                return liq_info

        # If arrived at session open (09:30), compute opening cross auction
        cross_auction = {}
        if current_ts.time() == time(9, 30):
            open_bar_time = current_ts + timedelta(minutes=1)
            open_bars = self.feeder.get_bars_for_time(open_bar_time)
            if open_bars:
                self.latest_bars.update(open_bars)
            cross_auction = {t: {"price": b.open, "volume": 1.0} for t, b in self.latest_bars.items()}

        # Broadcast events for Web UI
        bars_payload = {t: b.model_dump(mode="json") for t, b in last_bars.items()}
        if bars_payload:
            await self._broadcast("MARKET_BARS", {
                "timestamp": self.clock.current_time.isoformat(),
                "bars": bars_payload,
            })
        for trade in all_new_trades:
            await self._broadcast("TRADE_EXECUTION", trade.model_dump(mode="json"))
        for order in updated_orders_map.values():
            await self._broadcast("ORDER_UPDATE", order.model_dump(mode="json"))

        acc = self.account_mgr.get_or_create_account(account_id)
        await self._broadcast("ACCOUNT_UPDATE", acc.model_dump(mode="json"))

        unseen_bars = self.drain_unseen_bars(account_id)

        return {
            "status": "TARGET_REACHED",
            "current_time": self.clock.current_time.isoformat(),
            "bars_processed": bars_processed,
            "trades": [t.model_dump(mode="json") for t in all_new_trades],
            "orders": [o.model_dump(mode="json") for o in updated_orders_map.values()],
            "bars": bars_payload,
            "unseen_bars": unseen_bars,
            "cross_auction": cross_auction,
            "account": acc.model_dump(mode="json"),
        }

    async def step_until(self, target_time: datetime, account_id: str = "trader_1") -> Dict[str, Any]:
        return await self.step_to(target_time, account_id=account_id)

    async def step(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        acc_id = account_id or self.account_mgr.default_account_id
        target = self.clock.current_time + timedelta(minutes=1)
        res = await self.step_to(target, account_id=acc_id)
        res["timestamp"] = res["current_time"]
        res["trades_count"] = len(res.get("trades", []))
        return res

    async def _run_loop(self):
        """Asynchronous playback loop respecting speed multiplier."""
        logger.info("Simulation playback loop started")
        while self.clock.is_running:
            if not self.clock.is_paused:
                try:
                    await self.step()
                except Exception as e:
                    logger.error(f"Error during simulation step: {e}", exc_info=True)
            await self.clock.sleep_for_speed()

    def start(self, start_time: Optional[datetime] = None) -> Dict[str, Any]:
        """Start or initialize simulation at the specified start_time (ET)."""
        from simtrade.utils import to_eastern_time
        if start_time:
            self.clock.set_time(to_eastern_time(start_time), reason="START_TIME")
        elif self.feeder.timeline:
            first_ts = self.feeder.timeline[0]
            if first_ts.time() == time(9, 31):
                self.clock.set_time(first_ts - timedelta(minutes=1), reason="START_DEFAULT")
            else:
                self.clock.set_time(first_ts, reason="START_DEFAULT")
        self.clock.is_running = True
        self.clock.is_paused = False

        c_time = self.clock.current_time
        if c_time.time() == time(9, 30):
            open_bar_time = c_time + timedelta(minutes=1)
            open_bars = self.feeder.get_bars_for_time(open_bar_time)
            if open_bars:
                self.latest_bars.update(open_bars)
            cross_auction = {
                t: {"price": b.open, "volume": 1.0}
                for t, b in self.latest_bars.items()
            }
        else:
            bars = self.feeder.get_bars_for_time(c_time)
            if bars:
                self.latest_bars.update(bars)
            cross_auction = {
                t: {"price": b.open, "volume": 1.0}
                for t, b in self.latest_bars.items()
            }
        logger.info(f"Simulator started at {self.clock.current_time.isoformat()}")
        return {
            "status": "STARTED",
            "current_time": self.clock.current_time.isoformat(),
            "cross_auction": cross_auction,
        }

    def pause(self):
        """Pause playback."""
        self.clock.pause()
        logger.info("Simulator paused")

    def stop(self):
        """Stop playback completely."""
        self.clock.is_running = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        logger.info("Simulator stopped")

    def reset(self, start_time: Optional[datetime] = None):
        """Reset simulation playback cursor to start a fresh simulation pass."""
        self.pause()
        if self.feeder.timeline:
            idx = 0
            if start_time:
                # Find index closest to start_time
                for i, t in enumerate(self.feeder.timeline):
                    if t >= start_time:
                        idx = i
                        break
            reset_ts = self.feeder.timeline[idx]
            self.clock.cursor = idx + 1
        elif start_time:
            reset_ts = start_time
        else:
            reset_ts = self.feeder.start_time or datetime(2026, 1, 5, 9, 30, 0)

        self.clock.set_time(reset_ts, reason="SIMULATOR_RESET")

        self.clock.step_count = 0
        self.matcher.active_orders.clear()
        self.latest_bars.clear()
        self.account_bar_cursors.clear()
        # Seed initial market bar prices at reset time
        bars = self.feeder.get_bars_for_time(self.clock.current_time)
        self.latest_bars.update(bars)
        logger.info(f"Simulator reset to time {self.clock.current_time}")

    def get_metadata(self) -> Dict[str, Any]:
        """Get simulation metadata including time range, total bars, and available tickers."""
        total_bars = len(self.feeder.timeline)
        is_finished = (self.clock.cursor >= total_bars) if total_bars > 0 else False
        progress_pct = round((self.clock.cursor / max(1, total_bars)) * 100, 2) if total_bars > 0 else 0.0
        current_idx = max(0, self.clock.cursor - 1) if self.clock.step_count > 0 else 0

        local_start = getattr(self.clock, "local_start_time", None)
        sim_start = getattr(self.clock, "sim_trade_start_time", None)
        if not sim_start:
            if self.feeder.timeline:
                first_ts = self.feeder.timeline[0]
                if first_ts.time() == time(9, 31):
                    sim_start = first_ts - timedelta(minutes=1)
                else:
                    sim_start = first_ts
            else:
                sim_start = None

        trading_dates = sorted(list({t.date().isoformat() for t in self.feeder.timeline}))

        return {
            "local_start_time": local_start.isoformat() if local_start else None,
            "sim_trade_start_time": sim_start.isoformat() if sim_start else None,
            "start_time": sim_start.isoformat() if sim_start else None,
            "end_time": self.feeder.timeline[-1].isoformat() if self.feeder.timeline else None,
            "trading_dates": trading_dates,
            "current_time": self.clock.current_time.isoformat(),
            "total_bars": total_bars,
            "current_step": self.clock.step_count,
            "cursor": current_idx,
            "progress_pct": progress_pct,
            "is_finished": is_finished,
            "speed_multiplier": self.clock.speed_multiplier,
            "is_running": self.clock.is_running,
            "is_paused": self.clock.is_paused,
            "available_tickers": self.feeder.tickers,
            "active_accounts": [a.account_id for a in self.account_mgr.accounts.values()],
        }

    def export_trades_csv(self, account_id: Optional[str] = None) -> str:
        """Export executed trades in standard CSV format."""
        import io
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["trade_id", "account_id", "order_id", "ticker", "side", "price", "quantity", "notional", "commission", "timestamp"])

        trades = self.all_trades
        if account_id:
            trades = [t for t in trades if t.account_id == account_id]

        for t in trades:
            writer.writerow([
                t.trade_id,
                t.account_id,
                t.order_id,
                t.ticker,
                t.side.value,
                t.price,
                t.quantity,
                t.notional,
                t.commission,
                t.timestamp.isoformat(),
            ])
        return output.getvalue()

    def save_session(self, account_id: str, output_dir: str = "reports") -> Dict[str, str]:
        """Save all transactions, trades, and performance reports to local disk."""
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        acc = self.account_mgr.get_or_create_account(account_id)
        tag = acc.tag or account_id

        # 1. Save trades CSV
        csv_content = self.export_trades_csv(account_id)
        csv_file = out_path / f"trades_{tag}.csv"
        with open(csv_file, "w") as f:
            f.write(csv_content)

        # 2. Save Performance Report JSON
        import json
        from simtrade.reporting.performance import PerformanceAnalytics
        snapshots = self.ledger.get_snapshots(account_id)
        trades = [t for t in self.all_trades if t.account_id == account_id]
        perf = PerformanceAnalytics.calculate(snapshots, trades, initial_capital=acc.initial_capital)
        perf_file = out_path / f"performance_{tag}.json"
        with open(perf_file, "w") as f:
            json.dump(perf, f, indent=2)

        # 3. Save Ledger JSON
        ledger_entries = [e.model_dump(mode="json") for e in self.ledger.get_entries(account_id=account_id, limit=10000)]
        ledger_file = out_path / f"ledger_{tag}.json"
        with open(ledger_file, "w") as f:
            json.dump(ledger_entries, f, indent=2)

        # 4. Save unified PassRecord to PassStore
        net_profit = round(acc.equity - acc.initial_capital, 2)
        total_ret = round((net_profit / max(1.0, acc.initial_capital)) * 100.0, 2)
        # Collect all orders for this account (including REJECTED, CANCELLED, FILLED, ACCEPTED)
        account_orders = [
            o.model_dump(mode="json")
            for o in self.matcher.all_orders.values()
            if o.account_id == account_id
        ]
        account_orders.sort(key=lambda o: str(o.get("created_at") or o.get("order_id")))
        failed_count = sum(1 for o in account_orders if o.get("status") == "REJECTED")

        summary = PassSummary(
            pass_id=tag,
            account_id=account_id,
            tag=tag,
            created_at=acc.created_at,
            completed_at=utc_now(),
            initial_capital=acc.initial_capital,
            ending_equity=acc.equity,
            net_profit=net_profit,
            total_return_pct=total_ret,
            cash=acc.cash,
            realized_pnl=acc.realized_pnl,
            unrealized_pnl=acc.unrealized_pnl,
            total_trades=len(trades),
            total_orders=len(account_orders),
            failed_orders=failed_count,
            win_rate_pct=float(perf.get("win_rate_pct", 0.0)),
            max_drawdown_pct=float(perf.get("max_drawdown_pct", 0.0)),
            sharpe_ratio=float(perf.get("sharpe_ratio", 0.0)),
            positions_count=len(acc.positions),
        )
        rec = PassRecord(
            summary=summary,
            performance=perf,
            positions={t: p.model_dump(mode="json") for t, p in acc.positions.items()},
            trades=[t.model_dump(mode="json") for t in trades],
            orders=account_orders,
            snapshots=[s.model_dump(mode="json") for s in snapshots],
            ledger_entries=ledger_entries,
        )
        self.pass_store.save_pass(rec)

        logger.info(f"Saved simulation pass reports for {account_id} ({tag}) to {output_dir}/")
        return {
            "trades_csv": str(csv_file),
            "performance_json": str(perf_file),
            "ledger_json": str(ledger_file),
            "pass_id": tag,
        }
