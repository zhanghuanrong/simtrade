# SimTrade

SimTrade is a high-performance, realistic paper trading simulation server designed for quantitative research, algorithmic trading bots, and market replay.

It simulates an exchange and broker environment, publishing synchronized multi-ticker market data feeds (e.g. 1-minute OHLCV bars), maintaining paper trader accounts, matching orders with realistic execution prices and slippage, and enforcing configurable margin and risk policies.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Key Features

1. **Multi-Ticker Market Data Replay**:
   - Synchronized replay of 1-minute OHLCV bar feeds across arbitrary ticker lists.
   - Built-in realistic Geometric Brownian Motion synthetic data generator or CSV historical data playback.
   - Configurable simulation clock: Step-by-step mode or real-time simulation with speed multipliers ($1\times, 10\times, 60\times, \text{MAX}$).

2. **Realistic Matching Engine**:
   - Order types: `MARKET`, `LIMIT`, `STOP`, and `STOP_LIMIT`.
   - Execution against 1-minute bars using $[Low, High]$ boundaries and configurable slippage models.
   - Realistic volume participation limits (e.g., maximum $10\%$ of bar volume per execution) with partial fills and `TimeInForce` (`GTC`, `DAY`, `IOC`, `FOK`).
   - Commissions and fee tracking.

3. **Margin & Risk Management Policy**:
   - Reg-T style initial margin (e.g., $50\%$) and maintenance margin (e.g., $25\%$).
   - Dynamic buying power calculation and leverage monitoring.
   - Full short selling support with borrow fees and cash collateral tracking.
   - Margin call detection and automated liquidation when $\text{Equity} < \text{Maintenance Margin}$.
   - Daily/hourly borrowing interest on debit cash balances.

4. **Multi-Protocol & Network Interfaces**:
   - **Interactive Web Dashboard**: Visual interface with live ticker prices, interactive equity curve, position table, active orders, and trade tape.
   - **REST API with Swagger Docs**: Clean endpoints for orders, accounts, positions, and simulation controls at `/docs`.
   - **WebSocket Streams**: Real-time bi-directional streaming at `/ws/unified` for market bars, order fills, and account telemetry.
   - **Python Client SDK**: Async client library (`SimTradeClient`) for algorithmic bots.

5. **Reporting & Auditing**:
   - Immutable event ledger for all orders, fills, margin calls, and portfolio snapshots.
   - Quantitative performance analytics: Total Return, Sharpe Ratio, Sortino Ratio, Maximum Drawdown ($ and %), Win Rate, and Profit Factor.
   - One-click export of trades to CSV and complete audit history to JSON.

6. **Multi-Pass Simulation & Run Tagging**:
   - Tag simulation passes by name (e.g. `momentum_v1_run`, `mean_revert_pass_2`).
   - Negotiate custom initial account parameters (cash, starting positions, custom leverage/margin rates).
   - Easily switch between and inspect individual passes in the Web UI.
   - Reset/rewind simulation to run repeated passes across identical market data.

> 📖 **Full Usage Guide**: See [docs/CLI_AND_CLIENT_USAGE.md](docs/CLI_AND_CLIENT_USAGE.md) for detailed instructions on connecting, querying time ranges, account negotiation, simulation controls, and UI inspection.

---

## Installation

```bash
# Clone repository
git clone git@github.com:zhanghuanrong/simtrade.git
cd simtrade

# Setup virtual environment using uv or venv
uv venv
source .venv/bin/activate

# Install dependencies in editable mode
uv pip install -e ".[dev]"
```

---

## Quickstart

### 1. Launch Simulation Server
```bash
# Start server with 4 tickers on port 6688
simtrade serve --tickers AAPL,NVDA,TSLA,MSFT --port 6688
```
Open your browser to:
- **Web Dashboard**: [http://localhost:6688/dashboard](http://localhost:6688/dashboard)
- **Interactive API Docs**: [http://localhost:6688/docs](http://localhost:6688/docs)

### 2. Generate Sample 1m OHLCV Data (Optional)
```bash
simtrade generate-data --days 2 --output-dir data
```

### 3. Run Algorithmic Trading Bot Example
```bash
python examples/momentum_trader_bot.py
```

---

## Python Client SDK Usage

```python
import asyncio
from datetime import datetime, timedelta
from simtrade.client.trader_client import SimTradeClient
from simtrade.models.order import OrderType

async def main():
    async with SimTradeClient("http://localhost:6688") as client:
        # Subscribe to market bars
        @client.on_bar
        async def on_bar(bars):
            print("Received 1m bars for:", list(bars.keys()))

        # Subscribe to trade fills
        @client.on_trade
        async def on_trade(trade):
            print(f"Fill: {trade.side} {trade.quantity} {trade.ticker} @ ${trade.price}")

        # Negotiate initial account terms ($100k equity)
        await client.setup_account(account_id="bot_pass_1", initial_total_equity=100_000.0, leverage=4.0)

        # Submit an order
        order = await client.buy("AAPL", quantity=10, order_type=OrderType.MARKET)
        print("Submitted order:", order.order_id)

        # Accelerate simulation forward 5 minutes to execute
        meta = await client.get_metadata()
        target_time = datetime.fromisoformat(meta["current_time"]) + timedelta(minutes=5)
        await client.step_until(target_time)

        # Query order details
        order_detail = await client.get_order(order.order_id)
        print(f"Order status: {order_detail.status.value}, Filled: {order_detail.filled_quantity} @ ${order_detail.avg_fill_price:.2f}")

asyncio.run(main())
```

---

## API Endpoints Overview

| Category | Endpoint | Method | Description |
|---|---|---|---|
| **Account** | `/api/v1/account` | GET | Retrieve cash, equity, and margin health |
| **Account** | `/api/v1/account/setup` | POST | Configure initial equity, positions, and leverage |
| **Account** | `/api/v1/accounts` | GET | List all active simulation passes |
| **Positions** | `/api/v1/positions` | GET | List open long and short positions |
| **Orders** | `/api/v1/orders` | POST | Submit market, limit, stop, or stop-limit order |
| **Orders** | `/api/v1/orders` | GET | List active orders in book |
| **Orders** | `/api/v1/orders/{id}` | GET | Retrieve full order details by order ID |
| **Orders** | `/api/v1/orders/{id}` | DELETE | Cancel pending order |
| **Trades** | `/api/v1/trades` | GET | Retrieve execution trade history |
| **Market Data** | `/api/v1/market/bars/latest` | GET | Latest 1m OHLCV bars |
| **Simulation** | `/api/v1/sim/start` | POST | Start continuous real-time replay (1.0x) |
| **Simulation** | `/api/v1/sim/step_until` | POST | Accelerate simulation forward to target timestamp |
| **Simulation** | `/api/v1/sim/reset` | POST | Reset playback cursor to start |
| **Reports** | `/api/v1/reports/performance` | GET | Compute Sharpe, Drawdown, Return |
| **Reports** | `/api/v1/reports/trades/csv` | GET | Export trades as CSV |
| **Reports** | `/api/v1/reports/save` | POST | Persist full audit session to server disk |

---

## Testing

Run the test suite with pytest:
```bash
pytest -v
```

---

## License
MIT
