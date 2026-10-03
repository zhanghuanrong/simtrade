"""Immutable event ledger for tracing, audit trail, and historical snapshots."""

from datetime import datetime
from simtrade.utils import utc_now
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class LedgerEntry(BaseModel):
    """A recorded event in the simulation ledger."""
    entry_id: int
    event_type: str  # ORDER_SUBMITTED, ORDER_FILLED, MARGIN_CALL, SNAPSHOT, etc.
    sim_time: datetime
    wall_time: datetime = Field(default_factory=utc_now)
    account_id: str
    details: Dict[str, Any] = Field(default_factory=dict)


class AccountSnapshot(BaseModel):
    """Point-in-time portfolio valuation for equity curve generation."""
    sim_time: datetime
    wall_time: datetime = Field(default_factory=utc_now)
    cash: float
    reserved_cash: float = 0.0
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    gross_market_value: float
    leverage: float
    positions: Dict[str, float] = Field(default_factory=dict)  # ticker -> market_value


class EventLedger:
    """Stores all transaction histories, snapshots, and audit events."""

    def __init__(self):
        self.entries: List[LedgerEntry] = []
        self.snapshots: Dict[str, List[AccountSnapshot]] = {}  # account_id -> snapshots
        self._seq: int = 0

    def record(self, event_type: str, account_id: str, sim_time: datetime, details: Optional[Dict[str, Any]] = None):
        self._seq += 1
        entry = LedgerEntry(
            entry_id=self._seq,
            event_type=event_type,
            sim_time=sim_time,
            account_id=account_id,
            details=details or {},
        )
        self.entries.append(entry)

    def record_snapshot(self, account_id: str, sim_time: datetime, cash: float, equity: float,
                        realized_pnl: float, unrealized_pnl: float, gross_market_value: float, leverage: float,
                        positions: Optional[Dict[str, float]] = None, reserved_cash: float = 0.0):
        if account_id not in self.snapshots:
            self.snapshots[account_id] = []
        
        snap = AccountSnapshot(
            sim_time=sim_time,
            cash=cash,
            reserved_cash=reserved_cash,
            equity=equity,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            gross_market_value=gross_market_value,
            leverage=leverage,
            positions=positions or {},
        )
        self.snapshots[account_id].append(snap)

    def get_entries(self, account_id: Optional[str] = None, event_type: Optional[str] = None, limit: int = 100) -> List[LedgerEntry]:
        results = self.entries
        if account_id:
            results = [e for e in results if e.account_id == account_id]
        if event_type:
            results = [e for e in results if e.event_type == event_type]
        return results[-limit:]

    def get_snapshots(self, account_id: str) -> List[AccountSnapshot]:
        return self.snapshots.get(account_id, [])
