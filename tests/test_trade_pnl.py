import pytest
from simtrade.engine.account_mgr import AccountManager
from simtrade.models.order import OrderSide
from simtrade.models.trade import Trade
from simtrade.reporting.pass_store import enrich_trades_with_pnl, PassStore, PassRecord, PassSummary


def test_trade_cost_basis_and_pnl_long_sell():
    mgr = AccountManager(initial_capital=100_000.0)
    acc = mgr.get_or_create_account("test_acc")

    # Buy 100 shares @ $100.0
    t1 = Trade(
        order_id="ord-1",
        account_id="test_acc",
        ticker="XYZ",
        side=OrderSide.BUY,
        price=100.0,
        quantity=100.0,
        commission=1.0,
    )
    mgr.process_trade(t1)
    assert t1.cost_basis is None
    assert t1.realized_pnl is None

    # Sell 40 shares @ $120.0 (+ $20/share on 40 shares = +$800)
    t2 = Trade(
        order_id="ord-2",
        account_id="test_acc",
        ticker="XYZ",
        side=OrderSide.SELL,
        price=120.0,
        quantity=40.0,
        commission=1.0,
    )
    mgr.process_trade(t2)
    assert t2.cost_basis == 100.0
    assert t2.realized_pnl == 800.0
    assert t2.realized_pnl_pct == 20.0

    # Sell remaining 60 shares @ $90.0 (- $10/share on 60 shares = -$600)
    t3 = Trade(
        order_id="ord-3",
        account_id="test_acc",
        ticker="XYZ",
        side=OrderSide.SELL,
        price=90.0,
        quantity=60.0,
        commission=1.0,
    )
    mgr.process_trade(t3)
    assert t3.cost_basis == 100.0
    assert t3.realized_pnl == -600.0
    assert t3.realized_pnl_pct == -10.0


def test_trade_cost_basis_and_pnl_short_cover():
    mgr = AccountManager(initial_capital=100_000.0)
    acc = mgr.get_or_create_account("short_acc")

    # Short sell 50 shares @ $200.0
    t1 = Trade(
        order_id="ord-s1",
        account_id="short_acc",
        ticker="ABC",
        side=OrderSide.SELL_SHORT,
        price=200.0,
        quantity=50.0,
        commission=2.0,
    )
    mgr.process_trade(t1)

    # Cover 50 shares @ $180.0 (+ $20/share = +$1,000)
    t2 = Trade(
        order_id="ord-c1",
        account_id="short_acc",
        ticker="ABC",
        side=OrderSide.BUY,
        price=180.0,
        quantity=50.0,
        commission=2.0,
    )
    mgr.process_trade(t2)
    assert t2.cost_basis == 200.0
    assert t2.realized_pnl == 1000.0
    assert t2.realized_pnl_pct == 10.0


def test_enrich_trades_with_pnl_helper():
    raw_trades = [
        {"trade_id": "t1", "ticker": "LUNR", "side": "BUY", "price": 20.0, "quantity": 100.0, "timestamp": "2026-08-17T09:30:00"},
        {"trade_id": "t2", "ticker": "LUNR", "side": "BUY", "price": 30.0, "quantity": 100.0, "timestamp": "2026-08-17T09:35:00"},
        # Avg entry = (100*20 + 100*30) / 200 = 25.0
        {"trade_id": "t3", "ticker": "LUNR", "side": "SELL", "price": 35.0, "quantity": 100.0, "timestamp": "2026-08-17T10:00:00"},
        # Gain = (35 - 25) * 100 = 1000.0, pct = (35-25)/25 = +40%
        {"trade_id": "t4", "ticker": "LUNR", "side": "SELL", "price": 20.0, "quantity": 100.0, "timestamp": "2026-08-17T11:00:00"},
        # Loss = (20 - 25) * 100 = -500.0, pct = (20-25)/25 = -20%
    ]

    enriched = enrich_trades_with_pnl(raw_trades)

    assert enriched[0].get("realized_pnl") is None
    assert enriched[1].get("realized_pnl") is None

    assert enriched[2]["cost_basis"] == 25.0
    assert enriched[2]["realized_pnl"] == 1000.0
    assert enriched[2]["realized_pnl_pct"] == 40.0

    assert enriched[3]["cost_basis"] == 25.0
    assert enriched[3]["realized_pnl"] == -500.0
    assert enriched[3]["realized_pnl_pct"] == -20.0


def test_pass_store_export_csv_with_pnl(tmp_path):
    store = PassStore(storage_dir=str(tmp_path))
    rec = PassRecord(
        summary=PassSummary(pass_id="p1", account_id="a1", initial_capital=100000.0, ending_equity=101000.0),
        trades=[
            {"trade_id": "t1", "account_id": "a1", "order_id": "o1", "ticker": "AAPL", "side": "BUY", "price": 150.0, "quantity": 10.0, "notional": 1500.0, "commission": 1.0, "timestamp": "2026-08-17T09:30:00"},
            {"trade_id": "t2", "account_id": "a1", "order_id": "o2", "ticker": "AAPL", "side": "SELL", "price": 160.0, "quantity": 10.0, "notional": 1600.0, "commission": 1.0, "timestamp": "2026-08-17T10:00:00"},
        ]
    )
    store.save_pass(rec)
    csv_str = store.export_trades_csv("p1")
    assert "cost_basis,realized_pnl,realized_pnl_pct" in csv_str
    assert "150.0,100.0,6.67" in csv_str
