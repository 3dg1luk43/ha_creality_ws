#!/usr/bin/env bash
# Bring up the Creality test box and leave it ready to drive.
#
#   ./up.sh            start (keeps any existing config/)
#   ./up.sh --fresh    wipe config/ first: a Home Assistant that has never seen the printer
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
VENV_PY="$REPO/.venv/bin/python3"
FRESH=0
[ "${1:-}" = "--fresh" ] && FRESH=1

cd "$HERE"

if [ "$FRESH" = 1 ] && [ -d config ]; then
  echo "== wiping config/"
  docker compose down --remove-orphans >/dev/null 2>&1 || true
  rm -rf config
fi

mkdir -p config
[ -f config/configuration.yaml ] || cp support/configuration.yaml config/configuration.yaml
: > config/notify_capture.jsonl
: > config/push_capture.jsonl

echo "== starting the mock printer and Home Assistant"
docker compose up -d --build

"$VENV_PY" "$HERE/hactl.py" wait --timeout 240
"$VENV_PY" "$HERE/hactl.py" onboard

cat <<EOT

box:     http://127.0.0.1:8322   (user testbox / testbox-pw-0123)
printer: 172.31.77.10 inside the box; test control on http://127.0.0.1:8323
drive:   $VENV_PY $HERE/hactl.py --help
EOT
