# SimTrade

SimTrade is a high-performance, realistic paper trading simulation server designed for quantitative research, algorithmic trading bots, and market replay.

## Features
- **Multi-Ticker Market Data Replay**: Synchronized replay of 1-minute OHLCV bars (extensible to ticks/seconds) across arbitrary ticker lists.
- **Realistic Order Execution Engine**: Market, Limit, Stop, and Stop-Limit orders with realistic fill price estimation, slippage models, and bar volume participation limits.
- **Configurable Margin & Risk Management**: Reg-T style leverage, initial/maintenance margin tracking, borrow fees, and automated liquidation.
- **Multi-Protocol Support**: Full WebSocket streaming and REST APIs with Swagger UI (`/docs`).
- **Web Dashboard**: Real-time visual monitoring of price charts, account equity, open positions, active orders, and trade tape.
- **Python Client SDK**: Async client for trading bots to connect, stream, and trade in a few lines of code.
- **Reporting & Auditing**: Performance metrics (Sharpe ratio, max drawdown, win rate) and transaction ledger export.

## Quickstart
```bash
# Setup environment
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Run server with synthetic or historical data
simtrade serve --port 8000 --speed 10
```
