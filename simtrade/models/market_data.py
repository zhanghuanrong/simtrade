"""Market data models for bars, ticks, and order book snapshots."""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field, model_validator


class Bar(BaseModel):
    """Aggregated OHLCV bar (typically 1-minute interval, extensible to others)."""
    ticker: str = Field(..., description="Stock ticker symbol, e.g. AAPL")
    sim_timestamp: datetime = Field(..., description="Simulated timestamp of the bar (UTC)")
    open: float = Field(..., gt=0, description="Opening price")
    high: float = Field(..., gt=0, description="Highest price")
    low: float = Field(..., gt=0, description="Lowest price")
    close: float = Field(..., gt=0, description="Closing price")
    volume: float = Field(default=0.0, ge=0, description="Trading volume during the bar")
    vwap: Optional[float] = Field(default=None, description="Volume Weighted Average Price")
    timestamp: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def populate_sim_time(cls, data: Any):
        if isinstance(data, dict):
            if "sim_timestamp" not in data and "timestamp" in data:
                data["sim_timestamp"] = data["timestamp"]
            elif "timestamp" not in data and "sim_timestamp" in data:
                data["timestamp"] = data["sim_timestamp"]
        return data

    def model_post_init(self, __context):
        if self.timestamp is None:
            self.timestamp = self.sim_timestamp
        else:
            self.sim_timestamp = self.timestamp

    @property
    def midpoint(self) -> float:
        return round((self.high + self.low) / 2.0, 4)


class Tick(BaseModel):
    """Individual tick/trade event."""
    ticker: str
    sim_timestamp: datetime
    price: float = Field(..., gt=0)
    size: float = Field(..., gt=0)
    bid: Optional[float] = None
    ask: Optional[float] = None
    timestamp: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def populate_sim_time(cls, data: Any):
        if isinstance(data, dict):
            if "sim_timestamp" not in data and "timestamp" in data:
                data["sim_timestamp"] = data["timestamp"]
            elif "timestamp" not in data and "sim_timestamp" in data:
                data["timestamp"] = data["sim_timestamp"]
        return data

    def model_post_init(self, __context):
        if self.timestamp is None:
            self.timestamp = self.sim_timestamp
        else:
            self.sim_timestamp = self.timestamp


class OrderBookSnapshot(BaseModel):
    """Simulated Top-of-Book / L1 quote snapshot."""
    ticker: str
    sim_timestamp: datetime
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
    timestamp: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def populate_sim_time(cls, data: Any):
        if isinstance(data, dict):
            if "sim_timestamp" not in data and "timestamp" in data:
                data["sim_timestamp"] = data["timestamp"]
            elif "timestamp" not in data and "sim_timestamp" in data:
                data["timestamp"] = data["sim_timestamp"]
        return data

    def model_post_init(self, __context):
        if self.timestamp is None:
            self.timestamp = self.sim_timestamp
        else:
            self.sim_timestamp = self.timestamp
