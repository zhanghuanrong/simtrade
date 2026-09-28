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
    # Setup a tagged pass with starting positions and custom leverage
    setup_payload = {
        "account_id": "momentum_pass_1",
        "tag": "sma_fast_run",
        "initial_cash": 75000.0,
        "leverage": 4.0,  # 4x leverage = 25% initial margin
        "initial_positions": {
            "AAPL": 50.0,
        },
        "initial_entry_prices": {
            "AAPL": 300.0,
        }
    }
    resp = client.post("/api/v1/account/setup", json=setup_payload)
    assert resp.status_code == 200
    acc = resp.json()
    assert acc["account_id"] == "momentum_pass_1"
    assert acc["tag"] == "sma_fast_run"
    assert acc["cash"] == 75000.0
    assert "AAPL" in acc["positions"]
    assert acc["positions"]["AAPL"]["quantity"] == 50.0
    # Buying power at 4x leverage
    assert acc["margin"]["buying_power"] > 200000.0

    # Verify listed in /accounts
    list_resp = client.get("/api/v1/accounts")
    assert list_resp.status_code == 200
    accounts = list_resp.json()
    assert any(a["account_id"] == "momentum_pass_1" for a in accounts)


def test_simulation_reset_and_export(client):
    # Step simulation forward
    client.post("/api/v1/sim/step")
    client.post("/api/v1/sim/step")

    # Place an order for default trader_1
    order_data = {
        "ticker": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": 10,
    }
    client.post("/api/v1/orders?account_id=trader_1", json=order_data)
    client.post("/api/v1/sim/step")

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
