"""Persistent store and registry for finished simulation passes."""

import csv
from datetime import datetime
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from simtrade.utils import utc_now

logger = logging.getLogger(__name__)


class PassSummary(BaseModel):
    """Compact summary of a simulation pass for catalog and dropdown selection."""
    pass_id: str
    account_id: str
    tag: Optional[str] = None
    wall_created_at: datetime = Field(default_factory=utc_now)
    wall_completed_at: Optional[datetime] = None
    sim_start_time: Optional[datetime] = None
    sim_end_time: Optional[datetime] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    initial_capital: float = 100_000.0
    ending_equity: float = 100_000.0
    net_profit: float = 0.0
    total_return_pct: float = 0.0
    cash: float = 100_000.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_trades: int = 0
    total_orders: int = 0
    failed_orders: int = 0
    win_rate_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    positions_count: int = 0

    def model_post_init(self, __context):
        if self.created_at is None:
            self.created_at = self.wall_created_at
        else:
            self.wall_created_at = self.created_at
        if self.completed_at is None:
            self.completed_at = self.wall_completed_at
        else:
            self.wall_completed_at = self.completed_at

def enrich_trades_with_pnl(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure all SELL/COVER trades in the list have cost_basis, realized_pnl, and realized_pnl_pct."""
    positions: Dict[str, Dict[str, float]] = {}  # ticker -> {'qty': float, 'avg_price': float}

    for t in trades:
        ticker = t.get("ticker", "")
        side = (t.get("side") or "").upper()
        qty = float(t.get("quantity") or 0.0)
        price = float(t.get("price") or 0.0)

        pos = positions.setdefault(ticker, {"qty": 0.0, "avg_price": 0.0})

        if side == "BUY":
            if pos["qty"] < 0:
                closed = min(abs(pos["qty"]), qty)
                cost_basis = pos["avg_price"]
                pnl = (cost_basis - price) * closed
                pnl_pct = ((cost_basis - price) / cost_basis * 100.0) if cost_basis > 0 else 0.0
                if t.get("realized_pnl") is None:
                    t["cost_basis"] = round(cost_basis, 4)
                    t["realized_pnl"] = round(pnl, 4)
                    t["realized_pnl_pct"] = round(pnl_pct, 2)
                pos["qty"] += closed
                excess = qty - closed
                if excess > 0:
                    pos["qty"] = excess
                    pos["avg_price"] = price
            else:
                tot_cost = pos["qty"] * pos["avg_price"] + qty * price
                new_qty = pos["qty"] + qty
                pos["avg_price"] = tot_cost / new_qty if new_qty > 0 else 0.0
                pos["qty"] = new_qty
        elif side in ("SELL", "SELL_SHORT"):
            if pos["qty"] > 0:
                closed = min(pos["qty"], qty)
                cost_basis = pos["avg_price"]
                pnl = (price - cost_basis) * closed
                pnl_pct = ((price - cost_basis) / cost_basis * 100.0) if cost_basis > 0 else 0.0
                if t.get("realized_pnl") is None:
                    t["cost_basis"] = round(cost_basis, 4)
                    t["realized_pnl"] = round(pnl, 4)
                    t["realized_pnl_pct"] = round(pnl_pct, 2)
                pos["qty"] -= closed
                if pos["qty"] <= 1e-6:
                    pos["qty"] = 0.0
                    pos["avg_price"] = 0.0
                excess = qty - closed
                if excess > 0:
                    pos["qty"] = -excess
                    pos["avg_price"] = price
            else:
                tot_short_val = abs(pos["qty"]) * pos["avg_price"] + qty * price
                new_short_qty = abs(pos["qty"]) + qty
                pos["avg_price"] = round(tot_short_val / new_short_qty, 4) if new_short_qty > 0 else 0.0
                pos["qty"] = -new_short_qty
    return trades


class PassRecord(BaseModel):
    """Complete historical record of a finished simulation pass."""
    summary: PassSummary
    performance: Dict[str, Any] = Field(default_factory=dict)
    positions: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    trades: List[Dict[str, Any]] = Field(default_factory=list)
    orders: List[Dict[str, Any]] = Field(default_factory=list)
    snapshots: List[Dict[str, Any]] = Field(default_factory=list)
    ledger_entries: List[Dict[str, Any]] = Field(default_factory=list)

    def model_post_init(self, __context):
        if self.trades:
            self.trades = enrich_trades_with_pnl(self.trades)

    def get_orders(self) -> List[Dict[str, Any]]:
        """Return stored orders, or reconstruct from ledger entries if orders was empty."""
        if self.orders:
            return self.orders
        reconstructed: Dict[str, Dict[str, Any]] = {}
        for e in self.ledger_entries:
            etype = e.get("event_type")
            details = e.get("details") or {}
            oid = details.get("order_id")
            if not oid:
                continue
            if oid not in reconstructed:
                reconstructed[oid] = {
                    "order_id": oid,
                    "account_id": e.get("account_id"),
                    "ticker": details.get("ticker", ""),
                    "side": details.get("side", "BUY"),
                    "order_type": details.get("type", "MARKET"),
                    "quantity": float(details.get("quantity", 0.0) or 0.0),
                    "filled_quantity": 0.0,
                    "limit_price": details.get("limit_price"),
                    "status": "ACCEPTED",
                    "created_at": e.get("sim_time"),
                    "reject_reason": None,
                }
            item = reconstructed[oid]
            if etype == "ORDER_REJECTED":
                item["status"] = "REJECTED"
                item["reject_reason"] = details.get("reason")
            elif etype == "ORDER_CANCELLED":
                if item["status"] != "FILLED":
                    item["status"] = "CANCELLED"
            elif etype in ("ORDER_FILLED", "ORDER_PARTIALLY_FILLED"):
                item["status"] = "FILLED" if etype == "ORDER_FILLED" else "PARTIALLY_FILLED"
                item["filled_quantity"] = item.get("filled_quantity", 0.0) + float(details.get("quantity", 0.0) or 0.0)
                if not item["ticker"]:
                    item["ticker"] = details.get("ticker", "")
        return list(reconstructed.values())


class PassStore:
    """Manages finished simulation pass records in memory and on disk."""

    def __init__(self, storage_dir: str = "reports"):
        self.storage_dir = Path(storage_dir)
        self.passes_dir = self.storage_dir / "passes"
        self._passes: Dict[str, PassRecord] = {}
        self.scan_and_load()

    def save_pass(self, record: PassRecord) -> PassRecord:
        """Register and persist a finished pass record."""
        self._passes[record.summary.pass_id] = record
        self._persist_to_disk(record)
        logger.info(f"Registered finished pass '{record.summary.pass_id}' (tag: {record.summary.tag})")
        return record

    def get_pass(self, pass_id: str) -> Optional[PassRecord]:
        """Fetch complete pass record by pass_id."""
        if pass_id in self._passes:
            return self._passes[pass_id]

        # Attempt to load from disk
        file_path = self.passes_dir / f"{pass_id}.json"
        if file_path.exists():
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
                rec = PassRecord(**data)
                self._passes[pass_id] = rec
                return rec
            except Exception as e:
                logger.error(f"Error loading pass from {file_path}: {e}")
        return None

    def list_passes(self) -> List[PassSummary]:
        """List summaries of all finished passes, ordered newest to oldest."""
        summaries = [p.summary for p in self._passes.values()]
        summaries.sort(key=lambda s: s.completed_at or s.created_at, reverse=True)
        return summaries

    def delete_pass(self, pass_id: str) -> bool:
        """Delete a single pass from memory and disk."""
        removed = self._passes.pop(pass_id, None) is not None
        file_path = self.passes_dir / f"{pass_id}.json"
        if file_path.exists():
            file_path.unlink(missing_ok=True)
            removed = True
        return removed

    def clear(self) -> int:
        """Clear all stored passes from memory and delete pass files from disk."""
        count = len(self._passes)
        self._passes.clear()
        if self.passes_dir.exists():
            for f in self.passes_dir.glob("*.json"):
                try:
                    f.unlink(missing_ok=True)
                except Exception as e:
                    logger.error(f"Error deleting pass file {f}: {e}")
        if self.storage_dir.exists():
            for pat in ["performance_*.json", "trades_*.csv", "ledger_*.json", "e2e_trades_*.csv"]:
                for f in self.storage_dir.glob(pat):
                    try:
                        f.unlink(missing_ok=True)
                    except Exception as e:
                        logger.error(f"Error deleting legacy report {f}: {e}")
        logger.info(f"Cleared all {count} simulation passes from PassStore and reports/")
        return count

    def export_trades_csv(self, pass_id: str) -> str:
        """Export trades of a pass to CSV format."""
        rec = self.get_pass(pass_id)
        if not rec:
            return ""

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "trade_id", "account_id", "order_id", "ticker", "side",
            "price", "quantity", "notional", "commission", "timestamp",
            "cost_basis", "realized_pnl", "realized_pnl_pct"
        ])
        for t in rec.trades:
            writer.writerow([
                t.get("trade_id", ""),
                t.get("account_id", rec.summary.account_id),
                t.get("order_id", ""),
                t.get("ticker", ""),
                t.get("side", ""),
                t.get("price", 0.0),
                t.get("quantity", 0.0),
                t.get("notional", 0.0),
                t.get("commission", 0.0),
                t.get("timestamp", ""),
                t.get("cost_basis", "") if t.get("cost_basis") is not None else "",
                t.get("realized_pnl", "") if t.get("realized_pnl") is not None else "",
                t.get("realized_pnl_pct", "") if t.get("realized_pnl_pct") is not None else "",
            ])
        return output.getvalue()

    def _persist_to_disk(self, record: PassRecord):
        """Write pass record to disk."""
        try:
            self.passes_dir.mkdir(parents=True, exist_ok=True)
            out_file = self.passes_dir / f"{record.summary.pass_id}.json"
            with open(out_file, "w") as f:
                f.write(record.model_dump_json(indent=2))
        except Exception as e:
            logger.error(f"Failed to persist pass {record.summary.pass_id} to disk: {e}")

    def scan_and_load(self):
        """Scan passes directory and legacy reports directory to discover and index past runs."""
        if not self.passes_dir.exists():
            self._scan_legacy_reports()
            return

        for path in self.passes_dir.glob("*.json"):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                rec = PassRecord(**data)
                self._passes[rec.summary.pass_id] = rec
            except Exception as e:
                logger.debug(f"Could not load pass file {path}: {e}")

        # Also check for legacy reports that don't have a passes/ entry yet
        self._scan_legacy_reports()

    def _scan_legacy_reports(self):
        """Auto-discover any existing performance_{tag}.json / trades_{tag}.csv in storage_dir."""
        if not self.storage_dir.exists():
            return

        for perf_path in self.storage_dir.glob("performance_*.json"):
            tag = perf_path.stem.replace("performance_", "")
            if tag in self._passes or f"pass_{tag}" in self._passes:
                continue

            try:
                with open(perf_path, "r") as f:
                    perf = json.load(f)

                # Check if trades file exists
                trades_list = []
                trades_csv = self.storage_dir / f"trades_{tag}.csv"
                if trades_csv.exists():
                    with open(trades_csv, "r") as tf:
                        reader = csv.DictReader(tf)
                        for row in reader:
                            trades_list.append({
                                "trade_id": row.get("trade_id", ""),
                                "account_id": row.get("account_id", tag),
                                "order_id": row.get("order_id", ""),
                                "ticker": row.get("ticker", ""),
                                "side": row.get("side", ""),
                                "price": float(row.get("price", 0.0)),
                                "quantity": float(row.get("quantity", 0.0)),
                                "notional": float(row.get("notional", 0.0)),
                                "commission": float(row.get("commission", 0.0)),
                                "timestamp": row.get("timestamp", ""),
                            })

                # Check if ledger exists
                ledger_list = []
                ledger_json = self.storage_dir / f"ledger_{tag}.json"
                if ledger_json.exists():
                    with open(ledger_json, "r") as lf:
                        ledger_list = json.load(lf)

                init_cap = float(perf.get("initial_capital", 100_000.0))
                end_eq = float(perf.get("final_equity", init_cap))
                net_prof = round(end_eq - init_cap, 2)
                ret_pct = float(perf.get("total_return_pct", round((net_prof / init_cap) * 100, 2)))

                summary = PassSummary(
                    pass_id=tag,
                    account_id=tag,
                    tag=tag,
                    created_at=utc_now(),
                    completed_at=utc_now(),
                    initial_capital=init_cap,
                    ending_equity=end_eq,
                    net_profit=net_prof,
                    total_return_pct=ret_pct,
                    cash=end_eq,
                    realized_pnl=net_prof,
                    unrealized_pnl=0.0,
                    total_trades=len(trades_list) or int(perf.get("total_trades", 0)),
                    win_rate_pct=float(perf.get("win_rate_pct", 0.0)),
                    max_drawdown_pct=float(perf.get("max_drawdown_pct", 0.0)),
                    sharpe_ratio=float(perf.get("sharpe_ratio", 0.0)),
                    positions_count=0,
                )

                rec = PassRecord(
                    summary=summary,
                    performance=perf,
                    positions={},
                    trades=trades_list,
                    snapshots=[],
                    ledger_entries=ledger_list,
                )
                self._passes[tag] = rec
                logger.info(f"Auto-discovered and indexed legacy simulation pass '{tag}' from reports/")
            except Exception as e:
                logger.debug(f"Could not convert legacy report {perf_path}: {e}")
