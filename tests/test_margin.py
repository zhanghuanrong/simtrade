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
