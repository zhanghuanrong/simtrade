"""Example Algorithmic Paper Trading Bot using SimTrade Python Client SDK."""

import asyncio
from collections import deque
from typing import Dict
from rich.console import Console

from simtrade.client.trader_client import SimTradeClient
from simtrade.models.market_data import Bar
from simtrade.models.order import Order, OrderType
from simtrade.models.trade import Trade

console = Console()


async def run_bot():
    client = SimTradeClient(base_url="http://127.0.0.1:6688", account_id="trader_1")
    await client.connect()

    price_history: Dict[str, deque] = {"AAPL": deque(maxlen=5), "NVDA": deque(maxlen=5)}
    position_state: Dict[str, int] = {"AAPL": 0, "NVDA": 0}

    @client.on_bar
    async def on_bars(bars: Dict[str, Bar]):
        for ticker in ["AAPL", "NVDA"]:
            bar = bars.get(ticker)
            if not bar:
                continue

            history = price_history[ticker]
            history.append(bar.close)

            if len(history) < 5:
                continue

            sma = sum(history) / len(history)
            current_price = bar.close
            pos = position_state[ticker]

            # Simple Momentum Rule:
            # If current price crosses above SMA and not long -> BUY
            # If current price crosses below SMA and long -> CLOSE / SELL
            if current_price > sma and pos <= 0:
                console.print(f"[bold green]Signal: BUY {ticker}[/bold green] (Price: ${current_price:.2f} > SMA: ${sma:.2f})")
                try:
                    await client.buy(ticker=ticker, quantity=20, order_type=OrderType.MARKET)
                    position_state[ticker] = 20
                except Exception as e:
                    console.print(f"[red]Order failed: {e}[/red]")

            elif current_price < sma and pos > 0:
                console.print(f"[bold red]Signal: SELL {ticker}[/bold red] (Price: ${current_price:.2f} < SMA: ${sma:.2f})")
                try:
                    await client.sell(ticker=ticker, quantity=20, order_type=OrderType.MARKET)
                    position_state[ticker] = 0
                except Exception as e:
                    console.print(f"[red]Order failed: {e}[/red]")

    @client.on_trade
    async def on_trade(trade: Trade):
        console.print(f"[bold cyan]Trade Executed:[/bold cyan] {trade.side.value} {trade.quantity} {trade.ticker} @ ${trade.price:.2f} (Comm: ${trade.commission:.2f})")

    console.print("[bold yellow]Running simulation pass: advancing forward via step_until...[/bold yellow]")
    meta = await client.get_metadata()
    from datetime import datetime, timedelta
    current_t = datetime.fromisoformat(meta["current_time"])

    # Step through 20 minutes of bars to let the bot trade
    for i in range(1, 21):
        target_t = current_t + timedelta(minutes=i)
        await client.step_until(target_t)
        await asyncio.sleep(0.05)

    perf = await client.get_performance()
    acc = await client.get_account()

    console.print("\n[bold green]=== Paper Trading Session Summary ===[/bold green]")
    console.print(f"Ending Equity:   [cyan]${acc.equity:,.2f}[/cyan]")
    console.print(f"Total PnL:       [cyan]${perf['total_pnl']:,.2f}[/cyan] ({perf['total_return_pct']}%)")
    console.print(f"Sharpe Ratio:    [cyan]{perf['sharpe_ratio']}[/cyan]")
    console.print(f"Max Drawdown:    [cyan]{perf['max_drawdown_pct']}%[/cyan] (${perf['max_drawdown_usd']:,.2f})")
    console.print(f"Total Trades:    [cyan]{perf['total_trades']}[/cyan]")

    await client.close()


if __name__ == "__main__":
    asyncio.run(run_bot())
