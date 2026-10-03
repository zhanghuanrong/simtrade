import pytest
from fastapi.testclient import TestClient
from simtrade.config import ServerConfig, SimulationConfig
from simtrade.server.app import create_app


@pytest.fixture
def client():
    app = create_app(
        server_config=ServerConfig(),
        sim_config=SimulationConfig(speed_multiplier=0, tickers=["AAPL", "NVDA"]),
    )
    with TestClient(app) as test_client:
        yield test_client


def test_simulation_metadata_and_time_range(client):
    resp = client.get("/api/v1/sim/metadata")
    assert resp.status_code == 200
    data = resp.json()
    assert "start_time" in data
    assert "end_time" in data
    assert "total_bars" in data
    assert "available_tickers" in data
    assert isinstance(data["available_tickers"], list)


def test_account_negotiation_and_setup(client):
    # Setup a tagged pass with starting positions, initial total equity, and custom leverage
    setup_payload = {
        "account_id": "momentum_pass_1",
        "tag": "sma_fast_run",
        "initial_total_equity": 100000.0,
        "leverage": 4.0,  # 4x leverage = 25% initial margin
        "initial_positions": {
            "AAPL": 50.0,
        }
    }
    resp = client.post("/api/v1/account/setup", json=setup_payload)
    assert resp.status_code == 200
    acc = resp.json()
    assert acc["account_id"] == "momentum_pass_1"
    assert acc["tag"] == "sma_fast_run"
    assert acc["equity"] == 100000.0
    assert "AAPL" in acc["positions"]
    assert acc["positions"]["AAPL"]["quantity"] == 50.0
    # Positions occupy margin
    assert acc["margin"]["initial_margin_requirement"] > 0.0
    assert acc["cash"] < 100000.0  # Cash is equity minus position market value
    # Buying power at 4x leverage
    assert acc["margin"]["buying_power"] > 200000.0
    assert acc["custom_margin_config"]["max_leverage"] == 4.0
    assert "_internal_margin_config" not in acc

    # Verify listed in /accounts
    list_resp = client.get("/api/v1/accounts")
    assert list_resp.status_code == 200
    accounts = list_resp.json()
    assert any(a["account_id"] == "momentum_pass_1" for a in accounts)


def test_simulation_reset_and_export(client):
    from datetime import datetime, timedelta

    meta = client.get("/api/v1/sim/metadata").json()
    curr_t = datetime.fromisoformat(meta["current_time"])
    # Advance using step_until
    client.post("/api/v1/sim/step_until", json={"target_time": (curr_t + timedelta(minutes=2)).isoformat()})

    # Place an order for default trader_1
    order_data = {
        "ticker": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": 10,
    }
    client.post("/api/v1/orders?account_id=trader_1", json=order_data)
    client.post("/api/v1/sim/step_until", json={"target_time": (curr_t + timedelta(minutes=3)).isoformat()})

    # Export trades as CSV
    csv_resp = client.get("/api/v1/reports/trades/csv?account_id=trader_1")
    assert csv_resp.status_code == 200
    assert "trade_id,account_id,order_id" in csv_resp.text
    assert "AAPL" in csv_resp.text

    # Save session reports to server
    save_resp = client.post("/api/v1/reports/save", json={"account_id": "trader_1", "output_dir": "reports"})
    assert save_resp.status_code == 200
    assert save_resp.json()["status"] == "saved"

    # Reset simulation
    reset_resp = client.post("/api/v1/sim/reset", json={})
    assert reset_resp.status_code == 200
    meta = client.get("/api/v1/sim/metadata").json()
    assert meta["cursor"] == 0


