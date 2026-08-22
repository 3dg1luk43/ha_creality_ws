#!/usr/bin/env bash
#
# webrtc_test_server.sh — start/stop the Creality test server in the background
# for WebRTC camera testing.
#
# Uses a WebRTC-capable model (k2plus) and an absurdly long print duration so the
# simulated job never finishes on its own during a testing session.
#
# Usage:
#   ./webrtc_test_server.sh on    # start in background
#   ./webrtc_test_server.sh off   # stop
#   ./webrtc_test_server.sh status # show whether it's running
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$SCRIPT_DIR/tools/creality_printer_test_server.py"
PID_FILE="$SCRIPT_DIR/.webrtc_test_server.pid"
LOG_FILE="$SCRIPT_DIR/.webrtc_test_server.log"

# WebRTC-capable model + a ~1 year print so the job never ends mid-session.
MODEL="${MODEL:-k2plus}"
PRINT_SECONDS="${PRINT_SECONDS:-31536000}"
# Default to the project's .venv interpreter; override with PYTHON=... if needed.
PYTHON="${PYTHON:-$SCRIPT_DIR/.venv/bin/python}"

is_running() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid="$(cat "$PID_FILE")"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

start() {
    if is_running; then
        echo "Already running (PID $(cat "$PID_FILE"))."
        return 0
    fi
    # Validate the interpreter up front so a missing venv doesn't fail cryptically.
    if [[ ! -x "$PYTHON" ]] && ! command -v "$PYTHON" >/dev/null 2>&1; then
        local resolved
        resolved="$(command -v python3 || command -v python || true)"
        if [[ -z "$resolved" ]]; then
            echo "Error: Python interpreter not found at '$PYTHON' and no python3/python on PATH." >&2
            echo "       Set PYTHON=/path/to/python and retry." >&2
            return 1
        fi
        echo "Note: '$PYTHON' not found; falling back to '$resolved'." >&2
        PYTHON="$resolved"
    fi
    echo "Starting WebRTC test server (model=$MODEL, print-seconds=$PRINT_SECONDS)..."
    nohup "$PYTHON" "$SERVER" \
        --model "$MODEL" \
        --simulate-print \
        --print-seconds "$PRINT_SECONDS" \
        >"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    sleep 1
    if is_running; then
        echo "Started (PID $(cat "$PID_FILE")). Logs: $LOG_FILE"
        echo "  WebRTC signaling: POST http://localhost:8000/call/webrtc_local"
        echo "  WS telemetry:     ws://localhost:9999"
    else
        echo "Failed to start. Check $LOG_FILE:"
        tail -n 20 "$LOG_FILE" || true
        rm -f "$PID_FILE"
        return 1
    fi
}

stop() {
    if ! is_running; then
        echo "Not running."
        rm -f "$PID_FILE"
        return 0
    fi
    local pid
    pid="$(cat "$PID_FILE")"
    echo "Stopping (PID $pid)..."
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
        is_running || break
        sleep 0.5
    done
    if is_running; then
        echo "Still alive, sending SIGKILL..."
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    echo "Stopped."
}

status() {
    if is_running; then
        echo "Running (PID $(cat "$PID_FILE")). Logs: $LOG_FILE"
    else
        echo "Not running."
    fi
}

case "${1:-}" in
    on|start)   start ;;
    off|stop)   stop ;;
    status)     status ;;
    *)
        echo "Usage: $0 {on|off|status}"
        exit 1
        ;;
esac
