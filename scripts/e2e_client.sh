#!/usr/bin/env bash
# ==============================================================================
# SimTrade E2E Client Runner
#
# Connects to a running SimTrade server and executes an end-to-end replay
# of historical paper trading logs.
# ==============================================================================

set -euo pipefail

PORT="${PORT:-6688}"
HOST="${HOST:-127.0.0.1}"
HISTORY_FILE="${1:-data/test_checker_trading_history.json}"
ACCOUNT_ID="${2:-fake_e2e_pass}"
TAG="${3:-checker_replay}"
LEVERAGE="${LEVERAGE:-}"
OUTPUT_DIR="${OUTPUT_DIR:-reports}"
CSV_EXPORT="${OUTPUT_DIR}/e2e_trades_${ACCOUNT_ID}.csv"

mkdir -p "${OUTPUT_DIR}"

SERVER_URL="http://${HOST}:${PORT}"

echo "=================================================================="
echo "                 SimTrade E2E Client Replay                       "
echo "=================================================================="
echo "Server URL:    ${SERVER_URL}"
echo "History File:  ${HISTORY_FILE}"
echo "Account ID:    ${ACCOUNT_ID} (Tag: ${TAG})"
if [ -n "${LEVERAGE}" ]; then
echo "Pass Leverage: ${LEVERAGE}x (CLI Override)"
fi
echo "CSV Export:    ${CSV_EXPORT}"
echo "Reports Dir:   ${OUTPUT_DIR}"
echo "=================================================================="

# Check Python environment
if [ -d ".venv" ]; then
    PYTHON_CMD=".venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
else
    PYTHON_CMD="python"
fi

# Verify server connectivity
echo "Connecting to SimTrade server at ${SERVER_URL}..."
READY=0
for i in {1..60}; do
    if curl -s -f "${SERVER_URL}/api/v1/sim/status" >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 0.5
done

if [ "${READY}" -ne 1 ]; then
    echo "ERROR: Could not connect to SimTrade server at ${SERVER_URL}."
    echo "Please ensure the server is running. You can start it with:"
    echo "    ./scripts/run_simtrade_server.sh"
    exit 1
fi

echo "Connected successfully to SimTrade server!"
echo "Executing paper trading history replay..."

CLIENT_ARGS=(
    --server "${SERVER_URL}"
    --history "${HISTORY_FILE}"
    --account-id "${ACCOUNT_ID}"
    --tag "${TAG}"
    --export-csv "${CSV_EXPORT}"
    --reports-dir "${OUTPUT_DIR}"
)

if [ -n "${LEVERAGE}" ]; then
    CLIENT_ARGS+=(--leverage "${LEVERAGE}")
fi

exec ${PYTHON_CMD} -m simtrade.client.e2e_fake_trading_client "${CLIENT_ARGS[@]}"
