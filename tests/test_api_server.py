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


def test_get_sim_status(client):
    resp = client.get("/api/v1/sim/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "current_time" in data
    assert "tickers" in data


def test_submit_and_cancel_order(client):
    # Step to populate initial prices
    client.post("/api/v1/sim/step")

    # Place a limit buy order below current price
    order_data = {
        "ticker": "AAPL",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 10,
        "limit_price": 50.0,
        "time_in_force": "GTC"
    }
    resp = client.post("/api/v1/orders", json=order_data)
    assert resp.status_code == 201
    order = resp.json()
    order_id = order["order_id"]
    assert order["status"] == "ACCEPTED"

    # Verify listed in active orders
    active_resp = client.get("/api/v1/orders")
    assert any(o["order_id"] == order_id for o in active_resp.json())

    # Cancel the order
    del_resp = client.delete(f"/api/v1/orders/{order_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "CANCELLED"


def test_order_execution_and_account_update(client):
    # Place a market order
    order_data = {
        "ticker": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": 10,
    }
    resp = client.post("/api/v1/orders", json=order_data)
    assert resp.status_code == 201

    # Advance step
    step_resp = client.post("/api/v1/sim/step")
    assert step_resp.status_code == 200

    # Verify positions and trade execution
    acc_resp = client.get("/api/v1/account")
    acc = acc_resp.json()
    assert "AAPL" in acc["positions"]
    assert acc["positions"]["AAPL"]["quantity"] == 10

    # Verify performance report
    perf_resp = client.get("/api/v1/reports/performance")
    assert perf_resp.status_code == 200
    perf = perf_resp.json()
    assert perf["total_trades"] >= 1


def test_dashboard_endpoint(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "SimTrade Server" in resp.text
