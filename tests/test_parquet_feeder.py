from datetime import datetime
from pathlib import Path
import pytest
from simtrade.engine.feeder import DataFeeder
from simtrade.engine.simulator import Simulator
from simtrade.config import SimulationConfig


def test_parquet_feeder_loading():
    parquet_path = Path("data/1m_20260817_now.parquet")
    if not parquet_path.exists():
        pytest.skip("Parquet dataset not present")

    feeder = DataFeeder(
        tickers=["AAPL", "NVDA", "TSLA"],
        data_file=str(parquet_path),
    )

    assert len(feeder.tickers) == 3
    assert len(feeder.timeline) > 1000
    
    first_time = feeder.timeline[0]
    bars = feeder.get_bars_for_time(first_time)
    
    assert "AAPL" in bars
    assert "NVDA" in bars
    assert "TSLA" in bars
    assert bars["AAPL"].open > 0
    assert bars["AAPL"].high >= bars["AAPL"].low
    assert bars["AAPL"].volume > 0


def test_simulator_with_parquet():
    parquet_path = Path("data/1m_20260817_now.parquet")
    if not parquet_path.exists():
        pytest.skip("Parquet dataset not present")

    sim_cfg = SimulationConfig(
        tickers=["AAPL", "NVDA"],
        data_file=str(parquet_path),
        speed_multiplier=0,
    )
    sim = Simulator(sim_config=sim_cfg)
    
    assert sim.clock.current_time == sim.feeder.timeline[0]
    
    # Step simulation forward
    import asyncio
    res = asyncio.run(sim.step())
    
    assert res["timestamp"] == sim.feeder.timeline[1].isoformat()
    assert "AAPL" in res["bars"]
    assert res["bars"]["AAPL"]["close"] > 0
