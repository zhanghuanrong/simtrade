import pytest
from simtrade.config import MarginConfig
from simtrade.engine.margin import MarginEngine
from simtrade.models.account import Account, Position
from simtrade.models.order import Order, OrderSide, OrderType


def test_margin_metrics_calculation():
    config = MarginConfig(initial_margin_rate=0.50, maintenance_margin_rate=0.25)
    engine = MarginEngine(config=config)

    account = Account(cash=50_000.0)
    # Add long position: 100 shares @ $200 = $20,000 market value
    account.positions["AAPL"] = Position(ticker="AAPL", quantity=100, current_price=200.0)

    metrics = engine.update_account_margin(account)
    # Equity = 50,000 + 20,000 = 70,000
    assert account.equity == 70_000.0
    # Initial margin req = 20,000 * 0.50 = 10,000
    assert metrics.initial_margin_requirement == 10_000.0
    # Maint margin req = 20,000 * 0.25 = 5,000
    assert metrics.maintenance_margin_requirement == 5_000.0
    assert not metrics.is_margin_call


def test_order_rejection_insufficient_margin():
    config = MarginConfig(initial_margin_rate=0.50)
    engine = MarginEngine(config=config)

    account = Account(cash=10_000.0, equity=10_000.0)
    # Order: 500 shares @ $100 = $50,000 notional.
    # Initial margin req = $25,000 > $10,000 equity -> should reject
    order = Order(
        ticker="TSLA",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=500,
    )
    valid, reason = engine.validate_order(account, order, estimated_price=100.0)
    assert not valid
    assert "Insufficient margin" in reason


def test_auto_liquidation_trigger():
    config = MarginConfig(maintenance_margin_rate=0.25, auto_liquidate_on_call=True)
    engine = MarginEngine(config=config)

    # Cash negative (margin loan): cash = -80,000
    # Position: 500 shares @ $170 = $85,000 market value
    # Equity = 85,000 - 80,000 = 5,000
    # Maintenance req = 85,000 * 0.25 = 21,250
    # Equity (5,000) < Maint req (21,250) -> MARGIN CALL!
    account = Account(cash=-80_000.0)
    account.positions["NVDA"] = Position(ticker="NVDA", quantity=500, current_price=170.0)

    liq_orders = engine.check_and_generate_liquidations(account, mark_prices={"NVDA": 170.0})
    assert len(liq_orders) == 1
    assert liq_orders[0].side == OrderSide.SELL
    assert liq_orders[0].quantity == 500


def test_market_order_slippage_buffer_via_simulator():
    from simtrade.engine.simulator import Simulator
    from simtrade.config import SimulationConfig
    from simtrade.models.order import OrderCreate

    sim = Simulator(sim_config=SimulationConfig(generate_synthetic_if_missing=True, speed_multiplier=0))
    # Setup cash account with $10,000 equity and 1x leverage (100% initial margin rate)
    sim.account_mgr.setup_account(
        req=pytest.importorskip("simtrade.models.account").AccountSetupRequest(
            account_id="buffer_trader",
            initial_total_equity=10_000.0,
            initial_margin_rate=1.0,  # 100% margin required, max BP = $10,000
        )
    )

    # Set mock bar price for AAPL to $100.0
    from simtrade.models.market_data import Bar
    from simtrade.utils import utc_now
    sim.latest_bars["AAPL"] = Bar(
        ticker="AAPL",
        timestamp=utc_now(),
        open=100.0,
        high=100.5,
        low=99.5,
        close=100.0,
        volume=50000.0,
    )

    # 1. 100 shares @ $100 = $10,000 notional.
    # With 5% default buffer, est_price = $105 -> req margin = $10,500 > $10,000 -> REJECTED
    order_overflow = sim.submit_order(
        OrderCreate(ticker="AAPL", side=OrderSide.BUY, quantity=100.0, order_type=OrderType.MARKET),
        account_id="buffer_trader",
    )
    assert order_overflow.status.value == "REJECTED"
    assert "Insufficient margin" in order_overflow.reject_reason

    # 2. 95 shares @ $100 = $9,500 notional.
    # With 5% buffer, est_price = $105 -> req margin = 95 * 105 = $9,975 <= $10,000 -> ACCEPTED & FILLED
    order_ok = sim.submit_order(
        OrderCreate(ticker="AAPL", side=OrderSide.BUY, quantity=95.0, order_type=OrderType.MARKET),
        account_id="buffer_trader",
    )
    assert order_ok.status.value in ("ACCEPTED", "FILLED")
    acc = sim.account_mgr.get_or_create_account("buffer_trader")
    # Verified: Actual cash deducted is at executed price (base + execution slippage bps), NOT the 5% margin buffer
    import asyncio
    asyncio.run(sim.step("buffer_trader"))
    trade = sim.all_trades[-1]
    expected_cash = 10_000.0 - (trade.quantity * trade.price) - trade.commission
    assert acc.cash == pytest.approx(expected_cash, abs=0.01)


