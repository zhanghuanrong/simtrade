from datetime import datetime, timedelta
import pytest
from simtrade.engine.clock import SimClock
from simtrade.engine.feeder import DataFeeder, SyntheticDataGenerator
from simtrade.utils import NY_TZ, to_eastern_time


def test_clock_stepping():
    start = datetime(2026, 1, 5, 9, 30, 0, tzinfo=NY_TZ)
    clock = SimClock(start_time=start, speed_multiplier=1.0, interval_seconds=60)
    
    assert clock.current_time == start
    t1 = clock.step()
    assert t1 == start + timedelta(minutes=1)
    assert clock.step_count == 1

    t2 = clock.step()
    assert t2 == start + timedelta(minutes=2)
    assert clock.step_count == 2


def test_clock_listeners():
    clock = SimClock()
    calls = []
    clock.add_listener(lambda t: calls.append(t))
    clock.step()
    assert len(calls) == 1


def test_synthetic_feeder():
    tickers = ["AAPL", "NVDA"]
    feeder = DataFeeder(tickers=tickers, generate_synthetic=True)
    dt = datetime(2026, 1, 5, 9, 30, 0)
    
    bars = feeder.get_bars_for_time(dt)
    assert "AAPL" in bars
    assert "NVDA" in bars
    assert bars["AAPL"].ticker == "AAPL"
    assert bars["AAPL"].high >= bars["AAPL"].low
    assert bars["AAPL"].volume > 0


def test_historical_csv_feeder(tmp_path):
    import pandas as pd
    dt = datetime(2026, 1, 5, 9, 30, 0)
    df = pd.DataFrame([
        {"timestamp": (dt + timedelta(minutes=i)).isoformat(),
         "open": 100.0 + i, "high": 102.0 + i, "low": 99.0 + i, "close": 101.0 + i, "volume": 1000}
        for i in range(5)
    ])
    csv_file = tmp_path / "TEST_1m.csv"
    df.to_csv(csv_file, index=False)

    feeder = DataFeeder(tickers=["TEST"], data_dir=str(tmp_path))
    b0 = feeder.get_bars_for_time(dt)
    assert "TEST" in b0
    assert b0["TEST"].open == 100.0


def test_clock_discrete_stepping():
    start = datetime(2026, 1, 5, 9, 30, 0, tzinfo=NY_TZ)
    clock = SimClock(start_time=start)
    
    assert clock.now() == start
    assert clock.current_time == start

    # Advancing step_to updates current time directly
    t1 = clock.step_to(datetime(2026, 1, 5, 9, 31, 0, tzinfo=NY_TZ))
    assert t1 == datetime(2026, 1, 5, 9, 31, 0, tzinfo=NY_TZ)
    assert clock.now() == datetime(2026, 1, 5, 9, 31, 0, tzinfo=NY_TZ)

    # set_time directly sets current time
    target = datetime(2026, 1, 5, 11, 0, 0, tzinfo=NY_TZ)
    clock.set_time(target, reason="STEP_TO")
    assert clock.current_time == target
    assert clock.now() == target

