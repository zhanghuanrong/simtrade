"""Order schemas, enums, and lifecycle definitions."""

import uuid
from datetime import datetime
from simtrade.utils import utc_now
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    SELL_SHORT = "SELL_SHORT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"              # Created, waiting to be sent/processed
    ACCEPTED = "ACCEPTED"            # Accepted by matching engine, in book
    PARTIALLY_FILLED = "PARTIALLY_FILLED" # Some shares filled
    FILLED = "FILLED"                # Completely filled
    CANCELLED = "CANCELLED"          # User cancelled
    REJECTED = "REJECTED"            # Rejected due to risk/margin/invalid args
    EXPIRED = "EXPIRED"              # TimeInForce expired


class TimeInForce(str, Enum):
    GTC = "GTC"  # Good 'Til Cancelled
    DAY = "DAY"  # Day order
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill


class OrderCreate(BaseModel):
    """Request payload to submit a new order."""
    ticker: str = Field(..., description="Ticker symbol e.g. AAPL")
    side: OrderSide = Field(..., description="BUY, SELL, or SELL_SHORT")
    order_type: OrderType = Field(default=OrderType.MARKET, description="Order type")
    quantity: float = Field(..., gt=0, description="Number of shares")
    limit_price: Optional[float] = Field(default=None, description="Limit price for LIMIT and STOP_LIMIT orders")
    stop_price: Optional[float] = Field(default=None, description="Stop trigger price for STOP and STOP_LIMIT orders")
    time_in_force: TimeInForce = Field(default=TimeInForce.GTC, description="Time in force")
    client_order_id: Optional[str] = Field(default=None, description="Optional client-assigned identifier")


class OrderCancel(BaseModel):
    """Request payload to cancel an active order."""
    order_id: str


class Order(BaseModel):
    """Stateful order representation."""
    order_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    account_id: str = Field(default="default_account")
    client_order_id: Optional[str] = None
    ticker: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    status: OrderStatus = OrderStatus.PENDING
    avg_fill_price: float = 0.0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    reject_reason: Optional[str] = None

    def model_post_init(self, __context):
        if self.remaining_quantity == 0.0 and self.filled_quantity == 0.0:
            self.remaining_quantity = self.quantity
