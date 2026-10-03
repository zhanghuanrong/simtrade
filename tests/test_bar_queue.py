import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from simtrade.config import ServerConfig, SimulationConfig
from simtrade.engine.feeder import DataFeeder
from simtrade.engine.simulator import Simulator
from simtrade.models.order import OrderCreate, OrderSide, OrderType
from simtrade.server.app import create_app


def test_feeder_get_bars_between():
    dt0 = datetime(2026, 1, 5, 9, 30, 0)
    feeder = DataFeeder(tickers=["AAPL", "NVDA"], generate_synthetic=True, start_time=dt0)
    # Manually populate timeline
    feeder.timeline = [dt0 + timedelta(minutes=i) for i in range(10)]

    # Fetch slice (minutes 2 to 5)
    t_start = dt0 + timedelta(minutes=2)
    t_end = dt0 + timedelta(minutes=5)
    events = feeder.get_bars_between(t_start, t_end)
    
    assert len(events) == 3
    assert events[0]["sim_timestamp"] == (dt0 + timedelta(minutes=3)).isoformat()
    assert events[-1]["sim_timestamp"] == t_end.isoformat()
    assert "AAPL" in events[0]["bars"]


def test_simulator_drain_unseen_bars():
    sim = Simulator(sim_config=SimulationConfig(generate_synthetic_if_missing=True, speed_multiplier=0))
    # Step simulation 3 times
    for _ in range(3):
        sim.clock.step()

    # Drain for account A
    unseen_a = sim.drain_unseen_bars(account_id="account_a")
    assert len(unseen_a) > 0

    # Second drain for account A should be empty (already up to date)
    unseen_a_empty = sim.drain_unseen_bars(account_id="account_a")
    assert len(unseen_a_empty) == 0

    # Drain for a new account B should return all bars up to current time
    unseen_b = sim.drain_unseen_bars(account_id="account_b")
    assert len(unseen_b) == len(unseen_a)


def test_step_until_piggybacks_unseen_bars():
    app = create_app(
        server_config=ServerConfig(),
        sim_config=SimulationConfig(speed_multiplier=0, tickers=["AAPL", "NVDA"]),
    )
    with TestClient(app) as client:
        # Reset simulation
        client.post("/api/v1/sim/reset", json={})
        meta = client.get("/api/v1/sim/metadata").json()
        t0 = datetime.fromisoformat(meta["current_time"])

        # Accelerate 5 minutes forward via step_until
        target = t0 + timedelta(minutes=5)
        resp = client.post("/api/v1/sim/step_until", json={
            "target_time": target.isoformat(),
            "account_id": "algo_trader",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "TARGET_REACHED"
        assert "unseen_bars" in data
        assert len(data["unseen_bars"]) == 5
        assert data["unseen_bars"][-1]["sim_timestamp"] == target.isoformat()


def test_submit_order_piggybacks_unseen_bars():
    app = create_app(
        server_config=ServerConfig(),
        sim_config=SimulationConfig(speed_multiplier=0, tickers=["AAPL", "NVDA"]),
    )
    with TestClient(app) as client:
        # Reset simulation
        client.post("/api/v1/sim/reset", json={})
        meta = client.get("/api/v1/sim/metadata").json()
        t0 = datetime.fromisoformat(meta["current_time"])

        # Step 3 bars forward globally
        for _ in range(3):
            client.post("/api/v1/sim/step")

        # Now client submits an order
        order_payload = {
            "ticker": "AAPL",
            "side": "BUY",
            "quantity": 10.0,
            "order_type": "MARKET",
        }
        resp = client.post("/api/v1/orders?account_id=new_trader", json=order_payload)
        assert resp.status_code == 201
        order_data = resp.json()
        assert "unseen_bars" in order_data
        assert len(order_data["unseen_bars"]) == 3


def test_get_unseen_bars_polling_endpoint():
    app = create_app(
        server_config=ServerConfig(),
        sim_config=SimulationConfig(speed_multiplier=0, tickers=["AAPL", "NVDA"]),
    )
    with TestClient(app) as client:
        client.post("/api/v1/sim/reset", json={})
        
        # Poll initially
        resp0 = client.get("/api/v1/sim/unseen_bars?account_id=polling_trader")
        assert resp0.status_code == 200
        data0 = resp0.json()
        assert "unseen_bars" in data0

        # Advance 2 steps
        client.post("/api/v1/sim/step")
        client.post("/api/v1/sim/step")

        # Poll again
        resp1 = client.get("/api/v1/sim/unseen_bars?account_id=polling_trader")
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert len(data1["unseen_bars"]) == 2
        assert data1["count"] == 2
