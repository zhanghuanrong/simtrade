#!/usr/bin/env bash
# ==============================================================================
# SimTrade E2E Simulation Replay Runner
#
# Launches the SimTrade server with the historical 1m dataset,
# waits for readiness, runs the E2E fake trading replay client,
# and prints full performance metrics.
# ==============================================================================

set -euo pipefail

PORT="${PORT:-6688}"
HOST="${HOST:-127.0.0.1}"
HISTORY_FILE="${1:-data/test_checker_trading_history.json}"
ACCOUNT_ID="${2:-fake_e2e_pass}"
TAG="${3:-checker_replay}"
OUTPUT_DIR="${OUTPUT_DIR:-reports}"
CSV_EXPORT="${OUTPUT_DIR}/e2e_trades_${ACCOUNT_ID}.csv"

mkdir -p "${OUTPUT_DIR}"

SERVER_URL="http://${HOST}:${PORT}"
echo "=================================================================="
echo "          Starting SimTrade Server & E2E Simulation Replay         "
echo "=================================================================="
echo "Server URL:    ${SERVER_URL}"
echo "History File:  ${HISTORY_FILE}"
echo "Account ID:    ${ACCOUNT_ID} (Tag: ${TAG})"
echo "CSV Export:    ${CSV_EXPORT}"
echo "=================================================================="

# Check Python environment
if [ -d ".venv" ]; then
    PYTHON_CMD=".venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
else
    PYTHON_CMD="python"
fi

# Launch SimTrade server in background
echo "[1/3] Launching SimTrade server in background..."
${PYTHON_CMD} -m simtrade.cli serve --host "${HOST}" --port "${PORT}" --tickers "ALL" &
SERVER_PID=$!

# Ensure server cleanup upon script exit, failure, or interrupt
cleanup() {
    echo ""
    echo "[Cleanup] Stopping SimTrade server (PID: ${SERVER_PID})..."
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
    echo "[Cleanup] Done."
}
trap cleanup EXIT INT TERM

# Wait for server healthcheck
echo "[2/3] Waiting for server at ${SERVER_URL} to become ready..."
READY=0
for i in {1..60}; do
    if curl -s -f "${SERVER_URL}/api/v1/sim/status" >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 0.5
done

if [ "${READY}" -ne 1 ]; then
    echo "ERROR: Server failed to start within 30 seconds."
    exit 1
fi
echo "SimTrade server is healthy and ready!"

# Run the E2E Fake Trading Client
echo "[3/3] Running E2E Fake Trading Client..."
${PYTHON_CMD} -m simtrade.client.e2e_fake_trading_client \
    --server "${SERVER_URL}" \
    --history "${HISTORY_FILE}" \
    --account-id "${ACCOUNT_ID}" \
    --tag "${TAG}" \
    --export-csv "${CSV_EXPORT}" \
    --reports-dir "${OUTPUT_DIR}"

CLIENT_EXIT_CODE=$?

if [ ${CLIENT_EXIT_CODE} -eq 0 ]; then
    echo "SUCCESS: E2E Simulation completed successfully."
else
    echo "FAILURE: E2E Simulation encountered errors (Exit Code: ${CLIENT_EXIT_CODE})."
fi

exit ${CLIENT_EXIT_CODE}
