"""Trade execution record models."""

import uuid
from datetime import datetime
from typing import Optional
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
    sim_timestamp: datetime = Field(default_factory=utc_now)
    wall_timestamp: datetime = Field(default_factory=utc_now)
    timestamp: Optional[datetime] = None

    def model_post_init(self, __context):
        if self.timestamp is None:
            self.timestamp = self.sim_timestamp
        else:
            self.sim_timestamp = self.timestamp

    @property
    def notional(self) -> float:
        return round(self.price * self.quantity, 4)
