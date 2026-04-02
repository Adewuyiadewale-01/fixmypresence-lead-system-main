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

# === CONTROLLER COMMAND ===
# The exact command to run the master controller.
# Modify this if you need different flags or a different Python path.
CONTROLLER_PYTHON = os.environ.get("DASHBOARD_PYTHON", "python3")
CONTROLLER_SCRIPT = str(CONTROLLER_DIR / "main.py")
CONTROLLER_FLAGS = ["--with-watcher"]  # Add/remove flags as needed

def get_controller_cmd() -> list:
    """Build the controller command from config."""
    return [CONTROLLER_PYTHON, CONTROLLER_SCRIPT] + CONTROLLER_FLAGS

# === SCHEDULER DEFAULTS ===
DEFAULT_RUN_HOURS = int(os.environ.get("CYCLE_RUN_HOURS", "4"))
DEFAULT_REST_HOURS = int(os.environ.get("CYCLE_REST_HOURS", "2"))

# === STOP TIMEOUT ===
GRACEFUL_STOP_TIMEOUT = 30  # seconds to wait before escalating to SIGKILL
