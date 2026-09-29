#!/usr/bin/env bash
# ==============================================================================
# SimTrade Server Runner
#
# Starts the SimTrade paper trading server with historical 1m market feed,
# matching engine, margin rules, REST API, WebSocket, and Web Dashboard.
# ==============================================================================

set -euo pipefail

PORT="${PORT:-6688}"
HOST="${HOST:-0.0.0.0}"
TICKERS="${TICKERS:-ALL}"
DATA_FILE="${DATA_FILE:-}"

# Check Python environment
if [ -d ".venv" ]; then
    PYTHON_CMD=".venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
else
    PYTHON_CMD="python"
fi

DASHBOARD_HOST="${HOST}"
if [ "${DASHBOARD_HOST}" = "0.0.0.0" ]; then
    DASHBOARD_HOST="127.0.0.1"
fi

echo "=================================================================="
echo "                 Starting SimTrade Trading Server                 "
echo "=================================================================="
echo "Host:          ${HOST}"
echo "Port:          ${PORT}"
echo "Tickers:       ${TICKERS}"
echo "Dashboard:     http://${DASHBOARD_HOST}:${PORT}/dashboard"
echo "API Docs:      http://${DASHBOARD_HOST}:${PORT}/docs"
echo "=================================================================="

SERVE_ARGS=(serve --host "${HOST}" --port "${PORT}" --tickers "${TICKERS}")
if [ -n "${DATA_FILE}" ]; then
    SERVE_ARGS+=(--data-file "${DATA_FILE}")
fi

exec ${PYTHON_CMD} -m simtrade.cli "${SERVE_ARGS[@]}"
