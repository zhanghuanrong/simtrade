"""Market data feeder supporting Parquet/CSV playback and synthetic 1m OHLCV generation."""

from datetime import datetime, timedelta
import os
from pathlib import Path
import random
from typing import Any, Dict, Iterator, List, Optional
import pandas as pd
import logging

from simtrade.models.market_data import Bar

logger = logging.getLogger(__name__)


from simtrade.utils import to_eastern_time, NY_TZ


def normalize_ts(ts) -> datetime:
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    return to_eastern_time(ts)


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
        volatility = random.uniform(0.0005, 0.0018)
        drift = random.gauss(0.0, volatility)
        
        open_p = current_price
        close_p = max(0.5, open_p * (1.0 + drift))
        
        wick_high = abs(random.gauss(0.0, volatility * 0.8))
        wick_low = abs(random.gauss(0.0, volatility * 0.8))
        high_p = max(open_p, close_p) * (1.0 + wick_high)
        low_p = min(open_p, close_p) * (1.0 - wick_low)
        
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
        tickers: Optional[List[str]] = None,
        data_file: Optional[str] = None,
        data_dir: Optional[str] = None,
        start_time: Optional[datetime] = None,
        generate_synthetic: bool = True,
    ):
        self.tickers = [t.upper() for t in tickers] if tickers else []
        self.data_file = Path(data_file) if data_file else None
        self.data_dir = Path(data_dir) if data_dir else None
        self.start_time: Optional[datetime] = start_time
        self.generate_synthetic = generate_synthetic

        # High-performance in-memory indexing:
        # timestamp -> {ticker: Bar}
        self._bars_by_time: Dict[datetime, Dict[str, Bar]] = {}
        self._last_bars: Dict[str, Bar] = {}
        self.timeline: List[datetime] = []
        self._synthetic_gen: Optional[SyntheticDataGenerator] = None

        self._load_or_init()

    def _load_or_init(self):
        loaded = False

        # 1. Try loading from explicit data_file if specified
        if self.data_file and self.data_file.exists():
            loaded = self._load_file(self.data_file)

        # 2. If not loaded, search for parquet files in data_dir
        if not loaded and self.data_dir and self.data_dir.exists():
            parquet_files = sorted(list(self.data_dir.glob("*.parquet")))
            for p_file in parquet_files:
                if self._load_file(p_file):
                    loaded = True
                    break

        # 3. If not loaded, search for individual {ticker}.csv files in data_dir
        if not loaded and self.data_dir and self.data_dir.exists():
            loaded = self._load_csv_directory(self.data_dir)

        # 4. Fallback to synthetic if allowed
        if loaded:
            self.timeline = sorted(list(self._bars_by_time.keys()))
            if self.timeline:
                self.start_time = self.timeline[0]
                logger.info(
                    f"DataFeeder loaded {len(self.timeline)} time steps for "
                    f"{len(self.tickers)} tickers ({self.timeline[0]} to {self.timeline[-1]})."
                )
        elif self.generate_synthetic:
            if not self.tickers:
                self.tickers = ["AAPL", "NVDA", "TSLA", "MSFT"]
            self._synthetic_gen = SyntheticDataGenerator(self.tickers)
            if not self.start_time:
                self.start_time = datetime(2026, 1, 5, 9, 30, 0, tzinfo=NY_TZ)
            logger.info(f"DataFeeder initialized in synthetic mode for {self.tickers}")

    def _load_file(self, file_path: Path) -> bool:
        """Load from a consolidated Parquet or CSV file."""
        try:
            logger.info(f"Loading market data from {file_path}...")
            if file_path.suffix.lower() == ".parquet":
                df = pd.read_parquet(file_path)
            else:
                df = pd.read_csv(file_path)

            df.columns = [c.lower() for c in df.columns]

            time_col = next((c for c in ["time", "timestamp", "datetime", "date"] if c in df.columns), None)
            symbol_col = next((c for c in ["symbol", "ticker", "code"] if c in df.columns), None)

            if not time_col:
                logger.error(f"Cannot identify timestamp column in {file_path}")
                return False

            # Convert timestamp to America/New_York (Eastern Time)
            s_time = pd.to_datetime(df[time_col])
            if s_time.dt.tz is not None:
                df["norm_time"] = s_time.dt.tz_convert(NY_TZ)
            else:
                df["norm_time"] = s_time.dt.tz_localize(NY_TZ)

            # Filter or discover symbols
            if symbol_col:
                df[symbol_col] = df[symbol_col].astype(str).str.upper()
                available_symbols = df[symbol_col].unique().tolist()
                if self.tickers and "ALL" not in self.tickers:
                    df = df[df[symbol_col].isin(self.tickers)]
                else:
                    self.tickers = sorted(available_symbols)
            else:
                # Single-symbol file, infer from filename
                symbol_name = file_path.stem.split("_")[0].upper()
                df["symbol"] = symbol_name
                symbol_col = "symbol"
                if not self.tickers:
                    self.tickers = [symbol_name]

            if df.empty:
                logger.warning(f"No matching rows found in {file_path} for tickers {self.tickers}")
                return False

            # Build fast lookup dictionary: timestamp -> {ticker: Bar}
            for row in df.itertuples(index=False):
                ts = normalize_ts(getattr(row, "norm_time"))
                ticker = getattr(row, symbol_col)
                open_p = float(getattr(row, "open"))
                high_p = float(getattr(row, "high"))
                low_p = float(getattr(row, "low"))
                close_p = float(getattr(row, "close"))
                volume = float(getattr(row, "volume", 0.0))
                vwap = float(getattr(row, "vwap", (high_p + low_p) / 2.0))

                bar = Bar(
                    ticker=ticker,
                    timestamp=ts,
                    open=round(open_p, 4),
                    high=round(high_p, 4),
                    low=round(low_p, 4),
                    close=round(close_p, 4),
                    volume=round(volume, 0),
                    vwap=round(vwap, 4),
                )

                if ts not in self._bars_by_time:
                    self._bars_by_time[ts] = {}
                self._bars_by_time[ts][ticker] = bar

            return True
        except Exception as e:
            logger.error(f"Failed to load file {file_path}: {e}", exc_info=True)
            return False

    def _load_csv_directory(self, dir_path: Path) -> bool:
        """Load multiple individual {ticker}.csv files."""
        loaded_any = False
        target_tickers = self.tickers or ["AAPL", "NVDA", "TSLA", "MSFT"]
        
        for ticker in target_tickers:
            csv_path = dir_path / f"{ticker}.csv"
            if not csv_path.exists():
                csv_path = dir_path / f"{ticker}_1m.csv"

            if csv_path.exists():
                try:
                    df = pd.read_csv(csv_path)
                    df.columns = [c.lower() for c in df.columns]
                    time_col = next((c for c in ["timestamp", "date", "time"] if c in df.columns), None)
                    if not time_col:
                        continue

                    s_time = pd.to_datetime(df[time_col])
                    if s_time.dt.tz is not None:
                        df["norm_time"] = s_time.dt.tz_convert(NY_TZ)
                    else:
                        df["norm_time"] = s_time.dt.tz_localize(NY_TZ)
                    for row in df.itertuples(index=False):
                        ts = normalize_ts(getattr(row, "norm_time"))

                        bar = Bar(
                            ticker=ticker,
                            timestamp=ts,
                            open=float(getattr(row, "open")),
                            high=float(getattr(row, "high")),
                            low=float(getattr(row, "low")),
                            close=float(getattr(row, "close")),
                            volume=float(getattr(row, "volume", 10000.0)),
                            vwap=float(getattr(row, "vwap", (float(getattr(row, "high")) + float(getattr(row, "low"))) / 2.0)),
                        )
                        if ts not in self._bars_by_time:
                            self._bars_by_time[ts] = {}
                        self._bars_by_time[ts][ticker] = bar

                    loaded_any = True
                except Exception as e:
                    logger.error(f"Error loading {csv_path}: {e}")

        if loaded_any and not self.tickers:
            self.tickers = target_tickers

        return loaded_any

    def get_bars_for_time(self, timestamp: datetime) -> Dict[str, Bar]:
        """Fetch or generate synchronized bars for all tickers at the given timestamp."""
        result: Dict[str, Bar] = {}

        if self._synthetic_gen:
            for ticker in self.tickers:
                result[ticker] = self._synthetic_gen.generate_bar(ticker, timestamp)
            return result

        # Fast lookup from in-memory index
        norm_t = normalize_ts(timestamp)
        if norm_t not in self._bars_by_time:
            return {}

        matched_bars = self._bars_by_time[norm_t]
        for ticker in self.tickers:
            if ticker in matched_bars:
                bar = matched_bars[ticker]
                result[ticker] = bar
                self._last_bars[ticker] = bar
            elif ticker in self._last_bars:
                # Carry forward last known close price with zero volume for illiquid tickers
                last = self._last_bars[ticker]
                result[ticker] = Bar(
                    ticker=ticker,
                    timestamp=timestamp,
                    open=last.close,
                    high=last.close,
                    low=last.close,
                    close=last.close,
                    volume=0.0,
                    vwap=last.close,
                )

        return result

    def get_latest_price(self, ticker: str) -> Optional[float]:
        """Return the most recent known price for a ticker, or first known price if not yet stepped."""
        if ticker in self._last_bars:
            return self._last_bars[ticker].close
        for bars in self._bars_by_time.values():
            if ticker in bars:
                return bars[ticker].open
        return None

    def get_bars_between(self, start_ts: Optional[datetime], end_ts: datetime) -> List[Dict[str, Any]]:
        """
        Return chronological sequence of bar events for timestamps t where start_ts < t <= end_ts.
        Each item is: {"sim_timestamp": t.isoformat(), "bars": {ticker: bar_dict}}
        """
        end_norm = normalize_ts(end_ts)
        start_norm = normalize_ts(start_ts) if start_ts else None

        events: List[Dict[str, Any]] = []
        if self.timeline:
            for t in self.timeline:
                t_norm = normalize_ts(t)
                if start_norm and t_norm <= start_norm:
                    continue
                if t_norm > end_norm:
                    break
                bars = self.get_bars_for_time(t)
                events.append({
                    "sim_timestamp": t.isoformat(),
                    "bars": {ticker: b.model_dump(mode="json") for ticker, b in bars.items()}
                })
        return events
