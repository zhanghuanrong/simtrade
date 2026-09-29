"""End-to-end fake trading client replaying historical paper trading events against SimTrade server."""

import argparse
import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

from simtrade.client.trader_client import SimTradeClient, TraderClient
from simtrade.models.account import Account
from simtrade.models.order import Order, OrderSide, OrderStatus, OrderType
from simtrade.models.trade import Trade

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("e2e_fake_trading_client")


async def run_fake_trading(
    history_file: str,
    server_url: str = "http://127.0.0.1:6688",
    account_id: str = "fake_e2e_pass",
    tag: str = "e2e_history_replay",
    export_csv: Optional[str] = None,
    reports_dir: str = "reports",
    leverage: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Connect to SimTrade server and replay trading events from a history JSON file.
    
    Returns a dictionary of execution results, metrics, and summary.
    """
    history_path = Path(history_file)
    if not history_path.exists():
        raise FileNotFoundError(f"History file not found: {history_file}")

    with open(history_path, "r") as f:
        history_data = json.load(f)

    events: List[Dict[str, Any]] = history_data.get("events", [])
    account_cfg = history_data.get("account_configuration", {})
    init_capital = float(account_cfg.get("initial_capital", 100_000.0))
    if leverage is not None:
        pass_leverage = float(leverage)
    else:
        pass_leverage = float(account_cfg.get("max_intraday_leverage", 2.0))

    logger.info(f"Loaded {len(events)} events from {history_file}")
    logger.info(f"Account config: capital=${init_capital:,.2f}, leverage={pass_leverage}x")

    # Pre-build lookup for order shares and identify successful vs unsuccessful orders
    filled_order_ids: set[str] = set()
    order_to_filled_shares: Dict[str, float] = {}
    order_to_shares: Dict[str, float] = {}

    for e in events:
        etype = e.get("event_type")
        oid = e.get("order_id")
        if not oid:
            continue

        if "shares" in e and e["shares"] is not None:
            order_to_shares[oid] = float(e["shares"])

        if etype == "ORDER_FILLED":
            filled_order_ids.add(oid)
            fill_shares = float(e.get("shares", 0.0) or 0.0)
            order_to_filled_shares[oid] = order_to_filled_shares.get(oid, 0.0) + fill_shares
        elif etype in ("REBALANCE_EXIT", "EOD_DELEVERAGE_TRIM", "STOP_LOSS_TRIGGERED"):
            # These are executed trades directly recorded in historical log
            if e.get("price") is not None or (e.get("shares") is not None and float(e["shares"]) > 0):
                filled_order_ids.add(oid)
                if oid not in order_to_filled_shares and e.get("shares") is not None:
                    order_to_filled_shares[oid] = float(e["shares"])

    logger.info(
        f"Order analysis: {len(filled_order_ids)} successful orders identified with fills/executions in history. "
        f"All unfilled or totally cancelled orders will be dropped."
    )

    # Group actionable events by timestamp in chronological order
    ts_groups: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        ts = e.get("timestamp")
        if ts:
            ts_groups.setdefault(ts, []).append(e)

    sorted_timestamps = sorted(ts_groups.keys())
    logger.info(f"Found {len(sorted_timestamps)} distinct trading timestamps to replay")

    client = TraderClient(base_url=server_url, account_id=account_id)
    await client.connect(with_ws=False)

    client_order_map: Dict[str, str] = {}  # history_order_id -> server_order_id
    total_orders_submitted = 0
    total_orders_cancelled = 0
    total_orders_rejected = 0
    total_orders_dropped = 0

    try:
        # Reset simulator state to start fresh replay
        await client.reset_sim()

        # 1. Setup simulation account for this pass
        logger.info(f"Negotiating account setup for '{account_id}' (tag: '{tag}')...")
        account = await client.setup_account(
            account_id=account_id,
            tag=tag,
            initial_total_equity=init_capital,
            leverage=pass_leverage,
        )
        logger.info(f"Account initialized: Equity=${account.equity:,.2f}, Cash=${account.cash:,.2f}")

        # 2. Iterate chronologically through event timestamps
        for step_idx, ts_str in enumerate(sorted_timestamps, start=1):
            ts_dt = datetime.fromisoformat(ts_str)

            # Advance simulation virtual time to event timestamp
            step_resp = await client.step_until(ts_dt, account_id=account_id)
            if step_resp.get("status") == "LIQUIDATION_TRIGGERED":
                logger.warning(f"Margin liquidation triggered at {step_resp.get('current_time')}!")

            # Replay all events at this exact timestamp in original sequence
            for e in ts_groups[ts_str]:
                etype = e.get("event_type")
                oid = e.get("order_id")
                symbol = e.get("symbol")

                if etype in ("ORDER_PLACED", "REBALANCE_EXIT", "EOD_DELEVERAGE_TRIM", "STOP_LOSS_TRIGGERED"):
                    # Drop unsuccessful orders that were totally cancelled or never filled in history
                    if oid and oid not in filled_order_ids:
                        logger.info(
                            f"Dropping unsuccessful/cancelled order {oid} ({symbol}) - no fills in history"
                        )
                        total_orders_dropped += 1
                        continue

                    raw_action = str(e.get("action", "BUY")).upper()
                    if raw_action == "BUY":
                        side = OrderSide.BUY
                    elif raw_action == "SELL_SHORT":
                        side = OrderSide.SELL_SHORT
                    else:
                        side = OrderSide.SELL

                    # Resolve share count - use exact filled shares from history if available
                    shares = order_to_filled_shares.get(oid) if oid else None
                    if shares is None:
                        shares = e.get("shares")
                    if shares is None and oid:
                        shares = order_to_shares.get(oid)
                    if shares is None and e.get("target_weight") and e.get("limit_price"):
                        shares = round(float(e["target_weight"]) * init_capital / float(e["limit_price"]), 4)
                    if shares is None or float(shares) <= 0:
                        shares = 100.0
                    else:
                        shares = float(shares)

                    # Determine order type and limit price
                    raw_otype = str(e.get("order_type", "MARKET")).upper()
                    limit_price = e.get("limit_price")
                    if limit_price is not None:
                        limit_price = float(limit_price)

                    # Treat orders with limit_price as LIMIT orders to respect caps
                    if raw_otype == "LIMIT" or (limit_price is not None and etype == "ORDER_PLACED"):
                        otype = OrderType.LIMIT
                    else:
                        otype = OrderType.MARKET

                    try:
                        submitted_order = await client.submit_order(
                            ticker=symbol,
                            side=side,
                            quantity=shares,
                            order_type=otype,
                            limit_price=limit_price,
                            client_order_id=oid,
                        )
                        total_orders_submitted += 1
                        if oid:
                            client_order_map[oid] = submitted_order.order_id

                        if submitted_order.status == OrderStatus.REJECTED:
                            total_orders_rejected += 1
                            logger.warning(
                                f"Order {oid} ({symbol} {side.value} {shares}) REJECTED: "
                                f"{submitted_order.reject_reason}"
                            )
                        else:
                            logger.debug(
                                f"Order {oid} ({symbol} {side.value} {shares}) ACCEPTED as {submitted_order.order_id}"
                            )
                    except Exception as err:
                        logger.error(f"Error submitting order {oid}: {err}")

                elif etype == "ORDER_CANCELLED":
                    # If this order was dropped as unsuccessful / cancelled with no fills, completely ignore it
                    if oid and oid not in filled_order_ids:
                        logger.debug(f"Dropping cancellation for dropped order {oid}")
                        continue

                    if oid and oid in client_order_map:
                        srv_id = client_order_map[oid]
                        try:
                            await client.cancel(srv_id)
                            total_orders_cancelled += 1
                            logger.debug(f"Cancelled order {oid} ({srv_id})")
                        except Exception as err:
                            # Already filled or closed, non-fatal
                            logger.debug(f"Order cancellation note for {oid}: {err}")

        # 3. Step simulation to completion / end of dataset timeline
        try:
            meta = await client.get_metadata()
            end_time = meta.get("end_time")
            if end_time:
                logger.info(f"Advancing simulation to timeline end: {end_time}...")
                await client.step_until(end_time, account_id=account_id)
        except Exception as err:
            logger.warning(f"Could not advance to final timeline end: {err}")

        # 4. Fetch final account state and executed trades
        final_account = await client.get_account()
        trades = await client.get_trades(limit=5000)

        # 5. Export session reports and CSV
        saved_reports = await client.save_session(account_id=account_id, output_dir=reports_dir)
        csv_path = None
        if export_csv:
            csv_path = str(Path(export_csv).resolve())
            await client.export_trades_csv(account_id=account_id, save_path=csv_path)
            logger.info(f"Trades CSV exported to {csv_path}")

        net_profit = round(final_account.equity - init_capital, 2)
        total_return_pct = round((net_profit / init_capital) * 100.0, 2)

        summary = {
            "status": "COMPLETED",
            "account_id": account_id,
            "tag": tag,
            "initial_capital": init_capital,
            "ending_equity": final_account.equity,
            "net_profit": net_profit,
            "total_return_pct": total_return_pct,
            "cash": final_account.cash,
            "realized_pnl": final_account.realized_pnl,
            "unrealized_pnl": final_account.unrealized_pnl,
            "total_orders_submitted": total_orders_submitted,
            "total_orders_cancelled": total_orders_cancelled,
            "total_orders_rejected": total_orders_rejected,
            "total_orders_dropped": total_orders_dropped,
            "total_trades_executed": len(trades),
            "open_positions_count": len(final_account.positions),
            "open_positions": {t: p.quantity for t, p in final_account.positions.items()},
            "reports_saved": saved_reports,
            "export_csv": csv_path,
        }

        # Print formatted summary table
        print_summary_table(summary)
        return summary

    finally:
        await client.close()


def print_summary_table(summary: Dict[str, Any]):
    """Print an attractive summary table to stdout."""
    sep = "=" * 65
    print("\n" + sep)
    print("             SIMTRADE E2E FAKE TRADING SUMMARY             ")
    print(sep)
    print(f" Account ID:            {summary['account_id']} ({summary['tag']})")
    print(f" Initial Capital:       ${summary['initial_capital']:,.2f}")
    print(f" Ending Total Equity:   ${summary['ending_equity']:,.2f}")
    profit_sign = "+" if summary['net_profit'] >= 0 else ""
    print(f" Total Net Profit:      {profit_sign}${summary['net_profit']:,.2f} ({profit_sign}{summary['total_return_pct']}%)")
    print(f" Cash Balance:          ${summary['cash']:,.2f}")
    print(f" Realized PnL:          ${summary['realized_pnl']:,.2f}")
    print(f" Unrealized PnL:        ${summary['unrealized_pnl']:,.2f}")
    print("-" * 65)
    print(f" Orders Submitted:      {summary['total_orders_submitted']}")
    print(f" Orders Cancelled:      {summary['total_orders_cancelled']}")
    print(f" Orders Rejected:       {summary['total_orders_rejected']}")
    print(f" Orders Dropped (Unsuccessful): {summary.get('total_orders_dropped', 0)}")
    print(f" Trades Executed:       {summary['total_trades_executed']}")
    print(f" Open Positions Count:  {summary['open_positions_count']}")
    if summary['open_positions']:
        pos_details = ", ".join(f"{t}: {q:.1f}" for t, q in summary['open_positions'].items())
        print(f" Positions:             {pos_details}")
    if summary.get("export_csv"):
        print(f" Trades Export CSV:     {summary['export_csv']}")
    print(sep + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="E2E Fake Trading Client: Replay trading history events against SimTrade server."
    )
    parser.add_argument(
        "--history",
        type=str,
        default="data/test_checker_trading_history.json",
        help="Path to paper trading history JSON file (default: data/test_checker_trading_history.json)",
    )
    parser.add_argument(
        "--server",
        type=str,
        default="http://127.0.0.1:6688",
        help="SimTrade server base URL (default: http://127.0.0.1:6688)",
    )
    parser.add_argument(
        "--account-id",
        type=str,
        default="fake_e2e_pass",
        help="Account identifier for the simulation pass (default: fake_e2e_pass)",
    )
    parser.add_argument(
        "--tag",
        type=str,
        default="e2e_history_replay",
        help="Tag describing the run pass (default: e2e_history_replay)",
    )
    parser.add_argument(
        "--export-csv",
        type=str,
        default=None,
        help="Optional local filepath to save trade history as CSV",
    )
    parser.add_argument(
        "--reports-dir",
        type=str,
        default="reports",
        help="Directory to save simulation reports (default: reports)",
    )
    parser.add_argument(
        "--leverage",
        type=float,
        default=2.0,
        help="Leverage override for this pass (default: 2.0)",
    )

    args = parser.parse_args()

    try:
        summary = asyncio.run(
            run_fake_trading(
                history_file=args.history,
                server_url=args.server,
                account_id=args.account_id,
                tag=args.tag,
                export_csv=args.export_csv,
                reports_dir=args.reports_dir,
                leverage=args.leverage,
            )
        )
        sys.exit(0 if summary["status"] == "COMPLETED" else 1)
    except Exception as e:
        logger.error(f"E2E simulation execution failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
