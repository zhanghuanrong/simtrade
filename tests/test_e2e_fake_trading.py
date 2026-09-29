"""Automated end-to-end tests for the fake trading client replaying history JSONs against SimTrade."""

import asyncio
from pathlib import Path
import socket
import threading
import time
import pytest
import uvicorn

from simtrade.client.e2e_fake_trading_client import run_fake_trading
from simtrade.config import ServerConfig, SimulationConfig
from simtrade.server.app import create_app


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def e2e_server():
    parquet_path = Path("data/1m_20260817_now.parquet")
    if not parquet_path.exists():
        pytest.skip("Parquet dataset data/1m_20260817_now.parquet not found")

    port = find_free_port()
    app = create_app(
        server_config=ServerConfig(port=port),
        sim_config=SimulationConfig(
            speed_multiplier=0,
            tickers=["ALL"],
            data_file=str(parquet_path),
        ),
    )

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server ready
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)

    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.mark.asyncio
async def test_e2e_checker_trading_history(e2e_server, tmp_path):
    history_file = "data/test_checker_trading_history.json"
    if not Path(history_file).exists():
        pytest.skip(f"{history_file} not found")

    csv_file = str(tmp_path / "checker_trades.csv")
    reports_dir = str(tmp_path / "reports_checker")

    summary = await run_fake_trading(
        history_file=history_file,
        server_url=e2e_server,
        account_id="e2e_test_checker",
        tag="checker_pass",
        export_csv=csv_file,
        reports_dir=reports_dir,
    )

    assert summary["status"] == "COMPLETED"
    assert summary["account_id"] == "e2e_test_checker"
    assert summary["initial_capital"] == 100_000.0
    assert summary["ending_equity"] > 0
    assert summary["total_orders_submitted"] > 0
    assert summary["total_trades_executed"] > 0
    assert summary["total_orders_dropped"] == 10

    # Verify CSV export
    assert Path(csv_file).exists()
    csv_text = Path(csv_file).read_text()
    assert "trade_id" in csv_text
    assert "account_id" in csv_text


@pytest.mark.asyncio
async def test_e2e_trading_history(e2e_server, tmp_path):
    history_file = "data/test_trading_history.json"
    if not Path(history_file).exists():
        pytest.skip(f"{history_file} not found")

    csv_file = str(tmp_path / "trading_history_trades.csv")
    reports_dir = str(tmp_path / "reports_history")

    summary = await run_fake_trading(
        history_file=history_file,
        server_url=e2e_server,
        account_id="e2e_test_history",
        tag="trading_pass",
        export_csv=csv_file,
        reports_dir=reports_dir,
    )

    assert summary["status"] == "COMPLETED"
    assert summary["account_id"] == "e2e_test_history"
    assert summary["initial_capital"] == 100_000.0
    assert summary["ending_equity"] > 0
    assert summary["total_orders_submitted"] > 0
    assert summary["total_trades_executed"] > 0
    assert summary["total_orders_dropped"] == 10
    assert Path(csv_file).exists()
