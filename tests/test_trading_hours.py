"""Tests for Regular Trading Hours (RTH) enforcement."""

from datetime import datetime, timezone
import pytest
from zoneinfo import ZoneInfo

from simtrade.config import SimulationConfig
from simtrade.engine.simulator import Simulator
from simtrade.models.order import OrderCreate, OrderSide, OrderStatus, OrderType
from simtrade.utils import is_regular_trading_hours, to_eastern_time

NY_TZ = ZoneInfo("America/New_York")


def test_is_regular_trading_hours_utility():
    # 1. Regular market hours (Monday 09:30 - 16:00 ET)
    open_time = datetime(2026, 8, 17, 13, 30, tzinfo=timezone.utc)  # 09:30 EDT
    mid_day = datetime(2026, 8, 17, 18, 0, tzinfo=timezone.utc)    # 14:00 EDT
    close_time = datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc)  # 16:00 EDT
    
    assert is_regular_trading_hours(open_time)[0] is True
    assert is_regular_trading_hours(mid_day)[0] is True
    assert is_regular_trading_hours(close_time)[0] is True

    # 2. Pre-market (09:29 EDT)
    pre_mkt = datetime(2026, 8, 17, 13, 29, tzinfo=timezone.utc)
    ok, reason = is_regular_trading_hours(pre_mkt)
    assert ok is False
    assert "Pre-market trading is disabled" in reason

    # 3. Post-market (16:01 EDT)
    post_mkt = datetime(2026, 8, 17, 20, 1, tzinfo=timezone.utc)
    ok, reason = is_regular_trading_hours(post_mkt)
    assert ok is False
    assert "Post-market trading is disabled" in reason

    # 4. Overnight (23:00 EDT)
    overnight = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
    ok, reason = is_regular_trading_hours(overnight)
    assert ok is False
    assert "Overnight trading is disabled" in reason

    # 5. Weekend (Saturday)
    weekend = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
    ok, reason = is_regular_trading_hours(weekend)
    assert ok is False
    assert "Market is closed on weekends (Saturday)" in reason


def test_order_rejection_outside_regular_trading_hours():
    sim = Simulator(sim_config=SimulationConfig(generate_synthetic_if_missing=True, speed_multiplier=0))

    # Advance clock to after-hours: 16:30 ET on Monday 2026-08-17 (20:30 UTC)
    after_hours = datetime(2026, 8, 17, 20, 30, tzinfo=timezone.utc)
    sim.clock.set_time(after_hours, reason="TEST_AFTER_HOURS")

    order = sim.submit_order(
        OrderCreate(
            ticker="AAPL",
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
        ),
        account_id="trader_1",
    )

    assert order.status == OrderStatus.REJECTED
    assert "Market is closed" in order.reject_reason
    assert "Post-market trading is disabled" in order.reject_reason

    # Verify not placed in active orders book
    assert order.order_id not in sim.matcher.active_orders
    # Verify present in historical order registry
    assert order.order_id in sim.matcher.all_orders


def test_order_accepted_during_regular_trading_hours():
    sim = Simulator(sim_config=SimulationConfig(generate_synthetic_if_missing=True, speed_multiplier=0))
    # Set clock to 10:30 ET on a trading day (Monday 2026-08-17 10:30 EDT)
    rth_time = datetime(2026, 8, 17, 10, 30, tzinfo=NY_TZ)
    sim.clock.set_time(rth_time, reason="TEST_RTH")

    order = sim.submit_order(
        OrderCreate(
            ticker="AAPL",
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
        ),
        account_id="trader_1",
    )

    assert order.status == OrderStatus.ACCEPTED
    assert order.order_id in sim.matcher.active_orders
    # Virtual sim time is mapped by server to current clock ET
    assert order.trading_time == rth_time
    assert order.sim_created_at == rth_time


def test_order_server_time_mapping_rejects_closed_hours():
    sim = Simulator(sim_config=SimulationConfig(generate_synthetic_if_missing=True, speed_multiplier=0))
    # Clock is at 09:15 ET (pre-market)
    pre_market = datetime(2026, 8, 17, 9, 15, tzinfo=NY_TZ)
    sim.clock.set_time(pre_market, reason="TEST_PRE_MARKET")

    order = sim.submit_order(
        OrderCreate(
            ticker="AAPL",
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
        ),
        account_id="trader_1",
    )

    assert order.status == OrderStatus.REJECTED
    assert "Pre-market trading is disabled" in order.reject_reason
    assert order.trading_time == pre_market


