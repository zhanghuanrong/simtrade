from datetime import datetime
from simtrade.utils import utc_now
import pytest
from simtrade.config import MatchingConfig
from simtrade.engine.matcher import MatchingEngine
from simtrade.models.market_data import Bar
from simtrade.models.order import Order, OrderSide, OrderStatus, OrderType, TimeInForce


def test_market_buy_execution():
    config = MatchingConfig(slippage_bps=10.0, commission_per_share=0.01, min_commission=1.0)
    matcher = MatchingEngine(config=config)

    order = Order(
        ticker="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=100,
    )
    matcher.add_order(order)

    bar = Bar(
        ticker="AAPL",
        timestamp=utc_now(),
        open=200.0,
        high=205.0,
        low=199.0,
        close=202.0,
        volume=10000.0,
    )

    matches = matcher.match_bar(bar)
    assert len(matches) == 1
    filled_order, trade = matches[0]
    
    assert filled_order.status == OrderStatus.FILLED
    # 200 * (1 + 0.001) = 200.2
    assert trade.price == 200.2
    assert trade.quantity == 100
    assert trade.commission == 1.0  # max(1.0, 100 * 0.01)


def test_limit_buy_unfilled_and_filled():
    matcher = MatchingEngine()
    order = Order(
        ticker="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=50,
        limit_price=195.0,
    )
    matcher.add_order(order)

    # Bar doesn't reach limit price (low is 196.0)
    bar1 = Bar(
        ticker="AAPL",
        timestamp=utc_now(),
        open=200.0,
        high=202.0,
        low=196.0,
        close=198.0,
        volume=5000.0,
    )
    matches1 = matcher.match_bar(bar1)
    assert len(matches1) == 0
    assert order.status == OrderStatus.ACCEPTED

    # Next bar dips to 194.0 -> fills!
    bar2 = Bar(
        ticker="AAPL",
        timestamp=utc_now(),
        open=197.0,
        high=198.0,
        low=194.0,
        close=195.0,
        volume=5000.0,
    )
    matches2 = matcher.match_bar(bar2)
    assert len(matches2) == 1
    assert matches2[0][0].status == OrderStatus.FILLED
    assert matches2[0][1].price == 195.0


def test_volume_share_cap_partial_fill():
    config = MatchingConfig(max_volume_share=0.10)  # max 10% of bar volume
    matcher = MatchingEngine(config=config)

    order = Order(
        ticker="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1000,
        time_in_force=TimeInForce.GTC,
    )
    matcher.add_order(order)

    # Bar volume = 5000. 10% is 500 shares max executable.
    bar = Bar(
        ticker="AAPL",
        timestamp=utc_now(),
        open=150.0,
        high=152.0,
        low=149.0,
        close=151.0,
        volume=5000.0,
    )
    matches = matcher.match_bar(bar)
    assert len(matches) == 1
    ord_res, trade = matches[0]
    assert ord_res.status == OrderStatus.PARTIALLY_FILLED
    assert ord_res.filled_quantity == 500
    assert ord_res.remaining_quantity == 500
    assert trade.quantity == 500
