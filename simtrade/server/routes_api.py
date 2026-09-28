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
    """Retrieve details for a specific order."""
    sim = get_simulator()
    order = sim.matcher.active_orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found or already completed/cancelled")
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
class SpeedPayload(BaseModel):
    speed_multiplier: float


@api_router.post("/sim/start", tags=["Simulation Control"])
def start_simulation():
    """Start or resume continuous simulation replay."""
    sim = get_simulator()
    sim.start()
    return {"status": "started", "speed": sim.clock.speed_multiplier}


@api_router.post("/sim/pause", tags=["Simulation Control"])
def pause_simulation():
    """Pause continuous simulation replay."""
    sim = get_simulator()
    sim.pause()
    return {"status": "paused"}


@api_router.post("/sim/step", tags=["Simulation Control"])
async def step_simulation():
    """Manually advance simulation clock by 1 minute interval."""
    sim = get_simulator()
    result = await sim.step()
    return {"status": "stepped", **result}


@api_router.post("/sim/speed", tags=["Simulation Control"])
def set_simulation_speed(payload: SpeedPayload):
    """Change playback speed multiplier (e.g. 1.0=realtime, 60.0=1s/min, 0=instant)."""
    sim = get_simulator()
    sim.clock.set_speed(payload.speed_multiplier)
    return {"status": "speed_updated", "speed_multiplier": sim.clock.speed_multiplier}


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


# -------------------------------------------------------------
# Reporting & Tracing Endpoints
# -------------------------------------------------------------
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
