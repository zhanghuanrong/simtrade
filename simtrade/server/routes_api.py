"""REST API routes for SimTrade server."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from simtrade.engine.simulator import Simulator
from simtrade.models.account import Account, Position
from simtrade.models.market_data import Bar
from simtrade.models.order import Order, OrderCreate, OrderStatus
from simtrade.models.trade import Trade
from simtrade.reporting.performance import PerformanceAnalytics
from simtrade.reporting.pass_store import PassRecord, PassSummary
from simtrade.reporting.ledger import AccountSnapshot

api_router = APIRouter(prefix="/api/v1")

# Reference to simulator instance injected during startup
simulator: Optional[Simulator] = None


def get_simulator() -> Simulator:
    if simulator is None:
        raise HTTPException(status_code=500, detail="Simulator is not initialized")
    return simulator


# -------------------------------------------------------------
# Account & Portfolio Endpoints
# -------------------------------------------------------------
@api_router.get("/account", response_model=Account, tags=["Account"])
def get_account(account_id: str = "trader_1"):
    """Get complete account ledger, equity, cash, and margin health."""
    sim = get_simulator()
    return sim.account_mgr.get_or_create_account(account_id)


@api_router.get("/account/snapshots", response_model=List[AccountSnapshot], tags=["Account"])
def get_account_snapshots(account_id: str = "trader_1"):
    """Get historical equity and portfolio snapshots for an active account."""
    sim = get_simulator()
    return sim.ledger.get_snapshots(account_id)


@api_router.get("/positions", response_model=Dict[str, Position], tags=["Account"])
def get_positions(account_id: str = "trader_1"):
    """Get active long and short positions."""
    sim = get_simulator()
    acc = sim.account_mgr.get_or_create_account(account_id)
    return acc.positions


# -------------------------------------------------------------
# Order Management Endpoints
# -------------------------------------------------------------
@api_router.post("/orders", response_model=Order, status_code=status.HTTP_201_CREATED, tags=["Orders"])
def create_order(payload: OrderCreate, account_id: str = "trader_1"):
    """Submit a new order to the matching engine."""
    sim = get_simulator()
    order = sim.submit_order(payload, account_id=account_id)
    if order.status == OrderStatus.REJECTED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=order.reject_reason)
    return order


@api_router.get("/orders", response_model=List[Order], tags=["Orders"])
def list_active_orders(account_id: Optional[str] = None):
    """List currently active unfilled orders."""
    sim = get_simulator()
    orders = list(sim.matcher.active_orders.values())
    if account_id:
        orders = [o for o in orders if o.account_id == account_id]
    return orders


@api_router.get("/orders/{order_id}", response_model=Order, tags=["Orders"])
def get_order(order_id: str):
    """Retrieve details for a specific order (active, filled, cancelled, or rejected)."""
    sim = get_simulator()
    order = sim.matcher.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@api_router.delete("/orders/{order_id}", response_model=Order, tags=["Orders"])
def cancel_order(order_id: str, account_id: str = "trader_1"):
    """Cancel an active order."""
    sim = get_simulator()
    order = sim.cancel_order(order_id, account_id=account_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found or cannot be cancelled")
    return order


@api_router.get("/trades", response_model=List[Trade], tags=["Orders"])
def list_trades(account_id: Optional[str] = None, limit: int = 100):
    """List trade execution history."""
    sim = get_simulator()
    trades = sim.all_trades
    if account_id:
        trades = [t for t in trades if t.account_id == account_id]
    return trades[-limit:]


# -------------------------------------------------------------
# Market Data Endpoints
# -------------------------------------------------------------
@api_router.get("/market/bars/latest", response_model=Dict[str, Bar], tags=["Market Data"])
def get_latest_bars():
    """Get the most recent 1m OHLCV bar for all active tickers."""
    sim = get_simulator()
    return sim.latest_bars


@api_router.get("/market/tickers", response_model=List[str], tags=["Market Data"])
def get_tickers():
    """List tickers configured in the simulator."""
    sim = get_simulator()
    return sim.sim_config.tickers


# -------------------------------------------------------------
# Simulation Control Endpoints
# -------------------------------------------------------------
@api_router.post("/sim/start", tags=["Simulation Control"])
def start_simulation():
    """Start or resume continuous simulation replay at 1.0x baseline speed."""
    sim = get_simulator()
    sim.start()
    return {"status": "started", "speed": 1.0}


class StepUntilPayload(BaseModel):
    target_time: str
    account_id: str = "trader_1"


@api_router.post("/sim/step_until", tags=["Simulation Control"])
async def step_until_target(payload: StepUntilPayload):
    """
    Advance simulation from last T_anchor to target_time.
    If target_time <= current_time, request is ignored.
    If liquidation triggered mid-way, stops early and returns liquidation status and updated timestamp.
    If target_time is in a non-trading gap, returns empty bars with success status.
    """
    from datetime import datetime
    sim = get_simulator()
    try:
        target_dt = datetime.fromisoformat(payload.target_time)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid ISO datetime format: {e}")
    return await sim.step_until(target_dt, account_id=payload.account_id)


@api_router.get("/sim/status", tags=["Simulation Control"])
def get_simulation_status():
    """Get current simulation clock, playback state, and statistics."""
    sim = get_simulator()
    return {
        "current_time": sim.clock.current_time.isoformat(),
        "is_running": sim.clock.is_running,
        "is_paused": sim.clock.is_paused,
        "step_count": sim.clock.step_count,
        "speed_multiplier": sim.clock.speed_multiplier,
        "tickers": sim.sim_config.tickers,
        "active_orders_count": len(sim.matcher.active_orders),
        "total_trades_count": len(sim.all_trades),
    }


@api_router.get("/sim/metadata", tags=["Simulation Control"])
def get_simulation_metadata():
    """Get full simulation time range, total bars, cursor, progress, and available tickers."""
    sim = get_simulator()
    return sim.get_metadata()


class ResetPayload(BaseModel):
    start_time: Optional[str] = None


@api_router.post("/sim/reset", tags=["Simulation Control"])
def reset_simulation(payload: Optional[ResetPayload] = None):
    """Reset simulation playback cursor to start a fresh simulation run/pass."""
    from datetime import datetime
    sim = get_simulator()
    st = datetime.fromisoformat(payload.start_time) if payload and payload.start_time else None
    sim.reset(start_time=st)
    return {"status": "reset", "current_time": sim.clock.current_time.isoformat()}


# -------------------------------------------------------------
# Account Management & Negotiation Endpoints
# -------------------------------------------------------------
from simtrade.models.account import AccountSetupRequest, AccountSummary


@api_router.get("/accounts", response_model=List[AccountSummary], tags=["Account"])
def list_accounts():
    """List all configured accounts and simulation passes."""
    sim = get_simulator()
    return sim.account_mgr.list_accounts()


@api_router.post("/account/setup", response_model=Account, tags=["Account"])
def setup_account(payload: AccountSetupRequest):
    """Negotiate and configure initial account balance, positions, leverage, and margin terms."""
    sim = get_simulator()
    mark_prices = {t: b.close for t, b in sim.latest_bars.items()}
    if not mark_prices and hasattr(sim, "feeder") and sim.feeder:
        cur_bars = sim.feeder.get_bars_for_time(sim.clock.current_time)
        mark_prices = {t: b.close for t, b in cur_bars.items()}
        if not mark_prices and sim.feeder.timeline:
            first_bars = sim.feeder.get_bars_for_time(sim.feeder.timeline[0])
            mark_prices = {t: b.close for t, b in first_bars.items()}
    return sim.account_mgr.setup_account(payload, mark_prices=mark_prices)


# -------------------------------------------------------------
# Reporting & Tracing Endpoints
# -------------------------------------------------------------
from fastapi.responses import Response


@api_router.get("/reports/performance", tags=["Reporting"])
def get_performance_report(account_id: str = "trader_1"):
    """Compute and return Sharpe ratio, max drawdown, win rate, and return metrics."""
    sim = get_simulator()
    snapshots = sim.ledger.get_snapshots(account_id)
    trades = [t for t in sim.all_trades if t.account_id == account_id]
    acc = sim.account_mgr.get_or_create_account(account_id)
    return PerformanceAnalytics.calculate(snapshots, trades, initial_capital=acc.initial_capital)


@api_router.get("/reports/ledger", tags=["Reporting"])
def get_ledger_entries(account_id: Optional[str] = None, event_type: Optional[str] = None, limit: int = 100):
    """Retrieve audit trail of transaction and risk events."""
    sim = get_simulator()
    return sim.ledger.get_entries(account_id=account_id, event_type=event_type, limit=limit)


@api_router.get("/reports/trades/csv", tags=["Reporting"])
def download_trades_csv(account_id: Optional[str] = None):
    """Export and download trades in CSV format."""
    sim = get_simulator()
    csv_content = sim.export_trades_csv(account_id=account_id)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=trades_{account_id or 'all'}.csv"},
    )


class SaveReportPayload(BaseModel):
    account_id: str = "trader_1"
    output_dir: str = "reports"


@api_router.post("/reports/save", tags=["Reporting"])
def save_simulation_session(payload: SaveReportPayload):
    """Save trades CSV, performance JSON, and audit ledger JSON to server reports directory."""
    sim = get_simulator()
    files = sim.save_session(account_id=payload.account_id, output_dir=payload.output_dir)
    return {"status": "saved", "account_id": payload.account_id, "files": files}


# -------------------------------------------------------------
# Finished Passes Endpoints
# -------------------------------------------------------------
@api_router.get("/passes", response_model=List[PassSummary], tags=["Passes"])
def list_simulation_passes():
    """List all finished simulation passes stored in memory or on disk."""
    sim = get_simulator()
    return sim.pass_store.list_passes()


@api_router.get("/passes/{pass_id}", response_model=PassRecord, tags=["Passes"])
def get_simulation_pass(pass_id: str):
    """Retrieve full details, metrics, equity snapshots, positions, and trades for a finished pass."""
    sim = get_simulator()
    record = sim.pass_store.get_pass(pass_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Pass '{pass_id}' not found")
    return record


@api_router.get("/passes/{pass_id}/trades/csv", tags=["Passes"])
def download_pass_trades_csv(pass_id: str):
    """Export and download trades of a finished pass in CSV format."""
    sim = get_simulator()
    record = sim.pass_store.get_pass(pass_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Pass '{pass_id}' not found")
    csv_content = sim.pass_store.export_trades_csv(pass_id)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=trades_{pass_id}.csv"},
    )