def test_step_until_flow(client):
    from datetime import datetime, timedelta

    meta = client.get("/api/v1/sim/metadata").json()
    current_dt = datetime.fromisoformat(meta["current_time"])

    # 1. Step until +5 minutes into the future from current T_anchor
    target_dt_1 = current_dt + timedelta(minutes=5)
    resp = client.post("/api/v1/sim/step_until", json={
        "target_time": target_dt_1.isoformat(),
        "account_id": "trader_1",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "TARGET_REACHED"
    assert data["bars_processed"] == 5
    assert data["current_time"] == target_dt_1.isoformat()
    assert len(data["bars"]) > 0

    # 2. Ignored when target_time is in the past or current anchor
    past_dt = current_dt - timedelta(minutes=1)
    resp_past = client.post("/api/v1/sim/step_until", json={
        "target_time": past_dt.isoformat(),
        "account_id": "trader_1",
    })
    assert resp_past.status_code == 200
    data_past = resp_past.json()
    assert data_past["status"] == "ignored"
    assert data_past["bars_processed"] == 0
    assert "target_time is less than or equal" in data_past["reason"]

    # Same time as current T_anchor
    resp_same = client.post("/api/v1/sim/step_until", json={
        "target_time": target_dt_1.isoformat(),
        "account_id": "trader_1",
    })
    assert resp_same.status_code == 200
    assert resp_same.json()["status"] == "ignored"

    # 3. Non-trading gap handling: step into non-trading hours
    # Market closes at 16:00 ET. If we step until 22:00 ET of same day:
    from simtrade.utils import to_eastern_time
    target_gap = to_eastern_time(datetime(current_dt.year, current_dt.month, current_dt.day, 22, 0, 0))
    resp_gap = client.post("/api/v1/sim/step_until", json={
        "target_time": target_gap.isoformat(),
        "account_id": "trader_1",
    })
    assert resp_gap.status_code == 200
    data_gap = resp_gap.json()
    assert data_gap["status"] == "TARGET_REACHED"
    assert data_gap["current_time"] == target_gap.isoformat()
    assert data_gap["bars"] == {}  # Empty bars for non-trading gap


def test_step_until_liquidation_halt(client):
    from datetime import datetime, timedelta

    client.post("/api/v1/sim/reset", json={})
    meta = client.get("/api/v1/sim/metadata").json()
    current_dt = datetime.fromisoformat(meta["current_time"])

    # Setup an account where positions occupy more margin than equity allows
    setup_resp = client.post("/api/v1/account/setup", json={
        "account_id": "margin_risk_trader",
        "tag": "risk_pass",
        "initial_total_equity": 1000.0,
        "initial_positions": {"AAPL": 50.0},
        "maintenance_margin_rate": 0.25,
    })
    assert setup_resp.status_code == 200

    # Request step_until +10 minutes
    target_dt = current_dt + timedelta(minutes=10)
    resp = client.post("/api/v1/sim/step_until", json={
        "target_time": target_dt.isoformat(),
        "account_id": "margin_risk_trader",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "LIQUIDATION_TRIGGERED"
    assert data["account_id"] == "margin_risk_trader"
    assert data["deficit"] > 0
    # Verified it halted early on the first bar (not completing all 10 bars)
    assert data["bars_processed"] == 1
    # Updated current_time is at the halt timestamp
    halt_time = datetime.fromisoformat(data["current_time"])
    assert halt_time < target_dt

    # The client handles the feedback and can issue a subsequent step_until
    subsequent_target = halt_time + timedelta(minutes=2)
    subsequent_resp = client.post("/api/v1/sim/step_until", json={
        "target_time": subsequent_target.isoformat(),
        "account_id": "margin_risk_trader",
    })
    assert subsequent_resp.status_code == 200


def test_order_server_timestamps_and_step_to_mapping(client):
    from datetime import datetime, timedelta

    # 1. Reset simulation
    client.post("/api/v1/sim/reset", json={})
    meta = client.get("/api/v1/sim/metadata").json()
    sim_t0 = datetime.fromisoformat(meta["current_time"])

    # 2. Client submits order WITHOUT client-side timestamp
    order_payload = {
        "ticker": "AAPL",
        "side": "BUY",
        "quantity": 10.0,
        "order_type": "LIMIT",
        "limit_price": 150.0,
    }
    resp = client.post("/api/v1/orders", json=order_payload)
    assert resp.status_code == 201
    order_data = resp.json()

    # 3. Server must have added sim_created_at (virtual time) and wall_received_at (wall time)
    assert "sim_created_at" in order_data
    assert "wall_received_at" in order_data
    assert order_data["wall_received_at"] is not None
    assert "created_at" in order_data
    assert "server_received_at" in order_data

    sim_created_at = datetime.fromisoformat(order_data["sim_created_at"])
    wall_received_at = datetime.fromisoformat(order_data["wall_received_at"])

    # Virtual sim_created_at matches simulation time at submission
    assert sim_created_at.date() == sim_t0.date()
    # Real wall time wall_received_at is today's real wall clock
    assert wall_received_at.year >= 2026

    # 4. Client accelerates simulation with step_until (STEP_TO)
    step_target = sim_t0 + timedelta(minutes=5)
    step_resp = client.post("/api/v1/sim/step_until", json={
        "target_time": step_target.isoformat(),
        "account_id": "trader_1",
    })
    assert step_resp.status_code == 200
    assert step_resp.json()["current_time"] == step_target.isoformat()


