# SimTrade Server & Client Usage Guide

SimTrade is an event-driven paper trading simulation server designed for quantitative researchers, algorithmic trading bots, and backtest-to-paper replay. It provides multi-ticker 1-minute OHLCV replay, margin policy enforcement, real-time WebSocket streaming, a REST API with Swagger documentation, and a visual Web Dashboard.

---

## Table of Contents
1. [Starting the Server (CLI Usage)](#1-starting-the-server-cli-usage)
2. [Connecting to the Server](#2-connecting-to-the-server)
3. [Querying Simulation Metadata & Time Range](#3-querying-simulation-metadata--time-range)
4. [Negotiating Initial Account Settings & Run Tagging](#4-negotiating-initial-account-settings--run-tagging)
5. [Controlling the Simulation Lifecycle](#5-controlling-the-simulation-lifecycle)
6. [Order Submission, Order Details & Queries](#6-order-submission-order-details--queries)
7. [Saving & Exporting Trades and Audit History](#7-saving--exporting-trades-and-audit-history)
8. [Inspecting Passes in the Web Dashboard](#8-inspecting-passes-in-the-web-dashboard)
9. [Complete End-to-End Python Client Example](#9-complete-end-to-end-python-client-example)

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
| `--tickers` | `str` | `AAPL,NVDA,TSLA,MSFT` | Comma-separated list of tickers to stream, or `ALL` to load all symbols in the dataset. |
| `--data-file` | `str` | `None` | Explicit path to a Parquet or CSV file. Defaults to `data/1m_20260817_now.parquet` if present. |
| `--data-dir` | `str` | `data` | Directory containing historical data files. |
| `--leverage` | `float` | `2.0` | Maximum leverage allowed (e.g. `2.0` = 50% initial margin, `4.0` = 25% initial margin). |
| `--maint-margin`| `float` | `0.25` | Maintenance margin rate threshold for liquidations (`0.25` = 25%). |

### Example Commands

```bash
# 1. Run server with top tickers on port 6688
simtrade serve --tickers AAPL,NVDA,TSLA,MSFT

# 2. Run server with ALL 149 tickers from the parquet file
simtrade serve --tickers ALL --port 6688

# 3. Custom leverage (4x leverage = 25% margin) and 15% maintenance margin
simtrade serve --tickers AAPL,NVDA --leverage 4.0 --maint-margin 0.15
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

You can register a tagged simulation pass with a defined starting **Total Equity**, initial holdings, and margin parameters.

### Margin Occupancy & Initial Total Equity Rules:
- **Total Equity** is defined as $\text{Cash} + \text{Net Market Value of Positions}$.
- `initial_entry_prices` is **not required**. The server automatically prices initial holdings using current market mark prices.
- Initial positions **occupy margin immediately**:
  $$\text{Initial Margin Requirement} = \sum |\text{Quantity}_i| \times \text{Mark Price}_i \times \text{Initial Margin Rate}$$
- Liquid cash is initialized as:
  $$\text{Cash} = \text{initial\_total\_equity} - \sum (\text{Quantity}_i \times \text{Mark Price}_i)$$
- **Available Margin** (and initial Buying Power) reflect the margin occupied by initial holdings:
  $$\text{Available Margin} = \text{Total Equity} - \text{Initial Margin Requirement}$$

### REST Endpoint
`POST /api/v1/account/setup`

### Request Body (`AccountSetupRequest`):
```json
{
  "account_id": "momentum_v1_run",
  "tag": "sma_fast_pass",
  "initial_total_equity": 100000.0,
  "leverage": 4.0,
  "initial_margin_rate": 0.25,
  "maintenance_margin_rate": 0.15,
  "initial_positions": {
    "AAPL": 100.0,
    "NVDA": 50.0
  }
}
```

### Field Explanations:
- `account_id`: Unique identifier for this simulation pass (e.g. `momentum_v1_run`).
- `tag`: Human-readable label (shown in the UI dropdown).
- `initial_total_equity`: Total portfolio starting equity (defaults to `100000.0`).
- `initial_cash`: Optional. If specified, sets liquid cash directly; otherwise, computed as `initial_total_equity - positions_market_value`.
- `leverage`: Max leverage permitted (e.g., `4.0` gives 4x buying power = 25% margin).
- `initial_margin_rate`: Fraction of position value required as margin (e.g., `0.25` for 25%).
- `maintenance_margin_rate`: Maintenance threshold before liquidation (e.g., `0.15` for 15%).
- `initial_positions`: Starting holdings (`> 0` for Long, `< 0` for Short).

### Python SDK:
```python
acc = await client.setup_account(
    account_id="momentum_v1_run",
    tag="sma_fast_pass",
    initial_total_equity=100000.0,
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

Time in SimTrade is strictly server-driven. The baseline progression pace is `1.0x` (real-time virtual time). When a client needs to advance time or evaluate strategies over intervals, it accelerates time forward using `step_until`.

### 1. Start Continuous Playback (1.0x Baseline)
```bash
curl -X POST "http://127.0.0.1:6688/api/v1/sim/start"
```
Or via SDK:
```python
await client.start_sim()
```

### 2. Accelerate Forward to Target Timestamp (`step_until`)
Clients accelerate forward to a target timestamp from the current $T_{anchor}$:

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
1. **$T_{anchor}$ Progression**: Server maintains $T_{anchor}$, the timestamp where historical bar data has been published. `step_until` processes all intermediate bars between $T_{anchor}$ and `target_time`.
2. **Pure Client Request & Past Ignored**: If `target_time <= current_time`, the server ignores the request without rolling backward:
   ```json
   {
     "status": "ignored",
     "reason": "target_time is less than or equal to current T_anchor",
     "current_time": "2026-08-17T14:30:00",
     "bars_processed": 0
   }
   ```
3. **Non-Trading Gaps (Overnights / Weekends)**: The server does not invent non-trading gap data. If the client steps into a gap, the clock fast-forwards to `target_time` and replies with empty bars (`bars: {}`) and status `"TARGET_REACHED"`.
4. **Intermediate Lifecycle Execution**: All limit/stop orders, mark prices, financing fees, and performance snapshots are processed sequentially along each 1-minute step.
5. **Margin Liquidation Early Halt**: If an account incurs a margin call (`equity < maintenance_margin_requirement`), execution immediately halts at that exact bar:
   ```json
   {
     "status": "LIQUIDATION_TRIGGERED",
     "current_time": "2026-08-17T13:45:00",
     "account_id": "trader_1",
     "deficit": 1250.0,
     "bars_processed": 14
   }
   ```
   The client receives this feedback with the updated timestamp. After handling the feedback, subsequent `step_until` calls will resume from this updated timestamp.

### 3. Check if Simulation is Finished
Check `meta['is_finished']` via `GET /api/v1/sim/metadata`. When `is_finished == true`, all historical bars in the dataset have been replayed.

### 4. Reset Simulation for a New Pass
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

## 6. Order Submission, Order Details & Queries

SimTrade provides comprehensive order management and detailed order querying.

### 1. Submit an Order
Submit a new order (`MARKET`, `LIMIT`, `STOP`, `STOP_LIMIT`) via REST:

```bash
curl -X POST "http://127.0.0.1:6688/api/v1/orders?account_id=momentum_v1_run" \
     -H "Content-Type: application/json" \
     -d '{
       "ticker": "AAPL",
       "side": "BUY",
       "order_type": "LIMIT",
       "quantity": 25,
       "limit_price": 305.50,
       "time_in_force": "GTC",
       "client_order_id": "my_signal_101"
     }'
```

Or via Python SDK:
```python
order = await client.submit_order(
    ticker="AAPL",
    side=OrderSide.BUY,
    quantity=25,
    order_type=OrderType.LIMIT,
    limit_price=305.50,
    client_order_id="my_signal_101"
)
print("Order created:", order.order_id, order.status)
```

Convenience methods on `SimTradeClient`:
```python
await client.buy("AAPL", quantity=10)
await client.sell("AAPL", quantity=10)
await client.short("NVDA", quantity=20, order_type=OrderType.LIMIT, limit_price=230.0)
```

---

### 2. Query Specific Order Details (`GET /api/v1/orders/{order_id}`)

You can retrieve complete details for any order by its `order_id`, regardless of whether it is active, partially filled, filled, cancelled, or rejected.

#### REST Endpoint
`GET /api/v1/orders/{order_id}`

#### Example Request:
```bash
curl -X GET "http://127.0.0.1:6688/api/v1/orders/f47ac10b-58cc-4372-a567-0e02b2c3d479"
```

#### Example Response:
```json
{
  "order_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "client_order_id": "my_signal_101",
  "account_id": "momentum_v1_run",
  "ticker": "AAPL",
  "side": "BUY",
  "order_type": "LIMIT",
  "quantity": 25.0,
  "limit_price": 305.5,
  "stop_price": null,
  "time_in_force": "GTC",
  "status": "FILLED",
  "filled_quantity": 25.0,
  "remaining_quantity": 0.0,
  "avg_fill_price": 305.42,
  "reject_reason": null,
  "created_at": "2026-09-28T16:30:00.123456Z",
  "updated_at": "2026-09-28T16:31:00.654321Z"
}
```

#### Order Detail Fields:
| Field | Type | Description |
|---|---|---|
| `order_id` | `string` | Unique UUID assigned by the server. |
| `client_order_id` | `string \| null` | Optional client-provided identifier for idempotency and correlation. |
| `account_id` | `string` | ID of the account or pass that owns the order. |
| `ticker` | `string` | Instrument symbol (e.g. `"AAPL"`). |
| `side` | `string` | `"BUY"`, `"SELL"`, or `"SELL_SHORT"`. |
| `order_type` | `string` | `"MARKET"`, `"LIMIT"`, `"STOP"`, or `"STOP_LIMIT"`. |
| `quantity` | `float` | Original requested order size. |
| `limit_price` | `float \| null` | Limit price for LIMIT/STOP_LIMIT orders. |
| `stop_price` | `float \| null` | Activation trigger price for STOP/STOP_LIMIT orders. |
| `time_in_force` | `string` | `"GTC"` (Good 'Til Cancelled), `"DAY"`, `"IOC"` (Immediate or Cancel), `"FOK"` (Fill or Kill). |
| `status` | `string` | Current lifecycle state: `"PENDING"`, `"ACCEPTED"`, `"PARTIALLY_FILLED"`, `"FILLED"`, `"CANCELLED"`, `"REJECTED"`. |
| `filled_quantity` | `float` | Cumulative number of shares executed so far. |
| `remaining_quantity` | `float` | Remaining unfilled quantity (`quantity - filled_quantity`). |
| `avg_fill_price` | `float` | Volume-weighted average price of all execution fills. |
| `reject_reason` | `string \| null` | Error reason if order was rejected by margin check or exchange validation. |
| `created_at` | `string (ISO)` | Timestamp when the order was submitted. |
| `updated_at` | `string (ISO)` | Timestamp of last execution fill, cancellation, or status update. |

#### Python SDK Example:
```python
order = await client.get_order("f47ac10b-58cc-4372-a567-0e02b2c3d479")
print(f"Order Status: {order.status.value}")
print(f"Filled: {order.filled_quantity}/{order.quantity} @ ${order.avg_fill_price:.2f}")
```

---

### 3. Query Active Orders (`GET /api/v1/orders`)
List all currently open and unfilled orders:
```bash
curl -X GET "http://127.0.0.1:6688/api/v1/orders?account_id=momentum_v1_run"
```
Or via SDK:
```python
active_orders = await client.get_active_orders()
for o in active_orders:
    print(f"Open: {o.side} {o.quantity} {o.ticker} Limit: {o.limit_price}")
```

---

### 4. Cancel an Order (`DELETE /api/v1/orders/{order_id}`)
```bash
curl -X DELETE "http://127.0.0.1:6688/api/v1/orders/f47ac10b-58cc-4372-a567-0e02b2c3d479?account_id=momentum_v1_run"
```
Or via SDK:
```python
cancelled_order = await client.cancel("f47ac10b-58cc-4372-a567-0e02b2c3d479")
print("Cancelled order:", cancelled_order.order_id, cancelled_order.status)
```

---

## 7. Saving & Exporting Trades and Audit History

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

## 8. Inspecting Passes in the Web Dashboard

Visit [http://localhost:6688/dashboard](http://localhost:6688/dashboard):

1. **Pass / Account Dropdown**:
   - Located in the top header.
   - Shows all active accounts and passes (e.g. `trader_1 (default)`, `momentum_v1_run`).
   - Selecting a pass immediately updates the KPI cards, open positions table, active orders table, and equity curve chart for that specific pass.
2. **`+ New Pass` Button**:
   - Opens a modal where you can specify Account ID, Initial Total Equity, Leverage multiplier, and Initial Holdings.
   - Creating the pass automatically registers it and switches the UI view to it.
3. **Simulation Timeline & Progress Bar**:
   - Displays the dataset date range (e.g. `2026-08-17 13:31 ~ 2026-09-25 20:00`), current virtual time, and progress percentage.
4. **Playback & Export Controls**:
   - `Play (1.0x Realtime)` and `Reset Run`.
   - `Export Trades` and `Report`.

---

## 9. Complete End-to-End Python Client Example

Save the script below as `my_trading_bot.py` and run it against the server:

```python
import asyncio
from datetime import datetime, timedelta
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

        # 3. Negotiate initial account settings with $100k total equity and initial AAPL holding
        pass_name = "momentum_v1_run"
        acc = await client.setup_account(
            account_id=pass_name,
            tag="sma_momentum_strategy",
            initial_total_equity=100_000.0,
            leverage=4.0,  # 4x leverage = 25% margin
            initial_positions={"AAPL": 50.0},
        )
        print(f"Pass '{acc.account_id}' created with Equity: ${acc.equity:,.2f}, Buying Power: ${acc.margin.buying_power:,.2f}")

        # 4. Accelerate forward 10 minutes
        current_time = datetime.fromisoformat(meta["current_time"])
        target_time = current_time + timedelta(minutes=10)
        step_res = await client.step_until(target_time)
        print(f"Stepped to {step_res['current_time']} (Processed {step_res['bars_processed']} bars)")

        # 5. Place a limit buy order and query order details
        order = await client.buy("AAPL", quantity=20, order_type=OrderType.LIMIT, limit_price=310.0)
        print(f"Submitted Order ID: {order.order_id}, Initial Status: {order.status.value}")

        # Accelerate forward another 5 minutes to trigger matching
        target_time_2 = target_time + timedelta(minutes=5)
        await client.step_until(target_time_2)

        # 6. Retrieve detailed order result
        order_detail = await client.get_order(order.order_id)
        print(f"Updated Order Status: {order_detail.status.value}")
        print(f"Filled Quantity:      {order_detail.filled_quantity}/{order_detail.quantity}")
        print(f"Avg Fill Price:       ${order_detail.avg_fill_price:.2f}")

        # 7. Check performance
        final_acc = await client.get_account()
        perf = await client.get_performance()

        print("\n=== Pass Performance Summary ===")
        print(f"Final Equity:   ${final_acc.equity:,.2f}")
        print(f"Total PnL:      ${perf['total_pnl']:,.2f} ({perf['total_return_pct']}%)")
        print(f"Total Trades:   {perf['total_trades']}")

        # 8. Save session files to server & export local CSV
        await client.save_session(output_dir="reports")
        await client.export_trades_csv(save_path="my_run_trades.csv")
        print("Reports and trades saved successfully!")

if __name__ == "__main__":
    asyncio.run(run_simulation_pass())
```
