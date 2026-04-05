"""Enrichment Watcher - Monitors Validated_leads and triggers LinkedIn enrichment.

This module runs as a background loop, checking the Google Sheet every N minutes
for rows that are ready for enrichment. When enough ready rows are found, it
runs the enricher with --drain mode (which uses the _meta.next_row pointer).

Usage:
    python enrichment_watcher.py --once      # Single check and run if needed
    python enrichment_watcher.py             # Continuous loop (every 10 min)
    python enrichment_watcher.py --status    # Check lock status
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Lazy imports for gspread to allow --status to work without dependencies
gspread = None

# ============================================================================
# CONFIGURATION
# ============================================================================

CREDENTIALS_PATH = Path(
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json")
)
SHEET_ID = os.environ.get(
    "SHEET_ID", "1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc"
)
TAB_NAME = os.environ.get("TAB_NAME", "Validated_leads")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Watcher settings
MIN_BACKLOG_TO_DRAIN = int(os.environ.get("MIN_BACKLOG_TO_DRAIN", "200"))
MAX_WAIT_BEFORE_FORCED_DRAIN_MINUTES = int(os.environ.get("MAX_WAIT_BEFORE_FORCED_DRAIN_MINUTES", "60"))
WATCHER_INTERVAL_MINUTES = int(os.environ.get("WATCHER_INTERVAL_MINUTES", "10"))

# Valid "live" status values
VALID_LIVE_STATUSES = {"✅", "live", "Live", "LIVE"}

# Paths
_CONTROLLER_DIR = Path(__file__).parent
_PROJECT_ROOT = _CONTROLLER_DIR.parent
ENRICHER_SCRIPT = _PROJECT_ROOT / "Bots" / "enricher" / "enrich_linkedin.py"
LOG_DIR = _CONTROLLER_DIR / "logs"

# Lock file
LOCK_FILE = _CONTROLLER_DIR / "enrichment_lock.json"

# In-memory backlog timer (monotonic clock, safe against wall-clock changes)
_backlog_since_monotonic: Optional[float] = None


# ============================================================================
# HELPERS
# ============================================================================

def _log(msg: str):
    """Print timestamped log message."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [Watcher] {msg}")


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _auth_client():
    """Authenticate with Google Sheets (lazy import)."""
    import gspread
    from google.oauth2 import service_account
    
    creds = service_account.Credentials.from_service_account_file(
        os.path.expanduser(str(CREDENTIALS_PATH)),
        scopes=SCOPES
    )
    return gspread.authorize(creds)


def _build_enricher_log_path() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOG_DIR / f"enrich_linkedin_{stamp}.log"


# ============================================================================
# LOCK (simple file-based, prevents multiple watchers running)
# ============================================================================

def _read_lock() -> dict | None:
    if not LOCK_FILE.exists():
        return None
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        with contextlib.suppress(OSError):
            LOCK_FILE.unlink()
        return None


def is_locked() -> bool:
    """Check whether a live process currently owns the lock."""
    data = _read_lock()
    if not data:
        return False
    pid = data.get("pid")
    if not isinstance(pid, int) or not _pid_is_alive(pid):
        release_lock()
        return False
    return True


def acquire_lock(pid: int, reason: str) -> bool:
    """Acquire lock for a live process. Returns False if already locked."""
    if is_locked():
        return False
    payload = {
        "pid": pid,
        "reason": reason,
        "locked_at": datetime.now().isoformat(),
    }
    try:
        with open(LOCK_FILE, "x", encoding="utf-8") as f:
            json.dump(payload, f)
        return True
    except FileExistsError:
        return False


def update_lock_pid(pid: int, reason: str):
    """Update lock ownership after the detached child has started."""
    payload = {
        "pid": pid,
        "reason": reason,
        "locked_at": datetime.now().isoformat(),
    }
    LOCK_FILE.write_text(json.dumps(payload), encoding="utf-8")


def release_lock():
    """Release lock."""
    with contextlib.suppress(OSError):
        LOCK_FILE.unlink()