def test_market_order_custom_buffer_override():
    from simtrade.engine.simulator import Simulator
    from simtrade.config import SimulationConfig
    from simtrade.models.order import OrderCreate
    from simtrade.models.account import AccountSetupRequest
    from simtrade.models.market_data import Bar
    from simtrade.utils import utc_now

    sim = Simulator(sim_config=SimulationConfig(generate_synthetic_if_missing=True, speed_multiplier=0))
    # Setup account with custom 2% buffer override
    sim.account_mgr.setup_account(
        req=AccountSetupRequest(
            account_id="custom_buf_trader",
            initial_total_equity=10_000.0,
            initial_margin_rate=1.0,
            market_order_slippage_buffer=0.02,  # 2% buffer instead of 5%
        )
    )

    sim.latest_bars["AAPL"] = Bar(
        ticker="AAPL",
        timestamp=utc_now(),
        open=100.0,
        high=100.5,
        low=99.5,
        close=100.0,
        volume=50000.0,
    )

    # 98 shares @ $100 with 2% buffer: 98 * 102 = $9,996 <= $10,000 -> ACCEPTED
    order_ok = sim.submit_order(
        OrderCreate(ticker="AAPL", side=OrderSide.BUY, quantity=98.0, order_type=OrderType.MARKET),
        account_id="custom_buf_trader",
    )
    assert order_ok.status.value in ("ACCEPTED", "FILLED")


def test_internal_leverage_boost_hidden_from_client():
    from simtrade.engine.simulator import Simulator
    from simtrade.config import SimulationConfig
    from simtrade.models.account import AccountSetupRequest
    from simtrade.models.order import Order, OrderSide, OrderType

    sim = Simulator(sim_config=SimulationConfig(generate_synthetic_if_missing=True, speed_multiplier=0))
    # Client sets up with leverage = 2.0
    acc = sim.account_mgr.setup_account(
        req=AccountSetupRequest(
            account_id="boost_trader",
            initial_total_equity=100_000.0,
            leverage=2.0,
        )
    )

    # 1. Client-facing metrics MUST show requested leverage = 2.0
    assert acc.custom_margin_config is not None
    assert acc.custom_margin_config["max_leverage"] == 2.0
    assert acc.custom_margin_config["initial_margin_rate"] == 0.50
    # Buying power visible to client is based on 2.0x ($200,000)
    assert acc.margin.buying_power == 200_000.0

    # 2. Client serialization hides internal boost
    dumped = acc.model_dump()
    assert "_internal_margin_config" not in dumped
    assert dumped["custom_margin_config"]["max_leverage"] == 2.0

    # 3. Internally, leverage is boosted by +1.0 (2.0 + 1.0 = 3.0)
    assert acc.internal_margin_config["max_leverage"] == 3.0
    assert acc.internal_margin_config["initial_margin_rate"] == pytest.approx(1.0 / 3.0)

    # 4. An order requiring 2.5x leverage ($250,000 notional) succeeds internally
    # (Under 2.0x it would require $125k margin and fail, under 3.0x it requires $83.33k and passes)
    order = Order(
        account_id="boost_trader",
        ticker="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=2500,
        limit_price=100.0,
    )
    valid, reason = sim.account_mgr.reserve_for_order("boost_trader", order, estimated_price=100.0)
    assert valid is True
    assert acc.frozen_cash == pytest.approx(250_000.0 / 3.0, abs=0.01)

