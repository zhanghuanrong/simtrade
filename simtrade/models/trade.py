"""Trade execution record models."""

import uuid
from datetime import datetime
from simtrade.utils import utc_now
from pydantic import BaseModel, Field
from simtrade.models.order import OrderSide


class Trade(BaseModel):
    """Represents a single fulfilled execution of an order."""
    trade_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str
    account_id: str
    ticker: str
    side: OrderSide
    price: float
    quantity: float
    commission: float = 0.0
    slippage: float = 0.0
    timestamp: datetime = Field(default_factory=utc_now)

    @property
    def notional(self) -> float:
        return round(self.price * self.quantity, 4)
