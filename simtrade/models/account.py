"""Account, Position, and Margin state models."""

from datetime import datetime
from simtrade.utils import utc_now
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, PrivateAttr
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
    tag: Optional[str] = None               # Optional simulation pass name / strategy tag
    cash: float = 100_000.0                 # Liquid cash balance (can be negative on margin loan)
    frozen_cash: float = 0.0                # Cash reserved for pending buy limit orders
    borrowed_margin: float = 0.0            # Margin loan from broker (when cash < 0)
    realized_pnl: float = 0.0               # Cumulative realized profit/loss
    unrealized_pnl: float = 0.0             # Mark-to-market unrealized profit/loss
    equity: float = 100_000.0               # Cash + Long MV - Short MV
    initial_capital: float = 100_000.0      # Starting capital baseline
    positions: Dict[str, Position] = Field(default_factory=dict)
    margin: MarginMetrics = Field(default_factory=MarginMetrics)
    custom_margin_config: Optional[Dict[str, float]] = None
    _internal_margin_config: Optional[Dict[str, float]] = PrivateAttr(default=None)
    wall_created_at: datetime = Field(default_factory=utc_now)
    sim_updated_at: datetime = Field(default_factory=utc_now)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def internal_margin_config(self) -> Dict[str, float]:
        if self._internal_margin_config:
            return self._internal_margin_config
        return self.custom_margin_config or {}

    def model_post_init(self, __context):
        if self.created_at is None:
            self.created_at = self.wall_created_at
        else:
            self.wall_created_at = self.created_at
        if self.updated_at is None:
            self.updated_at = self.sim_updated_at
        else:
            self.sim_updated_at = self.updated_at


class AccountSetupRequest(BaseModel):
    """Negotiate / configure initial account state for a simulation run pass."""
    account_id: str = Field(..., description="Unique name/tag for this account / simulation pass")
    tag: Optional[str] = Field(default=None, description="Descriptive label, e.g. 'momentum_v1_run'")
    initial_total_equity: Optional[float] = Field(default=100_000.0, description="Starting total account equity (Cash + Position Value)")
    initial_cash: Optional[float] = Field(default=None, description="Starting liquid cash balance (if omitted, computed as initial_total_equity - positions_market_value)")
    initial_positions: Optional[Dict[str, float]] = Field(default=None, description="Starting positions e.g. {'AAPL': 100, 'TSLA': -20}")
    initial_entry_prices: Optional[Dict[str, float]] = Field(default=None, description="Deprecated / ignored: mark prices are used automatically")
    leverage: Optional[float] = Field(default=None, ge=1.0, description="Max leverage multiplier (e.g. 4.0 for 4x)")
    initial_margin_rate: Optional[float] = Field(default=None, ge=0.05, le=1.0, description="Initial margin rate (e.g. 0.25)")
    maintenance_margin_rate: Optional[float] = Field(default=None, ge=0.01, le=1.0, description="Maintenance margin rate (e.g. 0.15)")
    market_order_slippage_buffer: Optional[float] = Field(default=None, ge=0.0, description="Buffer rate applied to estimated price of market orders to prevent margin overflow (e.g. 0.05)")


class AccountSummary(BaseModel):
    """High-level summary of a simulation account / pass."""
    account_id: str
    tag: Optional[str] = None
    initial_capital: float
    equity: float
    cash: float
    realized_pnl: float
    unrealized_pnl: float
    buying_power: float
    margin_used: float
    positions_count: int
    wall_created_at: datetime = Field(default_factory=utc_now)
    created_at: Optional[datetime] = None

    def model_post_init(self, __context):
        if self.created_at is None:
            self.created_at = self.wall_created_at
        else:
            self.wall_created_at = self.created_at

