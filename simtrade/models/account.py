"""Account, Position, and Margin state models."""

from datetime import datetime
from simtrade.utils import utc_now
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from simtrade.models.order import OrderSide


class Position(BaseModel):
    """A holding in a specific ticker (long or short)."""
    ticker: str
    quantity: float = 0.0          # Positive = Long, Negative = Short
    avg_entry_price: float = 0.0   # Average cost basis per share
    current_price: float = 0.0     # Latest mark price from market feed
    unrealized_pnl: float = 0.0    # Mark-to-market unrealized PnL
    realized_pnl: float = 0.0      # Accumulated realized PnL
    last_updated: datetime = Field(default_factory=utc_now)

    @property
    def side(self) -> OrderSide:
        if self.quantity >= 0:
            return OrderSide.BUY
        return OrderSide.SELL_SHORT

    @property
    def market_value(self) -> float:
        """Market value of position (can be positive or negative)."""
        return round(self.quantity * self.current_price, 4)

    @property
    def abs_market_value(self) -> float:
        """Absolute gross market exposure."""
        return abs(self.market_value)


class MarginMetrics(BaseModel):
    """Real-time margin utilization & risk health."""
    total_long_value: float = 0.0
    total_short_value: float = 0.0
    gross_market_value: float = 0.0
    initial_margin_requirement: float = 0.0
    maintenance_margin_requirement: float = 0.0
    margin_excess: float = 0.0              # Equity - Maintenance Margin
    buying_power: float = 0.0               # Maximum additional purchase capacity
    leverage: float = 1.0                   # Gross Market Value / Equity
    is_margin_call: bool = False            # True when Equity < Maintenance Margin
    margin_call_amount: float = 0.0         # Deficit required to cure call


class MarginCallEvent(BaseModel):
    """Event fired when an account enters margin call or liquidation."""
    account_id: str
    timestamp: datetime
    equity: float
    maintenance_margin: float
    deficit: float
    action_taken: str  # "CALL_ISSUED" | "LIQUIDATION_TRIGGERED" | "RESOLVED"
    liquidated_orders: List[str] = Field(default_factory=list)


class Account(BaseModel):
    """Comprehensive trader account ledger state."""
    account_id: str = "trader_1"
    cash: float = 100_000.0                 # Liquid cash balance (can be negative on margin loan)
    frozen_cash: float = 0.0                # Cash reserved for pending buy limit orders
    borrowed_margin: float = 0.0            # Margin loan from broker (when cash < 0)
    realized_pnl: float = 0.0               # Cumulative realized profit/loss
    unrealized_pnl: float = 0.0             # Mark-to-market unrealized profit/loss
    equity: float = 100_000.0               # Cash + Long MV - Short MV
    initial_capital: float = 100_000.0      # Starting capital baseline
    positions: Dict[str, Position] = Field(default_factory=dict)
    margin: MarginMetrics = Field(default_factory=MarginMetrics)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
