"""Dashboard Settings - Centralized configuration."""

import os
from pathlib import Path

# === PATHS ===
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
CONTROLLER_DIR = PROJECT_ROOT / "controller"
BOTS_DIR = PROJECT_ROOT / "Bots"
LOGS_DIR = PROJECT_ROOT / "logs"

# Ensure logs directory exists
LOGS_DIR.mkdir(exist_ok=True)

# === FILES ===
PID_FILE = PROJECT_ROOT / "dashboard_controller.pid"
SYSTEM_LOG = LOGS_DIR / "system.log"
DASHBOARD_SERVER_PID_FILE = PROJECT_ROOT / "dashboard_server.pid"
DASHBOARD_SERVER_LOG = LOGS_DIR / "dashboard_server.log"
DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8000"))
DASHBOARD_RELOAD = os.environ.get("DASHBOARD_RELOAD", "").strip().lower() in {"1", "true", "yes", "on"}

# === CONTROLLER COMMAND ===
# The exact command to run the master controller.
# Prefer the project virtualenv so the dashboard launches with the same
# interpreter/dependencies as the rest of the app.
_DEFAULT_VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
CONTROLLER_PYTHON = os.environ.get(
    "DASHBOARD_PYTHON",
    str(_DEFAULT_VENV_PYTHON) if _DEFAULT_VENV_PYTHON.exists() else "python3",
)
CONTROLLER_SCRIPT = str(CONTROLLER_DIR / "main.py")
CONTROLLER_FLAGS = []  # Dashboard manages persistent watcher subprocesses separately.

def get_controller_cmd(extra_flags: list[str] | None = None) -> list:
    """Build the controller command from config."""
    return [CONTROLLER_PYTHON, CONTROLLER_SCRIPT] + CONTROLLER_FLAGS + list(extra_flags or [])


WATCHER_SPECS = [
    {
        "key": "enrichment",
        "label": "Enrichment Watcher",
        "script": str(CONTROLLER_DIR / "enrichment_watcher.py"),
        "pid_file": PROJECT_ROOT / "dashboard_enrichment_watcher.pid",
        "log_file": LOGS_DIR / "enrichment_watcher.log",
    },
    {
        "key": "ultra",
        "label": "Ultra Validated Watcher",
        "script": str(CONTROLLER_DIR / "ultra_validated_watcher.py"),
        "pid_file": PROJECT_ROOT / "dashboard_ultra_watcher.pid",
        "log_file": LOGS_DIR / "ultra_validated_watcher.log",
    },
    {
        "key": "scraper_v3",
        "label": "Scraper V3 Watcher",
        "script": str(CONTROLLER_DIR / "scraper_v3_watcher.py"),
        "pid_file": PROJECT_ROOT / "dashboard_scraper_v3_watcher.pid",
        "log_file": LOGS_DIR / "scraper_v3_watcher.log",
    },
]

# === SCHEDULER DEFAULTS (in minutes) ===
# Test values: 4 min run / 3 min rest. For production set CYCLE_RUN_MINUTES=240 CYCLE_REST_MINUTES=120
DEFAULT_RUN_MINUTES = int(os.environ.get("CYCLE_RUN_MINUTES", "4"))
DEFAULT_REST_MINUTES = int(os.environ.get("CYCLE_REST_MINUTES", "3"))

# === STOP TIMEOUT ===
GRACEFUL_STOP_TIMEOUT = 30  # seconds to wait before escalating to SIGKILL
