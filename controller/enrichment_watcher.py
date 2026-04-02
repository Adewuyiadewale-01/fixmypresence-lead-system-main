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

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

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
ENRICH_THRESHOLD = int(os.environ.get("ENRICH_THRESHOLD", "10"))
WATCHER_INTERVAL_MINUTES = int(os.environ.get("WATCHER_INTERVAL_MINUTES", "10"))

# Valid "live" status values
VALID_LIVE_STATUSES = {"✅", "live", "Live", "LIVE"}

# Paths
_CONTROLLER_DIR = Path(__file__).parent
_PROJECT_ROOT = _CONTROLLER_DIR.parent
ENRICHER_SCRIPT = _PROJECT_ROOT / "Bots" / "enricher" / "enrich_linkedin.py"

# Lock file
LOCK_FILE = _CONTROLLER_DIR / "enrichment_lock.json"


# ============================================================================
# HELPERS
# ============================================================================

def _log(msg: str):
    """Print timestamped log message."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [Watcher] {msg}")


def _auth_client():
    """Authenticate with Google Sheets (lazy import)."""
    import gspread
    from google.oauth2 import service_account
    
    creds = service_account.Credentials.from_service_account_file(
        os.path.expanduser(str(CREDENTIALS_PATH)),
        scopes=SCOPES
    )
    return gspread.authorize(creds)


# ============================================================================
# LOCK (simple file-based, prevents multiple watchers running)
# ============================================================================

def is_locked() -> bool:
    """Check if lock file exists."""
    return LOCK_FILE.exists()


def acquire_lock() -> bool:
    """Acquire lock. Returns False if already locked."""
    if LOCK_FILE.exists():
        return False
    LOCK_FILE.write_text(datetime.now().isoformat())
    return True


def release_lock():
    """Release lock."""
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()


# ============================================================================
# CORE WATCHER LOGIC
# ============================================================================

def count_ready_rows() -> int:
    """Count rows ready for enrichment.
    
    A row is "ready" if:
    - Has a website value
    - Website Status is valid/live (✅)
    
    Returns:
        count of ready rows
    """
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
    
    if col_website is None or col_web_status is None:
        _log("❌ Missing required columns: website or Website Status")
        return 0
    
    # Count ready rows
    all_values = ws.get_all_values()
    ready_count = 0
    
    for row in all_values[1:]:  # Skip header
        website = row[col_website] if col_website < len(row) else ""
        web_status = row[col_web_status] if col_web_status < len(row) else ""
        
        if website.strip() and web_status.strip() in VALID_LIVE_STATUSES:
            ready_count += 1
    
    return ready_count


def run_enricher() -> int:
    """Run the enricher directly (like pressing run button).
    
    The enricher runs in a new session so it survives if the controller is stopped.
    
    Returns:
        0 if started successfully (does NOT wait for completion)
    """
    if not ENRICHER_SCRIPT.exists():
        _log(f"❌ Enricher not found: {ENRICHER_SCRIPT}")
        return 1
    
    # Use project's venv Python if available (has Playwright configured)
    venv_python = _PROJECT_ROOT / ".venv" / "bin" / "python"
    python_exe = str(venv_python) if venv_python.exists() else sys.executable
    
    cmd = [
        python_exe,
        str(ENRICHER_SCRIPT),
        "--drain"  # Process all pending rows using _meta pointer
    ]
    
    _log(f"🚀 Starting enricher (detached): {' '.join(cmd)}")
    
    # Run in new session so it survives if controller is stopped
    # This allows enricher to finish draining even after dashboard Stop
    subprocess.Popen(
        cmd,
        start_new_session=True,  # Detach from parent process group
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    
    _log("✅ Enricher started in background (will drain all pending leads)")
    return 0


def watcher_tick() -> bool:
    """Single watcher tick. Returns True if enrichment was triggered."""
    _log("🔍 Checking for ready rows...")
    
    # Check lock state
    if is_locked():
        _log("🔒 Lock held (enricher running). Skipping this tick.")
        return False
    
    try:
        # Count ready rows
        ready_count = count_ready_rows()
        _log(f"📊 Ready rows: {ready_count} (threshold: {ENRICH_THRESHOLD})")
        
        if ready_count < ENRICH_THRESHOLD:
            _log(f"⏳ Not enough rows. Waiting for {ENRICH_THRESHOLD - ready_count} more.")
            return False
        
        # Acquire lock and run enricher
        if not acquire_lock():
            _log("❌ Could not acquire lock. Another process may be running.")
            return False
        
        try:
            exit_code = run_enricher()
            _log(f"📝 Enricher exit code: {exit_code}")
            return exit_code == 0
            
        finally:
            release_lock()
            _log("🔓 Lock released.")
    
    except Exception as e:
        _log(f"❌ Error during tick: {e}")
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
            lock_time = LOCK_FILE.read_text().strip()
            print(f"Lock Status: 🔒 LOCKED")
            print(f"  Locked At: {lock_time}")
        else:
            print("Lock Status: 🔓 UNLOCKED")
        sys.exit(0)
    
    if args.once:
        result = watcher_tick()
        sys.exit(0 if result else 1)
    
    run_watcher_loop(interval_minutes=args.interval)
