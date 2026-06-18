#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    git \
    curl \
    ca-certificates \
    xvfb \
    xauth
fi

python3 -m venv .venv
"$PROJECT_ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$PROJECT_ROOT/.venv/bin/python" -m pip install -r requirements.txt

# Keep these explicit in case the root requirements file changes later.
"$PROJECT_ROOT/.venv/bin/python" -m pip install -r Bots/enricher/requirements.txt
"$PROJECT_ROOT/.venv/bin/python" -m pip install -r Bots/maps_scraper/requirements_new_scraper.txt

# Install Playwright browser runtime. Prefer with-deps on Debian/Ubuntu when possible.
if command -v apt-get >/dev/null 2>&1; then
  "$PROJECT_ROOT/.venv/bin/python" -m playwright install --with-deps chromium
else
  "$PROJECT_ROOT/.venv/bin/python" -m playwright install chromium
fi

mkdir -p logs controller/logs secrets

echo "VM bootstrap complete."
echo "Next:"
echo "  1. Place the Google service account JSON in $PROJECT_ROOT/secrets/"
echo "  2. Create /etc/fixmypresence/dashboard.env from deploy/contabo/dashboard.env.example"
echo "  3. Install the systemd unit from deploy/contabo/fixmypresence-dashboard.service"
