#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python scripts/dashboard_server.py stop "$@"
