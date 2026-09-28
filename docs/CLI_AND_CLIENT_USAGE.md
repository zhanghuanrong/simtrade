# SimTrade Server & Client Usage Guide

SimTrade is an event-driven paper trading simulation server designed for quantitative researchers, algorithmic trading bots, and backtest-to-paper replay. It provides multi-ticker 1-minute OHLCV replay, margin policy enforcement, real-time WebSocket streaming, a REST API with Swagger documentation, and a visual Web Dashboard.

---

## Table of Contents
1. [Starting the Server (CLI Usage)](#1-starting-the-server-cli-usage)
2. [Connecting to the Server](#2-connecting-to-the-server)
3. [Querying Simulation Metadata & Time Range](#3-querying-simulation-metadata--time-range)
4. [Negotiating Initial Account Settings & Run Tagging](#4-negotiating-initial-account-settings--run-tagging)
5. [Controlling the Simulation Lifecycle](#5-controlling-the-simulation-lifecycle)
6. [Saving & Exporting Trades and Audit History](#6-saving--exporting-trades-and-audit-history)
7. [Inspecting Passes in the Web Dashboard](#7-inspecting-passes-in-the-web-dashboard)
8. [Complete End-to-End Python Client Example](#8-complete-end-to-end-python-client-example)

---

## 1. Starting the Server (CLI Usage)

Activate your virtual environment and start the server using the `simtrade` command:

```bash
cd /home/zhanglei/src/simtrade
source .venv/bin/activate

# Start server (auto-detects data/1m_20260817_now.parquet)
simtrade serve [OPTIONS]
```

### CLI Options for `simtrade serve`

| Option | Type | Default | Description |
|---|---|---|---|
| `--host` | `str` | `0.0.0.0` | Network interface to bind. |
| `--port` | `int` | `6688` | Port to listen on. |
| `--speed` | `float` | `10.0` | Playback speed multiplier (`1.0` = real-time, `60.0` = 1 simulated min per sec, `0` = MAX speed). |
| `--tickers` | `str` | `AAPL,NVDA,TSLA,MSFT` | Comma-separated list of tickers to stream, or `ALL` to load all symbols in the dataset. |
| `--data-file` | `str` | `None` | Explicit path to a Parquet or CSV file. Defaults to `data/1m_20260817_now.parquet` if present. |
| `--data-dir` | `str` | `data` | Directory containing historical data files. |
| `--leverage` | `float` | `2.0` | Maximum leverage allowed (e.g. `2.0` = 50% initial margin, `4.0` = 25% initial margin). |
| `--maint-margin`| `float` | `0.25` | Maintenance margin rate threshold for liquidations (`0.25` = 25%). |

### Example Commands

```bash
# 1. Run server with top tickers at 10x replay speed
simtrade serve --tickers AAPL,NVDA,TSLA,MSFT --speed 10

# 2. Run server with ALL 149 tickers from the parquet file at 5x speed
simtrade serve --tickers ALL --speed 5 --port 6688

# 3. Maximum non-blocking speed (as fast as CPU and event loop can process)
simtrade serve --tickers AAPL,NVDA --speed 0
```

Once running, the server provides:
- **Web Dashboard**: [http://localhost:6688/dashboard](http://localhost:6688/dashboard)
- **Interactive Swagger API Docs**: [http://localhost:6688/docs](http://localhost:6688/docs)
- **Unified WebSocket Stream**: `ws://localhost:6688/ws/unified`

---

## 2. Connecting to the Server

### Options:
1. **Python Client SDK (`SimTradeClient`)**: Recommended for Python bots.
2. **REST API**: Any HTTP library (`requests`, `httpx`, `curl`).
3. **WebSocket**: Any standard WebSocket client.

### Connecting via Python Client SDK:
```python
from simtrade.client.trader_client import SimTradeClient

client = SimTradeClient(base_url="http://127.0.0.1:6688", account_id="my_bot_pass_1")
await client.connect()
# Automatically connects HTTP session and WebSocket stream
```

### Connecting via curl (REST):
```bash
curl -X GET "http://127.0.0.1:6688/api/v1/sim/status"
```

---

## 3. Querying Simulation Metadata & Time Range

Before starting a simulation pass, clients can query the dataset's time range, total bars, and available tickers.

### REST Endpoint
`GET /api/v1/sim/metadata`

### Example `curl`:
```bash
curl -X GET "http://127.0.0.1:6688/api/v1/sim/metadata"
```

### Example JSON Response:
```json
{
  "start_time": "2026-08-17T13:31:00",
  "end_time": "2026-09-25T20:00:00",
  "current_time": "2026-08-17T13:31:00",
  "total_bars": 11310,
  "current_step": 0,
  "cursor": 0,
  "progress_pct": 0.0,
  "is_finished": false,
  "speed_multiplier": 10.0,
  "is_running": false,
  "is_paused": true,
  "available_tickers": ["AAPL", "NVDA", "TSLA", "MSFT"],
  "active_accounts": ["trader_1"]
}
```

### Python SDK:
```python
meta = await client.get_metadata()
print(f"Dataset Range: {meta['start_time']} to {meta['end_time']}")
print(f"Total Bars: {meta['total_bars']}, Available Tickers: {meta['available_tickers']}")
```

---

## 4. Negotiating Initial Account Settings & Run Tagging

You can register a tagged simulation pass with custom initial cash, initial positions, and custom margin terms.

### REST Endpoint
`POST /api/v1/account/setup`

### Request Body (`AccountSetupRequest`):
```json
{
  "account_id": "momentum_v1_run",
  "tag": "sma_fast_pass",
  "initial_cash": 100000.0,
  "leverage": 4.0,
  "initial_margin_rate": 0.25,
  "maintenance_margin_rate": 0.15,
  "initial_positions": {
    "AAPL": 100.0,
    "NVDA": 50.0
  },
  "initial_entry_prices": {
    "AAPL": 305.0,
    "NVDA": 225.0
  }
}
```

### Field Explanations:
- `account_id`: Unique identifier for this simulation pass (e.g. `run_pass_1`).
- `tag`: Human-readable label (shown in the UI dropdown).
- `initial_cash`: Liquid cash starting capital.
- `leverage`: Max leverage permitted (e.g., `4.0` gives 4x buying power).
- `initial_margin_rate`: Fraction of position value required as margin (e.g., `0.25` for 25%).
- `maintenance_margin_rate`: Maintenance threshold before liquidation (e.g., `0.15` for 15%).
- `initial_positions`: Starting holdings (`> 0` for Long, `< 0` for Short).
- `initial_entry_prices`: Optional cost basis override for initial positions.

### Python SDK:
```python
acc = await client.setup_account(
    account_id="momentum_v1_run",
    tag="sma_fast_pass",
    initial_cash=100000.0,
    leverage=4.0,
    initial_positions={"AAPL": 100}
)
print(f"Configured Account: {acc.account_id}, Equity: ${acc.equity:,.2f}, Buying Power: ${acc.margin.buying_power:,.2f}")
```

### Inspecting Account Health:
To query current cash, margin used, and margin available:
```bash
curl -X GET "http://127.0.0.1:6688/api/v1/account?account_id=momentum_v1_run"
```
Key response fields:
- `equity`: Total portfolio liquidation value.
- `cash`: Liquid cash (can be negative if borrowing on margin).
- `margin.buying_power`: Maximum additional position value the account can open.
- `margin.initial_margin_requirement`: Margin used / locked up.
- `margin.maintenance_margin_requirement`: Minimum required equity.
- `margin.margin_excess`: Headroom above margin call (`equity - maintenance_margin`).
- `margin.leverage`: Current effective leverage.

---

## 5. Controlling the Simulation Lifecycle

### 1. Start Continuous Playback
```bash
curl -X POST "http://127.0.0.1:6688/api/v1/sim/start"
```
Or via SDK:
```python
await client.start_sim()
```

### 2. Pause Playback
```bash
curl -X POST "http://127.0.0.1:6688/api/v1/sim/pause"
```
Or via SDK:
```python
await client.pause_sim()
```

### 3. Step 1 Minute Manually
```bash
curl -X POST "http://127.0.0.1:6688/api/v1/sim/step"
```
Or via SDK:
```python
step_data = await client.step_sim()
print("Stepped to:", step_data["timestamp"])
```

### 4. Accelerate Simulation Forward: Step Until Target Timestamp (`step_until`)
The server strictly drives simulation time forward with a baseline ratio of 1.0 (realtime). A client can also accelerate time forward to any target timestamp:

```bash
curl -X POST "http://127.0.0.1:6688/api/v1/sim/step_until" \
     -H "Content-Type: application/json" \
     -d '{
       "target_time": "2026-08-17T14:30:00",
       "account_id": "trader_1"
     }'
```
Or via Python SDK:
```python
res = await client.step_until("2026-08-17T14:30:00")
print(res["status"], res["current_time"], res["bars_processed"])
```

Or over unified WebSocket:
```json
{
  "type": "STEP_UNTIL",
  "data": {
    "target_time": "2026-08-17T14:30:00",
    "account_id": "trader_1"
  }
}
```

#### `step_until` Rules & Behaviors:
1. **$T_{anchor}$ Progression**: The server maintains $T_{anchor}$, the timestamp up to which historical bar data has been published. `step_until` processes all intermediate bars between $T_{anchor}$ and `target_time`.
2. **Pure Client Request & Past Ignored**: If `target_time <= current_time`, the server ignores the request without rolling backward:
   ```json
   {
     "status": "ignored",
     "reason": "target_time is less than or equal to current T_anchor",
     "current_time": "2026-08-17T14:30:00",
     "bars_processed": 0
   }
   ```
3. **Non-Trading Gaps (Overnight/Weekend)**: The server does not invent or guess non-trading gap data. If the client requests stepping into a gap, the server fast-forwards the clock to `target_time` and replies with empty bars (`bars: {}`) and status `"TARGET_REACHED"`.
4. **Intermediate Lifecycle Execution**: All limit/stop orders, mark prices, financing fees, and performance snapshots are processed sequentially along each 1-minute step.
5. **Margin Liquidation Early Halt**: If an account incurs a margin call (`equity < maintenance_margin_requirement`), `step_until` immediately halts execution at that exact bar, returning:
   ```json
   {
     "status": "LIQUIDATION_TRIGGERED",
     "current_time": "2026-08-17T13:45:00",
     "account_id": "trader_1",
     "deficit": 1250.0,
     "bars_processed": 14
   }
   ```
   The client receives this feedback with the updated timestamp. Once handled, the client can issue subsequent `step_until` commands from that updated position.

### 5. Change Playback Speed on-the-Fly
```bash
curl -X POST "http://127.0.0.1:6688/api/v1/sim/speed" \
     -H "Content-Type: application/json" \
     -d '{"speed_multiplier": 60.0}'
```
Or via SDK:
```python
await client.set_speed(60.0)  # 1 simulated minute per second
```

### 5. Check if Simulation is Finished
Check `meta['is_finished']` via `GET /api/v1/sim/metadata`. When `is_finished == true`, all historical bars in the dataset have been replayed.

### 6. Reset Simulation for a New Pass
Rewinds the clock cursor back to the start of the Parquet dataset (or a specified start time) and clears active unfilled orders:
```bash
curl -X POST "http://127.0.0.1:6688/api/v1/sim/reset" \
     -H "Content-Type: application/json" \
     -d '{}'
```
Or via SDK:
```python
await client.reset_sim()
```

---

## 6. Saving & Exporting Trades and Audit History

When a simulation pass finishes (or at any time), you can export the full transaction history and performance metrics.

### 1. Download Trades as CSV
Directly download a CSV containing every fulfilled trade (`trade_id`, `ticker`, `side`, `price`, `quantity`, `commission`, `timestamp`):
```bash
curl -X GET "http://127.0.0.1:6688/api/v1/reports/trades/csv?account_id=momentum_v1_run" \
     -o "trades_momentum_v1_run.csv"
```
Or via SDK:
```python
await client.export_trades_csv(account_id="momentum_v1_run", save_path="my_trades.csv")
```

### 2. Retrieve Quantitative Performance Report
Computes Sharpe ratio, Sortino ratio, max drawdown, win rate, and total return:
```bash
curl -X GET "http://127.0.0.1:6688/api/v1/reports/performance?account_id=momentum_v1_run"
```

### 3. Save Complete Session to Server Disk
Triggers the server to persist trades CSV, performance JSON, and audit ledger JSON into the `reports/` folder:
```bash
curl -X POST "http://127.0.0.1:6688/api/v1/reports/save" \
     -H "Content-Type: application/json" \
     -d '{"account_id": "momentum_v1_run", "output_dir": "reports"}'
```
Files created on server:
- `reports/trades_{tag}.csv`
- `reports/performance_{tag}.json`
- `reports/ledger_{tag}.json`

Or via SDK:
```python
await client.save_session(account_id="momentum_v1_run", output_dir="reports")
```

---

## 7. Inspecting Passes in the Web Dashboard

Visit [http://localhost:6688/dashboard](http://localhost:6688/dashboard):

1. **Pass / Account Dropdown**:
   - Located in the top header.
   - Shows all active accounts and passes (e.g. `trader_1 (default)`, `momentum_v1_run`, `mean_revert_pass_2`).
   - Selecting a pass immediately updates the KPI cards, open positions table, active orders table, and equity curve chart for that specific pass.
2. **`+ New Pass` Button**:
   - Opens a modal where you can specify Account ID, Starting Cash, Leverage multiplier, and Initial Holdings.
   - Creating the pass automatically registers it and switches the UI view to it.
3. **Simulation Timeline & Progress Bar**:
   - Displays the dataset date range (e.g. `2026-08-17 13:31 ~ 2026-09-25 20:00`), current virtual time, and progress percentage.
4. **Playback & Reset Controls**:
   - `Play`, `Pause`, `Step 1m`, and `Reset Run`.
   - Speed buttons (`1x`, `10x`, `60x`, `MAX`).
5. **Report & Export Buttons**:
   - **`Export Trades`**: Directly downloads the CSV of all trades for the active pass.
   - **`Report`**: Opens the quantitative summary modal with Initial Capital, Final Equity, Total PnL, Annualized Sharpe Ratio, Max Drawdown, and a "Save Session to Server" button.

---

## 8. Complete End-to-End Python Client Example

Save the script below as `my_trading_bot.py` and run it against the server:

```python
import asyncio
from simtrade.client.trader_client import SimTradeClient
from simtrade.models.order import OrderType

async def run_simulation_pass():
    async with SimTradeClient("http://127.0.0.1:6688") as client:
        # 1. Inspect dataset metadata
        meta = await client.get_metadata()
        print(f"Replay range: {meta['start_time']} to {meta['end_time']}")
        print(f"Available tickers: {meta['available_tickers']}")

        # 2. Reset simulator to the beginning of the dataset
        await client.reset_sim()

        # 3. Negotiate initial account settings for this pass
        pass_name = "momentum_v1_run"
        acc = await client.setup_account(
            account_id=pass_name,
            tag="sma_momentum_strategy",
            initial_cash=100_000.0,
            leverage=4.0,  # 4x leverage
        )
        print(f"Pass '{acc.account_id}' created with Buying Power: ${acc.margin.buying_power:,.2f}")

        # 4. Define trading logic on incoming 1-minute bars
        price_history = []

        @client.on_bar
        async def on_bar(bars):
            if "AAPL" in bars:
                aapl_bar = bars["AAPL"]
                price_history.append(aapl_bar.close)

                # Simple moving average crossover logic
                if len(price_history) >= 5:
                    sma5 = sum(price_history[-5:]) / 5.0
                    if aapl_bar.close > sma5:
                        # Buy 50 shares
                        await client.buy("AAPL", quantity=50, order_type=OrderType.MARKET)
                    elif aapl_bar.close < sma5:
                        # Sell / close 50 shares
                        await client.sell("AAPL", quantity=50, order_type=OrderType.MARKET)

        # 5. Listen to executions
        @client.on_trade
        async def on_trade(trade):
            print(f"Trade Fill: {trade.side.value} {trade.quantity} {trade.ticker} @ ${trade.price:.2f} (Fee: ${trade.commission:.2f})")

        # 6. Start simulation at 50x speed
        print("Starting simulation pass...")
        await client.set_speed(50.0)
        await client.start_sim()

        # Run for 15 seconds of real time
        await asyncio.sleep(15)

        # 7. Pause and check results
        await client.pause_sim()
        final_acc = await client.get_account()
        perf = await client.get_performance()

        print("\n=== Pass Performance Summary ===")
        print(f"Final Equity:   ${final_acc.equity:,.2f}")
        print(f"Total PnL:      ${perf['total_pnl']:,.2f} ({perf['total_return_pct']}%)")
        print(f"Sharpe Ratio:   {perf['sharpe_ratio']}")
        print(f"Max Drawdown:   ${perf['max_drawdown_usd']:,.2f} ({perf['max_drawdown_pct']}%)")
        print(f"Total Trades:   {perf['total_trades']}")

        # 8. Save session files to server & export local CSV
        await client.save_session(output_dir="reports")
        await client.export_trades_csv(save_path="my_run_trades.csv")
        print("Reports and trades saved successfully!")

if __name__ == "__main__":
    asyncio.run(run_simulation_pass())
```