# ============================================================================
# CORE WATCHER LOGIC
# ============================================================================

def count_ready_rows() -> int:
    """Count rows with pending LinkedIn enrichment/backfill work."""
    gc = _auth_client()
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(TAB_NAME)
    
    headers = [h.strip().lower() for h in ws.row_values(1)]
    
    # Find column indices
    def find_col(*names):
        for name in names:
            name_lc = name.lower()
            for i, h in enumerate(headers):
                if h == name_lc:
                    return i
        return None
    
    col_website = find_col("website")
    col_web_status = find_col("website status")
    col_linkedin = find_col("linkedin")
    col_status = find_col("linkedin status")
    col_emp_count = find_col("linkedin employees")
    col_emp_link = find_col("linkedin people link")
    col_company_id = find_col("linkedin company id")

    if col_website is None or col_web_status is None:
        _log("❌ Missing required columns: website or Website Status")
        return 0

    all_values = ws.get_all_values()
    ready_count = 0

    for row in all_values[1:]:
        def v(col_idx: Optional[int]) -> str:
            if col_idx is None or col_idx >= len(row):
                return ""
            return row[col_idx].strip()

        website = v(col_website)
        web_status = v(col_web_status)
        linkedin_val = v(col_linkedin)
        status_val = v(col_status)
        emp_count_v = v(col_emp_count)
        emp_link_v = v(col_emp_link)
        company_id_v = v(col_company_id)

        if not website or web_status not in VALID_LIVE_STATUSES:
            continue

        has_formula = linkedin_val.startswith("=")
        has_real_li = bool(linkedin_val) and not has_formula and "linkedin.com" in linkedin_val.lower()
        is_none = status_val == "NONE"
        needs_link_lookup = (not has_real_li) and not is_none
        employee_missing = not (emp_count_v and emp_link_v and company_id_v)
        needs_backfill = has_real_li and employee_missing

        if needs_link_lookup or needs_backfill:
            ready_count += 1

    return ready_count


def run_enricher() -> int:
    """Start the detached enricher and return its child PID."""
    if not ENRICHER_SCRIPT.exists():
        _log(f"❌ Enricher not found: {ENRICHER_SCRIPT}")
        return 0
    
    # Use project's venv Python if available (has Playwright configured)
    venv_python = _PROJECT_ROOT / ".venv" / "bin" / "python"
    python_exe = str(venv_python) if venv_python.exists() else sys.executable
    
    cmd = [
        python_exe,
        str(ENRICHER_SCRIPT),
        "--drain"  # Process all pending rows using _meta pointer
    ]

    log_path = _build_enricher_log_path()
    _log(f"🚀 Starting enricher (detached): {' '.join(cmd)}")
    _log(f"📝 Enricher log: {log_path}")

    # Run in new session so it survives if controller is stopped
    # This allows enricher to finish draining even after dashboard Stop
    log_handle = open(log_path, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,  # Detach from parent process group
            stdout=log_handle,
            stderr=log_handle,
        )
    finally:
        log_handle.close()
    
    _log(f"✅ Enricher started in background (pid={proc.pid}, will drain all pending leads)")
    return proc.pid


