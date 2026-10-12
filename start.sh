#!/usr/bin/env bash

set -e

# ============================================================
# KernelAI startup
# ============================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"

API_HOST="127.0.0.1"
API_PORT="8000"

UI_HOST="127.0.0.1"
UI_PORT="8501"


# ============================================================
# Helpers
# ============================================================

log() {
    echo "[KernelAI] $1"
}


cleanup() {
    echo
    log "Shutting down KernelAI..."

    if [ -n "${UI_PID:-}" ]; then
        kill "$UI_PID" 2>/dev/null || true
    fi

    if [ -n "${API_PID:-}" ]; then
        kill "$API_PID" 2>/dev/null || true
    fi

    if [ -n "${MCP_PID:-}" ]; then
        kill "$MCP_PID" 2>/dev/null || true
    fi

    wait 2>/dev/null || true

    log "Shutdown complete."
}


trap cleanup EXIT INT TERM


# ============================================================
# Project root
# ============================================================

cd "$ROOT_DIR"


# ============================================================
# Virtual environment
# ============================================================

if [ ! -d "$VENV_DIR" ]; then
    log "Virtual environment not found:"
    echo "  $VENV_DIR"
    echo
    echo "Create it with:"
    echo "  python3 -m venv .venv"
    exit 1
fi


log "Activating virtual environment."

source "$VENV_DIR/bin/activate"


# ============================================================
# Dependency checks
# ============================================================

if ! command -v uvicorn >/dev/null 2>&1; then
    log "uvicorn is not installed."
    echo "Install it with:"
    echo "  pip install uvicorn"
    exit 1
fi


if ! command -v streamlit >/dev/null 2>&1; then
    log "streamlit is not installed."
    echo "Install it with:"
    echo "  pip install streamlit"
    exit 1
fi


# ============================================================
# Environment
# ============================================================

export API_BASE_URL="http://${API_HOST}:${API_PORT}"


# ============================================================
# Start MCP
# ============================================================

log "Starting MCP server..."

python -m mcp_server.server &

MCP_PID=$!


# Give the MCP process a moment to initialize.
sleep 2


# Verify that the process did not immediately die.

if ! kill -0 "$MCP_PID" 2>/dev/null; then
    log "MCP server failed to start."
    exit 1
fi


log "MCP server started."


# ============================================================
# Start FastAPI
# ============================================================

log "Starting FastAPI..."

uvicorn api.app:app \
    --host "$API_HOST" \
    --port "$API_PORT" \
    --reload &

API_PID=$!


# ============================================================
# Wait for FastAPI
# ============================================================

log "Waiting for FastAPI..."

API_READY=0

for _ in $(seq 1 30); do
    if curl \
        --silent \
        --fail \
        "http://${API_HOST}:${API_PORT}/docs" \
        >/dev/null 2>&1
    then
        API_READY=1
        break
    fi

    # Stop waiting if FastAPI already crashed.
    if ! kill -0 "$API_PID" 2>/dev/null; then
        break
    fi

    sleep 1
done


if [ "$API_READY" -ne 1 ]; then
    log "FastAPI failed to start."
    exit 1
fi


log "FastAPI ready."


# ============================================================
# Start Streamlit
# ============================================================

log "Starting Streamlit..."

streamlit run ui/app.py \
    --server.address "$UI_HOST" \
    --server.port "$UI_PORT" &

UI_PID=$!


# ============================================================
# Wait for Streamlit
# ============================================================

log "Waiting for Streamlit..."

UI_READY=0

for _ in $(seq 1 30); do
    if curl \
        --silent \
        --fail \
        "http://${UI_HOST}:${UI_PORT}" \
        >/dev/null 2>&1
    then
        UI_READY=1
        break
    fi

    if ! kill -0 "$UI_PID" 2>/dev/null; then
        break
    fi

    sleep 1
done


if [ "$UI_READY" -ne 1 ]; then
    log "Streamlit failed to start."
    exit 1
fi


# ============================================================
# Ready
# ============================================================

echo
echo "============================================================"
echo " KernelAI"
echo "============================================================"
echo
echo " MCP"
echo "   Running"
echo
echo " API"
echo "   http://${API_HOST}:${API_PORT}"
echo
echo " API Docs"
echo "   http://${API_HOST}:${API_PORT}/docs"
echo
echo " UI"
echo "   http://${UI_HOST}:${UI_PORT}"
echo
echo " Press Ctrl+C to stop all KernelAI services."
echo
echo "============================================================"
echo


# ============================================================
# Keep services alive
# ============================================================

wait
