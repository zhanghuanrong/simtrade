import asyncio
import socket
import threading
import time
import pytest
import uvicorn

from simtrade.client.trader_client import SimTradeClient
from simtrade.config import ServerConfig, SimulationConfig
from simtrade.models.order import OrderType
from simtrade.server.app import create_app


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def running_server():
    port = find_free_port()
    app = create_app(
        server_config=ServerConfig(port=port),
        sim_config=SimulationConfig(speed_multiplier=0, tickers=["AAPL", "NVDA"]),
    )
    
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    
    # Wait for server to start
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)
            
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.mark.asyncio
async def test_client_sdk_workflow(running_server):
    client = SimTradeClient(base_url=running_server, account_id="trader_1")
    await client.connect()

    received_bars = []
    received_trades = []

    @client.on_bar
    async def handle_bar(bars):
        received_bars.append(bars)

    @client.on_trade
    async def handle_trade(trade):
        received_trades.append(trade)

    # 1. Submit Buy order
    order = await client.buy("AAPL", quantity=25, order_type=OrderType.MARKET)
    assert order.status.value in ("ACCEPTED", "FILLED")

    # 2. Advance time using step_until to trigger fill
    meta = await client.get_metadata()
    from datetime import datetime, timedelta
    current_dt = datetime.fromisoformat(meta["current_time"])
    target_dt = current_dt + timedelta(minutes=1)
    step_res = await client.step_until(target_dt)
    assert step_res["status"] == "TARGET_REACHED"
    await asyncio.sleep(0.1)

    # 3. Test get_order SDK method
    order_detail = await client.get_order(order.order_id)
    assert order_detail.order_id == order.order_id
    assert order_detail.status.value == "FILLED"
    assert order_detail.filled_quantity == 25.0

    # 4. Check account
    acc = await client.get_account()
    assert acc.cash < 100_000.0
    assert "AAPL" in acc.positions
    assert acc.positions["AAPL"].quantity == 25

    # 5. Check performance
    perf = await client.get_performance()
    assert perf["total_trades"] >= 1

    # 6. Test further step_until acceleration
    target_dt_2 = target_dt + timedelta(minutes=3)
    step_until_res = await client.step_until(target_dt_2)
    assert step_until_res["status"] == "TARGET_REACHED"
    assert step_until_res["bars_processed"] == 3

    await client.close()
