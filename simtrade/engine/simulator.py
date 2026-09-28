"""Central simulation engine orchestrating clock, market data feeds, matching, and risk."""

import asyncio
from datetime import datetime
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
from simtrade.models.order import Order, OrderCreate, OrderStatus
from simtrade.models.trade import Trade
from simtrade.reporting.ledger import EventLedger

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
        self.clock = SimClock(
            start_time=self.feeder.start_time,
            speed_multiplier=self.sim_config.speed_multiplier,
            timeline=self.feeder.timeline,
        )
        self.matcher = MatchingEngine(config=self.matching_config)
        self.margin_engine = MarginEngine(config=self.margin_config)
        self.account_mgr = AccountManager(margin_engine=self.margin_engine)
        self.ledger = EventLedger()

        # Current state
        self.latest_bars: Dict[str, Bar] = {}
        self.all_trades: List[Trade] = []
        self._loop_task: Optional[asyncio.Task] = None
        self._broadcast_callbacks: List[Callable[[str, Any], Coroutine[Any, Any, None]]] = []

    def register_broadcast_callback(self, callback: Callable[[str, Any], Coroutine[Any, Any, None]]):
        """Register an async callback for broadcasting events to WebSocket clients."""
        self._broadcast_callbacks.append(callback)

    async def _broadcast(self, event_type: str, data: Any):
        for cb in self._broadcast_callbacks:
            try:
                await cb(event_type, data)
            except Exception as e:
                logger.error(f"Error in broadcast callback {event_type}: {e}")

    def submit_order(self, order_create: OrderCreate, account_id: str = "trader_1") -> Order:
        """Submit a new order from a client."""
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
        )

        # Estimate execution price for margin check
        latest_bar = self.latest_bars.get(order.ticker)
        est_price = order.limit_price or (latest_bar.close if latest_bar else 100.0)

        # Validate with risk & margin engine
        valid, reject_reason = self.account_mgr.reserve_for_order(account_id, order, est_price)
        if not valid:
            order.status = OrderStatus.REJECTED
            order.reject_reason = reject_reason
            self.matcher.all_orders[order.order_id] = order
            self.ledger.record("ORDER_REJECTED", account_id, self.clock.current_time, {
                "order_id": order.order_id,
                "ticker": order.ticker,
                "reason": reject_reason,
            })
            return order

        # Register in matching engine
        self.matcher.add_order(order)
        self.ledger.record("ORDER_ACCEPTED", account_id, self.clock.current_time, {
            "order_id": order.order_id,
            "ticker": order.ticker,
            "side": order.side.value,
            "quantity": order.quantity,
            "type": order.order_type.value,
            "limit_price": order.limit_price,
        })
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

    async def step(self) -> Dict[str, Any]:
        """Execute one simulation step (e.g. 1 minute)."""
        sim_time = self.clock.step()

        # 1. Fetch bars for all tickers
        bars = self.feeder.get_bars_for_time(sim_time)
        self.latest_bars.update(bars)

        # 2. Update mark prices across all accounts
        self.account_mgr.update_mark_prices(bars)

        # 3. Match pending orders against incoming bars
        new_trades: List[Trade] = []
        for ticker, bar in bars.items():
            matches = self.matcher.match_bar(bar)
            for order, trade in matches:
                self.all_trades.append(trade)
                new_trades.append(trade)
                # Update account balances and positions
                self.account_mgr.process_trade(trade)
                self.account_mgr.release_reserved_for_order(trade.account_id, order)
                
                # Log execution
                self.ledger.record("ORDER_FILLED" if order.status == OrderStatus.FILLED else "ORDER_PARTIALLY_FILLED",
                                   trade.account_id, sim_time, {
                    "order_id": order.order_id,
                    "trade_id": trade.trade_id,
                    "ticker": trade.ticker,
                    "price": trade.price,
                    "quantity": trade.quantity,
                    "commission": trade.commission,
                })
                # Broadcast order and trade update
                await self._broadcast("ORDER_UPDATE", order.model_dump(mode="json"))
                await self._broadcast("TRADE_EXECUTION", trade.model_dump(mode="json"))

        # 4. Check margin calls and execute auto-liquidation if needed
        for account in self.account_mgr.accounts.values():
            mark_prices = {t: b.close for t, b in self.latest_bars.items()}
            liq_orders = self.margin_engine.check_and_generate_liquidations(account, mark_prices)
            for liq_order in liq_orders:
                self.matcher.add_order(liq_order)
                self.ledger.record("MARGIN_LIQUIDATION_SUBMITTED", account.account_id, sim_time, {
                    "order_id": liq_order.order_id,
                    "ticker": liq_order.ticker,
                    "quantity": liq_order.quantity,
                })
                await self._broadcast("MARGIN_CALL", {
                    "account_id": account.account_id,
                    "timestamp": sim_time.isoformat(),
                    "action": "AUTO_LIQUIDATION",
                    "ticker": liq_order.ticker,
                    "quantity": liq_order.quantity,
                })

            # 5. Accrue financing & borrowing fees
            fees = self.margin_engine.accrue_financing_fees(account, minutes_elapsed=1)
            if fees > 0:
                self.ledger.record("FINANCING_FEES_ACCRUED", account.account_id, sim_time, {"fees": fees})

            # 6. Record snapshot for performance analytics
            self.ledger.record_snapshot(
                account_id=account.account_id,
                sim_time=sim_time,
                cash=account.cash,
                equity=account.equity,
                realized_pnl=account.realized_pnl,
                unrealized_pnl=account.unrealized_pnl,
                gross_market_value=account.margin.gross_market_value,
                leverage=account.margin.leverage,
            )

        # 7. Broadcast market bars and updated account info
        bars_payload = {t: b.model_dump(mode="json") for t, b in bars.items()}
        await self._broadcast("MARKET_BARS", {
            "timestamp": sim_time.isoformat(),
            "bars": bars_payload,
        })

        default_acc = self.account_mgr.get_or_create_account(self.account_mgr.default_account_id)
        await self._broadcast("ACCOUNT_UPDATE", default_acc.model_dump(mode="json"))

        return {
            "timestamp": sim_time.isoformat(),
            "bars": bars_payload,
            "trades_count": len(new_trades),
            "equity": default_acc.equity,
        }

    async def step_until(self, target_time: datetime, account_id: str = "trader_1") -> Dict[str, Any]:
        """
        Advance simulation from current T_anchor to target_time.
        - If target_time <= current_time: ignored, returns current state.
        - Sequentially processes all intermediate bars up to target_time.
        - If target_time falls into a non-trading gap, advances to target_time and returns empty bars with success status.
        - If margin liquidation is triggered, halts immediately at the liquidation timestamp, notifies client, and returns updated timestamp.
        """
        from simtrade.engine.feeder import normalize_ts
        target_ts = normalize_ts(target_time)
        current_ts = normalize_ts(self.clock.current_time)

        # 1. Pure client request check: ignore if target is in the past or now
        if target_ts <= current_ts:
            return {
                "status": "ignored",
                "reason": "target_time is less than or equal to current T_anchor",
                "current_time": self.clock.current_time.isoformat(),
                "bars_processed": 0,
                "trades": [],
                "bars": {},
            }

        # 2. Find timeline bars strictly between current_ts and target_ts
        bars_to_step: List[datetime] = []
        if self.feeder.timeline:
            for t in self.feeder.timeline:
                norm_t = normalize_ts(t)
                if current_ts < norm_t <= target_ts:
                    bars_to_step.append(t)
                elif norm_t > target_ts:
                    break

        all_new_trades: List[Dict[str, Any]] = []
        total_bars_processed = 0
        last_bars_payload: Dict[str, Any] = {}

        # 3. Process each historical bar in range
        for _ in bars_to_step:
            step_result = await self.step()
            total_bars_processed += 1
            last_bars_payload = step_result.get("bars", {})

            # Collect trades
            trade_cnt = step_result.get("trades_count", 0)
            if trade_cnt > 0:
                for t in self.all_trades[-trade_cnt:]:
                    all_new_trades.append(t.model_dump(mode="json"))

            # Check if margin liquidation occurred during this bar
            acc = self.account_mgr.get_or_create_account(account_id)
            if acc.margin.is_margin_call:
                liq_info = {
                    "status": "LIQUIDATION_TRIGGERED",
                    "message": f"Account {account_id} equity (${acc.equity:.2f}) dropped below maintenance margin (${acc.margin.maintenance_margin_requirement:.2f})",
                    "current_time": self.clock.current_time.isoformat(),
                    "account_id": account_id,
                    "deficit": acc.margin.margin_call_amount,
                    "bars_processed": total_bars_processed,
                    "trades": all_new_trades,
                    "bars": last_bars_payload,
                }
                logger.warning(f"step_until halted at {self.clock.current_time} due to liquidation on account {account_id}")
                return liq_info

        # 4. Handle non-trading gap (if target_ts is beyond the last processed bar)
        if normalize_ts(self.clock.current_time) < target_ts:
            self.clock.current_time = target_ts
            if self.feeder.timeline:
                for idx, t in enumerate(self.feeder.timeline):
                    if normalize_ts(t) > target_ts:
                        self.clock.cursor = idx
                        break
                else:
                    self.clock.cursor = len(self.feeder.timeline)

            last_bars_payload = {}
            await self._broadcast("MARKET_BARS", {
                "timestamp": self.clock.current_time.isoformat(),
                "bars": {},
                "is_gap": True,
            })
            default_acc = self.account_mgr.get_or_create_account(account_id)
            await self._broadcast("ACCOUNT_UPDATE", default_acc.model_dump(mode="json"))

        return {
            "status": "TARGET_REACHED",
            "current_time": self.clock.current_time.isoformat(),
            "bars_processed": total_bars_processed,
            "trades": all_new_trades,
            "bars": last_bars_payload,
        }

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

    def start(self):
        """Start or resume the simulation loop."""
        self.clock.is_running = True
        self.clock.resume()
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._run_loop())
        logger.info("Simulator started")

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
            self.clock.cursor = 0
            if start_time:
                # Find index closest to start_time
                for idx, t in enumerate(self.feeder.timeline):
                    if t >= start_time:
                        self.clock.cursor = idx
                        break
            self.clock.current_time = self.feeder.timeline[self.clock.cursor]
        elif start_time:
            self.clock.current_time = start_time

        self.clock.step_count = 0
        self.matcher.active_orders.clear()
        logger.info(f"Simulator reset to time {self.clock.current_time}")

    def get_metadata(self) -> Dict[str, Any]:
        """Get simulation metadata including time range, total bars, and available tickers."""
        total_bars = len(self.feeder.timeline)
        is_finished = (self.clock.cursor >= total_bars) if total_bars > 0 else False
        progress_pct = round((self.clock.cursor / max(1, total_bars)) * 100, 2) if total_bars > 0 else 0.0

        return {
            "start_time": self.feeder.timeline[0].isoformat() if self.feeder.timeline else None,
            "end_time": self.feeder.timeline[-1].isoformat() if self.feeder.timeline else None,
            "current_time": self.clock.current_time.isoformat(),
            "total_bars": total_bars,
            "current_step": self.clock.step_count,
            "cursor": self.clock.cursor,
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

        logger.info(f"Saved simulation pass reports for {account_id} ({tag}) to {output_dir}/")
        return {
            "trades_csv": str(csv_file),
            "performance_json": str(perf_file),
            "ledger_json": str(ledger_file),
        }
