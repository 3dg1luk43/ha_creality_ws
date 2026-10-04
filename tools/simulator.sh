#!/usr/bin/env bash
#
# simulator.sh - run the printer simulator as a systemd service, with its control UI.
#
# Usage:
#   tools/simulator.sh on [model] [simulator flags...]   # install, enable and start; wait for the UI
#   tools/simulator.sh off                               # stop and disable
#   tools/simulator.sh restart
#   tools/simulator.sh status
#   tools/simulator.sh logs                              # follow the journal
#
# Examples:
#   tools/simulator.sh on                       # k2plus, printing
#   tools/simulator.sh on k1c --frames full     # flags go to creality_printer_test_server.py
#   PRINT_SECONDS=0 tools/simulator.sh on k2    # idle printer; start prints from the UI
#
# "on" writes the unit with the given model and flags, so the service comes back the same
# after a reboot. Env: MODEL (k2plus), PRINT_SECONDS (one year; 0 = idle),
# CONTROL_PORT (8888), PYTHON (.venv/bin/python, else python3).
#
set -euo pipefail

TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$TOOLS_DIR")"
SERVER="$TOOLS_DIR/creality_printer_test_server.py"
UNIT="creality-simulator.service"
UNIT_FILE="/etc/systemd/system/$UNIT"

MODEL="${MODEL:-k2plus}"
# A one-year print, so the job never ends during a session.
PRINT_SECONDS="${PRINT_SECONDS:-31536000}"
CONTROL_PORT="${CONTROL_PORT:-8888}"
PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"

SUDO=""
[[ $EUID -ne 0 ]] && SUDO="sudo"

# The control port in the installed unit, whatever CONTROL_PORT is now.
unit_port() {
    local port
    port="$(grep -o -- '"--control-port" "[0-9]*"' "$UNIT_FILE" 2>/dev/null | grep -o '[0-9][0-9]*' || true)"
    echo "${port:-$CONTROL_PORT}"
}

print_urls() {
    local ip
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    ip="${ip:-localhost}"
    echo "  Control UI:  http://$ip:$(unit_port)/ui/"
    echo "  Telemetry:   ws://$ip:9999 (add this host to Home Assistant as the printer)"
    echo "  Logs:        $0 logs"
}

# Quote one argument for an ExecStart= line.
unit_quote() {
    local a="${1//\\/\\\\}"
    a="${a//\"/\\\"}"
    printf '"%s"' "${a//%/%%}"
}

render_unit() {
    local cmd="" a
    for a in "$@"; do
        cmd+=" $(unit_quote "$a")"
    done
    cat <<EOF
[Unit]
Description=Creality printer simulator (ha_creality_ws)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT_DIR
ExecStart=${cmd# }
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

wait_ready() {
    local port="$1"
    # The control UI comes up before the printer's listeners, so wait for "powered";
    # give up after 20 s.
    for _ in $(seq 1 40); do
        if curl -fsS "http://127.0.0.1:$port/api/state" 2>/dev/null | grep -Eq '"powered": ?true'; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

start() {
    if [[ $# -gt 0 && "$1" != -* ]]; then
        MODEL="$1"
        shift
    fi
    if [[ ! -x "$PYTHON" ]]; then
        local resolved
        resolved="$(command -v python3 || true)"
        if [[ -z "$resolved" ]]; then
            echo "Error: no Python at '$PYTHON' and no python3 on PATH; set PYTHON=..." >&2
            return 1
        fi
        echo "Note: '$PYTHON' not found; using '$resolved'." >&2
        PYTHON="$resolved"
    fi

    local args=("$PYTHON" "$SERVER" --model "$MODEL" --control-port "$CONTROL_PORT")
    if [[ "$PRINT_SECONDS" != 0 ]]; then
        args+=(--simulate-print --print-seconds "$PRINT_SECONDS")
    fi
    args+=("$@")

    local new
    new="$(render_unit "${args[@]}")"
    if [[ -f "$UNIT_FILE" ]] && [[ "$(cat "$UNIT_FILE")" == "$new" ]] && systemctl is-active --quiet "$UNIT"; then
        echo "Already running (model=$MODEL)."
        print_urls
        return 0
    fi

    echo "Starting $UNIT (model=$MODEL)..."
    printf '%s\n' "$new" | $SUDO tee "$UNIT_FILE" >/dev/null
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable --quiet "$UNIT"
    $SUDO systemctl restart "$UNIT"

    if wait_ready "$CONTROL_PORT"; then
        echo "Started and enabled."
        print_urls
        return 0
    fi
    echo "Failed to start; stopping and disabling. Last journal lines:" >&2
    journalctl -u "$UNIT" -n 20 --no-pager >&2 || true
    stop >/dev/null
    return 1
}

stop() {
    if [[ ! -f "$UNIT_FILE" ]]; then
        echo "Not installed."
        return 0
    fi
    $SUDO systemctl disable --now --quiet "$UNIT"
    echo "Stopped and disabled."
}

status() {
    local enabled
    enabled="$(systemctl is-enabled "$UNIT" 2>/dev/null)" || true
    if systemctl is-active --quiet "$UNIT"; then
        echo "Running (${enabled:-not installed})."
        print_urls
    else
        echo "Not running (${enabled:-not installed})."
    fi
}

cmd="${1:-}"
[[ $# -gt 0 ]] && shift
case "$cmd" in
    on|start)   start "$@" ;;
    off|stop)   stop ;;
    restart)    $SUDO systemctl restart "$UNIT" && wait_ready "$(unit_port)"; status ;;
    status)     status ;;
    logs)       journalctl -u "$UNIT" -n 50 -f ;;
    *)
        echo "Usage: $0 {on|off|restart|status|logs} [model] [simulator flags...]"
        exit 1
        ;;
esac
