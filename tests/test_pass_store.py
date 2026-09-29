"""Unit and integration tests for PassStore and finished pass inspection APIs."""

import json
from pathlib import Path
import tempfile
import pytest
from fastapi.testclient import TestClient

from simtrade.config import ServerConfig, SimulationConfig
from simtrade.reporting.pass_store import PassRecord, PassStore, PassSummary
from simtrade.server.app import create_app
from simtrade.utils import utc_now


def test_pass_store_save_and_retrieve():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = PassStore(storage_dir=tmpdir)
        assert len(store.list_passes()) == 0

        summary = PassSummary(
            pass_id="test_momentum_1",
            account_id="acc_1",
            tag="momentum_alpha",
            created_at=utc_now(),
            completed_at=utc_now(),
            initial_capital=100_000.0,
            ending_equity=125_000.0,
            net_profit=25_000.0,
            total_return_pct=25.0,
            cash=125_000.0,
            total_trades=2,
            sharpe_ratio=2.15,
            max_drawdown_pct=4.5,
        )
        trades = [
            {
                "trade_id": "T1",
                "account_id": "acc_1",
                "order_id": "O1",
                "ticker": "AAPL",
                "side": "BUY",
                "price": 150.0,
                "quantity": 100.0,
                "notional": 15000.0,
                "commission": 1.0,
                "timestamp": "2026-08-20T10:00:00",
            },
            {
                "trade_id": "T2",
                "account_id": "acc_1",
                "order_id": "O2",
                "ticker": "AAPL",
                "side": "SELL",
                "price": 175.0,
                "quantity": 100.0,
                "notional": 17500.0,
                "commission": 1.0,
                "timestamp": "2026-08-20T14:00:00",
            },
        ]
        snapshots = [
            {"sim_time": "2026-08-20T10:00:00", "equity": 100000.0, "cash": 85000.0},
            {"sim_time": "2026-08-20T14:00:00", "equity": 125000.0, "cash": 125000.0},
        ]
        record = PassRecord(
            summary=summary,
            performance={"total_pnl": 25000.0, "sharpe_ratio": 2.15},
            positions={},
            trades=trades,
            snapshots=snapshots,
            ledger_entries=[],
        )

        store.save_pass(record)

        # In-memory retrieval
        loaded = store.get_pass("test_momentum_1")
        assert loaded is not None
        assert loaded.summary.ending_equity == 125_000.0
        assert len(loaded.trades) == 2
        assert len(loaded.snapshots) == 2

        # CSV export
        csv_out = store.export_trades_csv("test_momentum_1")
        assert "trade_id,account_id" in csv_out
        assert "AAPL" in csv_out
        assert "175.0" in csv_out

        # Reload store from disk in new instance to verify file persistence
        store2 = PassStore(storage_dir=tmpdir)
        assert len(store2.list_passes()) == 1
        reloaded = store2.get_pass("test_momentum_1")
        assert reloaded is not None
        assert reloaded.summary.net_profit == 25_000.0


def test_pass_store_legacy_scan():
    with tempfile.TemporaryDirectory() as tmpdir:
        dir_path = Path(tmpdir)
        # Create a legacy performance file and trades CSV
        perf_data = {
            "initial_capital": 50000.0,
            "final_equity": 55000.0,
            "total_pnl": 5000.0,
            "total_return_pct": 10.0,
            "sharpe_ratio": 1.8,
            "max_drawdown_pct": 3.2,
            "total_trades": 1,
        }
        with open(dir_path / "performance_legacy_strat.json", "w") as f:
            json.dump(perf_data, f)

        with open(dir_path / "trades_legacy_strat.csv", "w") as f:
            f.write("trade_id,account_id,order_id,ticker,side,price,quantity,notional,commission,timestamp\n")
            f.write("TL1,legacy_strat,OL1,NVDA,BUY,120.0,50,6000.0,0.5,2026-08-20T11:00:00\n")

        # Loading PassStore should discover legacy_strat
        store = PassStore(storage_dir=tmpdir)
        passes = store.list_passes()
        assert len(passes) == 1
        assert passes[0].pass_id == "legacy_strat"
        assert passes[0].ending_equity == 55000.0

        rec = store.get_pass("legacy_strat")
        assert rec is not None
        assert len(rec.trades) == 1
        assert rec.trades[0]["ticker"] == "NVDA"


def test_passes_api_endpoints():
    app = create_app(
        server_config=ServerConfig(),
        sim_config=SimulationConfig(speed_multiplier=0, tickers=["AAPL", "NVDA"]),
    )
    with TestClient(app) as client:
        # Check passes endpoint
        resp = client.get("/api/v1/passes")
        assert resp.status_code == 200
        initial_passes = resp.json()
        assert isinstance(initial_passes, list)

        # Trigger save of current session
        save_resp = client.post("/api/v1/reports/save", json={"account_id": "trader_1", "output_dir": "reports"})
        assert save_resp.status_code == 200
        data = save_resp.json()
        assert data["status"] == "saved"
        pass_id = data["files"]["pass_id"]

        # Now pass_id must appear in list_passes
        resp2 = client.get("/api/v1/passes")
        assert resp2.status_code == 200
        pass_ids = [p["pass_id"] for p in resp2.json()]
        assert pass_id in pass_ids

        # Fetch full pass record
        rec_resp = client.get(f"/api/v1/passes/{pass_id}")
        assert rec_resp.status_code == 200
        rec = rec_resp.json()
        assert rec["summary"]["pass_id"] == pass_id
        assert "performance" in rec
        assert "snapshots" in rec
        assert "trades" in rec

        # Download pass trades CSV
        csv_resp = client.get(f"/api/v1/passes/{pass_id}/trades/csv")
        assert csv_resp.status_code == 200
        assert csv_resp.headers["content-type"].startswith("text/csv")

        # Query account snapshots
        snap_resp = client.get("/api/v1/account/snapshots?account_id=trader_1")
        assert snap_resp.status_code == 200
        assert isinstance(snap_resp.json(), list)

        # Clear all passes via DELETE /api/v1/passes
        clear_resp = client.delete("/api/v1/passes")
        assert clear_resp.status_code == 200
        assert clear_resp.json()["status"] == "cleared"

        # Verify no passes remain
        resp3 = client.get("/api/v1/passes")
        assert resp3.status_code == 200
        assert len(resp3.json()) == 0