def watcher_tick() -> bool:
    """Single watcher tick. Returns True if enrichment was triggered."""
    global _backlog_since_monotonic

    _log("🔍 Checking for ready rows...")

    # Check lock state
    if is_locked():
        _log("🔒 Lock held (enricher running). Skipping this tick.")
        return False

    try:
        ready_count = count_ready_rows()

        # --- No backlog: reset timer and wait ---
        if ready_count == 0:
            if _backlog_since_monotonic is not None:
                _log("📭 Backlog cleared. Resetting timer.")
            _backlog_since_monotonic = None
            _log("📊 Ready rows: 0. Nothing to do.")
            return False

        # --- Determine which trigger fires ---
        threshold_met = ready_count >= MIN_BACKLOG_TO_DRAIN

        # Start timer on first sighting of a non-zero backlog
        if _backlog_since_monotonic is None:
            _backlog_since_monotonic = time.monotonic()

        waited_minutes = (time.monotonic() - _backlog_since_monotonic) / 60
        wait_exceeded = waited_minutes >= MAX_WAIT_BEFORE_FORCED_DRAIN_MINUTES

        if threshold_met:
            trigger = "threshold"
            _log(f"📊 Ready rows: {ready_count} >= {MIN_BACKLOG_TO_DRAIN}. Draining (threshold reached).")
        elif wait_exceeded:
            trigger = "timeout"
            _log(
                f"📊 Ready rows: {ready_count} (below {MIN_BACKLOG_TO_DRAIN}), "
                f"but backlog waiting {waited_minutes:.0f}m >= {MAX_WAIT_BEFORE_FORCED_DRAIN_MINUTES}m. "
                f"Draining (max wait exceeded)."
            )
        else:
            remaining = MAX_WAIT_BEFORE_FORCED_DRAIN_MINUTES - waited_minutes
            _log(
                f"📊 Ready rows: {ready_count}/{MIN_BACKLOG_TO_DRAIN}. "
                f"Waiting ({remaining:.0f}m until forced drain)."
            )
            return False

        # --- Drain ---
        if not acquire_lock(os.getpid(), "launching_enricher"):
            _log("❌ Could not acquire lock. Another process may be running.")
            _backlog_since_monotonic = None  # reset to avoid stale forced-drain loop
            return False

        try:
            child_pid = run_enricher()
            if not child_pid:
                release_lock()
                _backlog_since_monotonic = None
                return False
            update_lock_pid(child_pid, "enricher_drain")
            _log(f"📝 Enricher started (trigger: {trigger}, pid={child_pid})")
            _backlog_since_monotonic = None  # reset after launch
            return True
        except Exception:
            _backlog_since_monotonic = None  # reset on failure
            release_lock()
            raise

    except Exception as e:
        _log(f"❌ Error during tick: {e}")
        _backlog_since_monotonic = None  # reset to avoid stale loop
        return False


def run_watcher_loop(
    interval_minutes: int = WATCHER_INTERVAL_MINUTES,
    max_ticks: Optional[int] = None
):
    """Main watcher loop. Runs continuously until interrupted.
    
    Args:
        interval_minutes: Time between checks
        max_ticks: If set, stop after this many ticks (for testing)
    """
    import time
    
    _log(f"🚀 Starting watcher loop (interval: {interval_minutes}m)")
    
    tick_count = 0
    while True:
        try:
            watcher_tick()
        except Exception as e:
            _log(f"❌ Tick error: {e}")
        
        tick_count += 1
        if max_ticks and tick_count >= max_ticks:
            _log(f"🛑 Reached max ticks ({max_ticks}). Stopping.")
            break
        
        _log(f"💤 Sleeping for {interval_minutes} minutes...")
        time.sleep(interval_minutes * 60)


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    p = argparse.ArgumentParser(description="Enrichment Watcher")
    p.add_argument("--once", action="store_true", help="Run single tick then exit")
    p.add_argument("--status", action="store_true", help="Check lock status")
    p.add_argument("--interval", type=int, default=WATCHER_INTERVAL_MINUTES,
                   help=f"Minutes between checks (default: {WATCHER_INTERVAL_MINUTES})")
    args = p.parse_args()
    
    if args.status:
        if is_locked():
            lock_data = _read_lock() or {}
            print(f"Lock Status: 🔒 LOCKED")
            print(f"  PID: {lock_data.get('pid')}")
            print(f"  Reason: {lock_data.get('reason')}")
            print(f"  Locked At: {lock_data.get('locked_at')}")
        else:
            print("Lock Status: 🔓 UNLOCKED")
        sys.exit(0)
    
    if args.once:
        try:
            watcher_tick()
            sys.exit(0)  # healthy tick (whether drain triggered or not)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    
    run_watcher_loop(interval_minutes=args.interval)
