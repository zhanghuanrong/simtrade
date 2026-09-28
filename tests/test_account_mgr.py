from datetime import datetime
from simtrade.utils import utc_now
import pytest
from simtrade.engine.account_mgr import AccountManager
from simtrade.models.market_data import Bar
from simtrade.models.order import OrderSide
from simtrade.models.trade import Trade


def test_buy_and_sell_realized_pnl():
    mgr = AccountManager(initial_capital=100_000.0)
    acc = mgr.get_or_create_account("trader_1")

    # Trade 1: Buy 100 AAPL @ $150
    t1 = Trade(
        order_id="o1",
        account_id="trader_1",
        ticker="AAPL",
        side=OrderSide.BUY,
        price=150.0,
        quantity=100.0,
        commission=1.0,
    )
    mgr.process_trade(t1)
    
    # Cash = 100,000 - 15,000 - 1 = 84,999
    assert acc.cash == 84_999.0
    assert acc.positions["AAPL"].quantity == 100.0
    assert acc.positions["AAPL"].avg_entry_price == 150.0

    # Trade 2: Sell 50 AAPL @ $170 (Gain $20/share on 50 shares = +$1,000)
    t2 = Trade(
        order_id="o2",
        account_id="trader_1",
        ticker="AAPL",
        side=OrderSide.SELL,
        price=170.0,
        quantity=50.0,
        commission=1.0,
    )
    mgr.process_trade(t2)

    assert acc.positions["AAPL"].quantity == 50.0
    assert acc.realized_pnl == 1000.0
    # Cash = 84,999 + 50*170 - 1 = 93,498
    assert acc.cash == 93_498.0


def test_mark_price_unrealized_pnl():
    mgr = AccountManager(initial_capital=100_000.0)
    acc = mgr.get_or_create_account("trader_1")

    t1 = Trade(
        order_id="o1",
        account_id="trader_1",
        ticker="AAPL",
        side=OrderSide.BUY,
        price=100.0,
        quantity=100.0,
    )
    mgr.process_trade(t1)

    # Bar arrives with close at $110
    bar = Bar(
        ticker="AAPL",
        timestamp=utc_now(),
        open=100.0,
        high=115.0,
        low=99.0,
        close=110.0,
        volume=1000.0,
    )
    mgr.update_mark_prices({"AAPL": bar})

    assert acc.positions["AAPL"].current_price == 110.0
    assert acc.unrealized_pnl == 1000.0  # 100 * (110 - 100)
    # Equity = Cash (0) + MV (11,000) = 101,000
    assert acc.equity == 101_000.0
