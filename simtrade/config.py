"""Configuration settings for SimTrade server, simulation engine, and margin policies."""

from typing import List, Optional
from pydantic import BaseModel, Field


class MarginConfig(BaseModel):
    """Margin and risk management configuration."""
    # Leverage multiplier (e.g. 2.0 = 50% initial margin, 4.0 = 25% initial margin)
    max_leverage: float = Field(default=2.0, ge=1.0, description="Maximum permitted leverage")
    initial_margin_rate: float = Field(default=0.50, ge=0.05, le=1.0, description="Initial margin requirement (e.g. 0.50 for 50%)")
    maintenance_margin_rate: float = Field(default=0.25, ge=0.01, le=1.0, description="Maintenance margin threshold (e.g. 0.25 for 25%)")
    
    # Short selling policies
    allow_short: bool = Field(default=True, description="Whether short selling is allowed")
    short_initial_margin_rate: float = Field(default=0.50, ge=0.05, le=1.0, description="Initial margin required for short positions")
    short_maintenance_margin_rate: float = Field(default=0.30, ge=0.01, le=1.0, description="Maintenance margin required for short positions")
    annual_borrow_fee_rate: float = Field(default=0.03, ge=0.0, description="Annual borrow fee on shorted market value")
    annual_margin_interest_rate: float = Field(default=0.06, ge=0.0, description="Annual interest charged on margin cash debit")
    
    # Liquidation behavior
    auto_liquidate_on_call: bool = Field(default=True, description="Automatically liquidate positions when equity < maintenance margin")
    liquidation_penalty_rate: float = Field(default=0.01, ge=0.0, le=0.10, description="Penalty fee charged upon forced liquidation")


class MatchingConfig(BaseModel):
    """Order matching engine configuration."""
    slippage_bps: float = Field(default=2.0, ge=0.0, description="Fixed slippage in basis points (1 bp = 0.01%)")
    max_volume_share: float = Field(default=0.10, ge=0.001, le=1.0, description="Max percentage of 1m bar volume an order can consume")
    commission_per_share: float = Field(default=0.005, ge=0.0, description="Commission fee per share in USD")
    min_commission: float = Field(default=1.0, ge=0.0, description="Minimum commission fee per trade in USD")
    price_precision: int = Field(default=4, ge=2, le=6, description="Decimal places for prices")


class SimulationConfig(BaseModel):
    """Simulation clock and market data replay configuration."""
    speed_multiplier: float = Field(default=1.0, ge=0.0, description="Playback speed (1.0 = real-time 1m/min, 60.0 = 1m/sec, 0 = max/instant)")
    tickers: List[str] = Field(default_factory=lambda: ["AAPL", "NVDA", "TSLA", "MSFT"], description="List of active tickers")
    data_file: Optional[str] = Field(default=None, description="Path to historical parquet or CSV file")
    data_dir: Optional[str] = Field(default="data", description="Directory containing historical CSV/Parquet 1m data files")
    start_time: Optional[str] = Field(default=None, description="Simulation start datetime ISO string")
    end_time: Optional[str] = Field(default=None, description="Simulation end datetime ISO string")
    generate_synthetic_if_missing: bool = Field(default=True, description="Generate synthetic 1m OHLCV data if no data files found")


class ServerConfig(BaseModel):
    """HTTP & WebSocket server configuration."""
    host: str = Field(default="0.0.0.0", description="Host address to bind")
    port: int = Field(default=6688, ge=1024, le=65535, description="Port to listen on")
    title: str = Field(default="SimTrade Paper Trading Server")
    version: str = Field(default="0.1.0")
    log_level: str = Field(default="info")
