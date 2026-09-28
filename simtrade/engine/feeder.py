"""Market data feeder supporting CSV/Parquet playback and synthetic 1m OHLCV generation."""

from datetime import datetime, timedelta
import math
import os
from pathlib import Path
import random
from typing import Dict, Iterator, List, Optional
import pandas as pd
import logging

from simtrade.models.market_data import Bar

logger = logging.getLogger(__name__)


class SyntheticDataGenerator:
    """Generates realistic synthetic 1-minute OHLCV data using Geometric Brownian Motion."""

    DEFAULT_BASE_PRICES = {
        "AAPL": 220.0,
        "NVDA": 130.0,
        "TSLA": 250.0,
        "MSFT": 420.0,
        "AMZN": 185.0,
        "GOOGL": 175.0,
    }

    def __init__(self, tickers: List[str], seed: int = 42):
        self.tickers = tickers
        random.seed(seed)
        self.prices = {
            t: self.DEFAULT_BASE_PRICES.get(t, round(random.uniform(50.0, 300.0), 2))
            for t in tickers
        }

    def generate_bar(self, ticker: str, timestamp: datetime) -> Bar:
        current_price = self.prices[ticker]
        # Intraday drift + volatility (1m volatility ~ 0.05% - 0.2%)
        volatility = random.uniform(0.0005, 0.0018)
        drift = random.gauss(0.0, volatility)
        
        open_p = current_price
        close_p = max(0.5, open_p * (1.0 + drift))
        
        # High and Low intraday wicks
        wick_high = abs(random.gauss(0.0, volatility * 0.8))
        wick_low = abs(random.gauss(0.0, volatility * 0.8))
        high_p = max(open_p, close_p) * (1.0 + wick_high)
        low_p = min(open_p, close_p) * (1.0 - wick_low)
        
        # Volume lognormal distribution
        base_vol = random.randint(5000, 35000)
        volume = float(base_vol * random.uniform(0.8, 1.6))
        vwap = (open_p + high_p + low_p + close_p) / 4.0
        
        self.prices[ticker] = close_p
        
        return Bar(
            ticker=ticker,
            timestamp=timestamp,
            open=round(open_p, 4),
            high=round(high_p, 4),
            low=round(low_p, 4),
            close=round(close_p, 4),
            volume=round(volume, 0),
            vwap=round(vwap, 4),
        )


class DataFeeder:
    """Coordinates multi-ticker market data feeds, synchronized by timestamp."""

    def __init__(
        self,
        tickers: List[str],
        data_dir: Optional[str] = None,
        start_time: Optional[datetime] = None,
        generate_synthetic: bool = True,
    ):
        self.tickers = tickers
        self.data_dir = Path(data_dir) if data_dir else None
        self.start_time = start_time or datetime(2026, 1, 5, 9, 30, 0)
        self.generate_synthetic = generate_synthetic
        
        # Historical bars indexed by ticker -> list of Bar
        self._historical_data: Dict[str, List[Bar]] = {}
        self._cursor: int = 0
        self._timeline: List[datetime] = []
        self._synthetic_gen: Optional[SyntheticDataGenerator] = None

        self._load_or_init()

    def _load_or_init(self):
        loaded_any = False
        if self.data_dir and self.data_dir.exists():
            for ticker in self.tickers:
                # Look for {ticker}.csv or {ticker}_1m.csv
                csv_path = self.data_dir / f"{ticker}.csv"
                if not csv_path.exists():
                    csv_path = self.data_dir / f"{ticker}_1m.csv"
                
                if csv_path.exists():
                    try:
                        df = pd.read_csv(csv_path)
                        df.columns = [c.lower() for c in df.columns]
                        if "timestamp" in df.columns:
                            df["timestamp"] = pd.to_datetime(df["timestamp"])
                        elif "date" in df.columns:
                            df["timestamp"] = pd.to_datetime(df["date"])
                        else:
                            continue
                        
                        bars = []
                        for _, row in df.iterrows():
                            bars.append(
                                Bar(
                                    ticker=ticker,
                                    timestamp=row["timestamp"].to_pydatetime(),
                                    open=float(row["open"]),
                                    high=float(row["high"]),
                                    low=float(row["low"]),
                                    close=float(row["close"]),
                                    volume=float(row.get("volume", 10000.0)),
                                    vwap=float(row.get("vwap", (row["high"] + row["low"]) / 2.0)),
                                )
                            )
                        bars.sort(key=lambda b: b.timestamp)
                        self._historical_data[ticker] = bars
                        loaded_any = True
                        logger.info(f"Loaded {len(bars)} historical bars for {ticker} from {csv_path}")
                    except Exception as e:
                        logger.error(f"Failed to read CSV for {ticker}: {e}")

        if loaded_any:
            # Construct unified timeline across all loaded tickers
            all_timestamps = set()
            for ticker_bars in self._historical_data.values():
                for b in ticker_bars:
                    all_timestamps.add(b.timestamp)
            self._timeline = sorted(list(all_timestamps))
            logger.info(f"DataFeeder initialized with {len(self._timeline)} time steps from historical data.")
        elif self.generate_synthetic:
            self._synthetic_gen = SyntheticDataGenerator(self.tickers)
            logger.info(f"DataFeeder initialized in synthetic generation mode for tickers: {self.tickers}")

    def get_bars_for_time(self, timestamp: datetime) -> Dict[str, Bar]:
        """Fetch or generate synchronized bars for all tickers at the given timestamp."""
        result: Dict[str, Bar] = {}
        
        if self._synthetic_gen:
            for ticker in self.tickers:
                result[ticker] = self._synthetic_gen.generate_bar(ticker, timestamp)
            return result

        # From historical data:
        for ticker in self.tickers:
            bars = self._historical_data.get(ticker, [])
            # Find bar matching timestamp
            matched = next((b for b in bars if b.timestamp == timestamp), None)
            if matched:
                result[ticker] = matched
            elif bars:
                # If ticker has data but not exactly at this timestamp, carry forward last known price
                last_bar = bars[-1]
                result[ticker] = Bar(
                    ticker=ticker,
                    timestamp=timestamp,
                    open=last_bar.close,
                    high=last_bar.close,
                    low=last_bar.close,
                    close=last_bar.close,
                    volume=0.0,
                    vwap=last_bar.close,
                )
        return result

    @property
    def has_next_historical(self) -> bool:
        if self._synthetic_gen:
            return True
        return self._cursor < len(self._timeline)

    def next_historical_time(self) -> Optional[datetime]:
        if self._synthetic_gen:
            return None
        if self._cursor < len(self._timeline):
            t = self._timeline[self._cursor]
            self._cursor += 1
            return t
        return None
