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

    # Query order details via GET /api/v1/orders/{order_id}
    detail_resp = client.get(f"/api/v1/orders/{order_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["order_id"] == order_id
    assert detail_resp.json()["status"] == "ACCEPTED"

    # Verify listed in active orders
    active_resp = client.get("/api/v1/orders")
    assert any(o["order_id"] == order_id for o in active_resp.json())

    # Cancel the order
    del_resp = client.delete(f"/api/v1/orders/{order_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "CANCELLED"

    # Verify order details still queryable after cancellation
    detail_after = client.get(f"/api/v1/orders/{order_id}")
    assert detail_after.status_code == 200
    assert detail_after.json()["status"] == "CANCELLED"


def test_order_execution_and_account_update(client):
    from datetime import datetime, timedelta

    # Place a market order
    order_data = {
        "ticker": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": 10,
    }
    resp = client.post("/api/v1/orders", json=order_data)
    assert resp.status_code == 201
    order_id = resp.json()["order_id"]

    # Advance virtual time forward by 1 minute using step_until
    meta = client.get("/api/v1/sim/metadata").json()
    next_t = datetime.fromisoformat(meta["current_time"]) + timedelta(minutes=1)
    step_resp = client.post("/api/v1/sim/step_until", json={"target_time": next_t.isoformat()})
    assert step_resp.status_code == 200

    # Verify order details updated to FILLED
    order_detail = client.get(f"/api/v1/orders/{order_id}").json()
    assert order_detail["status"] == "FILLED"
    assert order_detail["filled_quantity"] == 10.0
    assert order_detail["avg_fill_price"] > 0

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
