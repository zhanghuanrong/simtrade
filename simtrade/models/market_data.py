"""Market data models for bars, ticks, and order book snapshots."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class Bar(BaseModel):
    """Aggregated OHLCV bar (typically 1-minute interval, extensible to others)."""
    ticker: str = Field(..., description="Stock ticker symbol, e.g. AAPL")
    timestamp: datetime = Field(..., description="Start timestamp of the bar (UTC)")
    open: float = Field(..., gt=0, description="Opening price")
    high: float = Field(..., gt=0, description="Highest price")
    low: float = Field(..., gt=0, description="Lowest price")
    close: float = Field(..., gt=0, description="Closing price")
    volume: float = Field(default=0.0, ge=0, description="Trading volume during the bar")
    vwap: Optional[float] = Field(default=None, description="Volume Weighted Average Price")

    @property
    def midpoint(self) -> float:
        return round((self.high + self.low) / 2.0, 4)


class Tick(BaseModel):
    """Individual tick/trade event."""
    ticker: str
    timestamp: datetime
    price: float = Field(..., gt=0)
    size: float = Field(..., gt=0)
    bid: Optional[float] = None
    ask: Optional[float] = None


class OrderBookSnapshot(BaseModel):
    """Simulated Top-of-Book / L1 quote snapshot."""
    ticker: str
    timestamp: datetime
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
