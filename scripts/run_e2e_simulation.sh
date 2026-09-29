#!/usr/bin/env bash
# ==============================================================================
# SimTrade All-in-One E2E Simulation Replay Runner
#
# Launches the SimTrade server in the background, executes e2e_client.sh,
# and automatically shuts down the background server upon completion.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-6688}"
HOST="${HOST:-127.0.0.1}"
HISTORY_FILE="${1:-data/test_checker_trading_history.json}"
ACCOUNT_ID="${2:-fake_e2e_pass}"
TAG="${3:-checker_replay}"
OUTPUT_DIR="${OUTPUT_DIR:-reports}"

# 1. Start server in background
echo "[1/2] Starting SimTrade server in background..."
HOST="${HOST}" PORT="${PORT}" "${SCRIPT_DIR}/run_simtrade_server.sh" &
SERVER_PID=$!

cleanup() {
    echo ""
    echo "[Cleanup] Stopping SimTrade server (PID: ${SERVER_PID})..."
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
    echo "[Cleanup] Done."
}
trap cleanup EXIT INT TERM

# 2. Run E2E Client
echo "[2/2] Running E2E Client..."
HOST="${HOST}" PORT="${PORT}" OUTPUT_DIR="${OUTPUT_DIR}" "${SCRIPT_DIR}/e2e_client.sh" "${HISTORY_FILE}" "${ACCOUNT_ID}" "${TAG}"
