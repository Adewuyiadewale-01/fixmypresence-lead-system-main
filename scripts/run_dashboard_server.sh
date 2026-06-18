#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing virtualenv python at $PYTHON_BIN" >&2
  exit 1
fi

HOST="${DASHBOARD_HOST:-127.0.0.1}"
PORT="${DASHBOARD_PORT:-8000}"
USE_XVFB="${DASHBOARD_USE_XVFB:-0}"
XVFB_DISPLAY="${XVFB_DISPLAY:-:99}"
XVFB_SCREEN="${XVFB_SCREEN:-1920x1080x24}"

if [[ "$USE_XVFB" == "1" ]]; then
  if command -v xvfb-run >/dev/null 2>&1; then
    exec xvfb-run \
      --auto-servernum \
      --server-num="${XVFB_DISPLAY#:}" \
      --server-args="-screen 0 ${XVFB_SCREEN}" \
      "$PYTHON_BIN" -m uvicorn dashboard.api:app --host "$HOST" --port "$PORT"
  fi
  echo "DASHBOARD_USE_XVFB=1 but xvfb-run is not installed." >&2
  exit 1
fi

exec "$PYTHON_BIN" -m uvicorn dashboard.api:app --host "$HOST" --port "$PORT"
