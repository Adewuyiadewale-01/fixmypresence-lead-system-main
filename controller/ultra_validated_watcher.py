"""Ultra Validated Watcher - Monitors Ultra_validated tab and triggers keyword/classification pipeline.

This module runs as a background loop, checking the Google Sheet every N minutes
for rows that are ready for keyword detection. When threshold (50) is met, it:
1. Runs keyword_detector.py --drain
2. Runs score_and_classify.py --drain

Usage:
    python ultra_validated_watcher.py --once      # Single check and run if needed
    python ultra_validated_watcher.py             # Continuous loop (every 10 min)
    python ultra_validated_watcher.py --status    # Check lock status
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Lazy imports for gspread to allow --status to work without dependencies
gspread = None

# ============================================================================
# CONFIGURATION
# ============================================================================

_SYSTEM_CONFIG_PATH = Path(__file__).resolve().parents[1] / "Bots" / "common" / "system_config.json"


def _read_system_config() -> dict:
    try:
        data = json.loads(_SYSTEM_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_keyword_watcher_config() -> dict:
    config = _read_system_config()
    enrichment = config.get("enrichment") if isinstance(config.get("enrichment"), dict) else {}
    keyword_scoring = enrichment.get("keyword_scoring") if isinstance(enrichment.get("keyword_scoring"), dict) else {}
    watcher = keyword_scoring.get("watcher") if isinstance(keyword_scoring.get("watcher"), dict) else {}
    return watcher


def _env_int(name: str, config_value, default: int) -> int:
    raw = os.environ.get(name)
    if raw not in (None, ""):
        try:
            return int(raw)
        except ValueError:
            return default
    try:
        return int(config_value)
    except (TypeError, ValueError):
        return default

CREDENTIALS_PATH = Path(
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json")
)
SHEET_ID = os.environ.get(
    "SHEET_ID", "1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc"
)
TAB_NAME = os.environ.get("ULTRA_TAB_NAME", "Ultra_validated")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Watcher settings
# Trigger when 50 rows are ready (website present, but no keyword status)
_WATCHER_CONFIG = _get_keyword_watcher_config()
TRIGGER_THRESHOLD = max(1, _env_int("ULTRA_TRIGGER_THRESHOLD", _WATCHER_CONFIG.get("trigger_threshold"), 50))
WATCHER_INTERVAL_MINUTES = max(1, _env_int("WATCHER_INTERVAL_MINUTES", _WATCHER_CONFIG.get("watcher_interval_minutes"), 10))
# Stale lock timeout - auto-clear locks older than this (prevents permanent blockage from crashes)
LOCK_TIMEOUT_MINUTES = max(1, _env_int("LOCK_TIMEOUT_MINUTES", _WATCHER_CONFIG.get("lock_timeout_minutes"), 30))

# Paths
_CONTROLLER_DIR = Path(__file__).parent
_PROJECT_ROOT = _CONTROLLER_DIR.parent
KEYWORD_DETECTOR_SCRIPT = _PROJECT_ROOT / "Bots" / "enricher" / "keyword_detector.py"
SCORE_CLASSIFY_SCRIPT = _PROJECT_ROOT / "Bots" / "enricher" / "score_and_classify.py"

# Lock file
LOCK_FILE = _CONTROLLER_DIR / "ultra_validated_lock.json"


# ============================================================================
# HELPERS
# ============================================================================

def _log(msg: str):
    """Print timestamped log message."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [Watcher] {msg}")


def _auth_client():
    """Authenticate with Google Sheets (lazy import)."""
    global gspread
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

def _get_lock_age_minutes() -> float | None:
    """Get age of lock file in minutes. Returns None if no lock or parse error."""
    if not LOCK_FILE.exists():
        return None
    try:
        lock_time = datetime.fromisoformat(LOCK_FILE.read_text().strip())
        age = (datetime.now() - lock_time).total_seconds() / 60
        return age
    except Exception:
        return None


def is_locked() -> bool:
    """Check if lock file exists and is still valid (not stale)."""
    if not LOCK_FILE.exists():
        return False
    
    # Auto-clear stale locks to recover from crashes/interrupts
    age_minutes = _get_lock_age_minutes()
    if age_minutes is not None and age_minutes > LOCK_TIMEOUT_MINUTES:
        _log(f"⚠️ Stale lock detected ({age_minutes:.1f} min old, threshold: {LOCK_TIMEOUT_MINUTES}m). Auto-clearing...")
        release_lock()
        return False
    
    return True


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
    """Count rows ready for keyword detection.
    
    A row is "ready" if:
    - Has a website value
    - 'Keyword status' is empty
    
    Returns:
        count of ready rows
    """
    gc = _auth_client()
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(TAB_NAME)
    except gspread.WorksheetNotFound:
        _log(f"❌ Tab '{TAB_NAME}' not found.")
        return 0
    
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
    col_kw_status = find_col("keyword status")
    
    if col_website is None:
        _log("❌ Missing required column: website")
        return 0
    
    # If keyword status column doesn't exist yet, then ALL rows with websites are ready
    # (Checking if column exists in header)
    kw_status_exists = (col_kw_status is not None)
    
    # Count ready rows
    all_values = ws.get_all_values()
    ready_count = 0
    
    # Skip header
    for row in all_values[1:]:
        website = row[col_website] if col_website < len(row) else ""
        
        # Check keyword status
        if kw_status_exists and col_kw_status < len(row):
            kw_status = row[col_kw_status]
        else:
            kw_status = ""
            
        if website.strip() and not kw_status.strip():
            ready_count += 1
    
    return ready_count


def run_pipeline() -> bool:
    """Run the pipeline (Keyword Detector -> Score & Classify).
    
    Returns:
        True if all successful
    """
    # 1. Run Keyword Detector
    if not KEYWORD_DETECTOR_SCRIPT.exists():
        _log(f"❌ Script not found: {KEYWORD_DETECTOR_SCRIPT}")
        return False
        
    _log("🚀 Starting Keyword Detector...")
    venv_python = _PROJECT_ROOT / ".venv" / "bin" / "python"
    python_exe = str(venv_python) if venv_python.exists() else sys.executable
    
    cmd_kw = [python_exe, str(KEYWORD_DETECTOR_SCRIPT), "--drain"]
    exit_kw = subprocess.call(cmd_kw)
    
    if exit_kw != 0:
        _log(f"❌ Keyword Detector failed with exit code {exit_kw}")
        return False
    
    _log("✅ Keyword Detector finished.")
    
    # 2. Run Score & Classify
    if not SCORE_CLASSIFY_SCRIPT.exists():
        _log(f"❌ Script not found: {SCORE_CLASSIFY_SCRIPT}")
        return False
        
    _log("🚀 Starting Score & Classify...")
    cmd_score = [python_exe, str(SCORE_CLASSIFY_SCRIPT), "--drain"]
    exit_score = subprocess.call(cmd_score)
    
    if exit_score != 0:
        _log(f"❌ Score & Classify failed with exit code {exit_score}")
        return False
        
    _log("✅ Score & Classify finished.")
    return True


def watcher_tick() -> bool:
    """Single watcher tick. Returns True if pipeline was triggered."""
    _log("🔍 Checking for rows ready for keyword detection...")
    
    # Check lock state
    if is_locked():
        _log("🔒 Lock held (pipeline running). Skipping this tick.")
        return False
    
    try:
        # Count ready rows
        ready_count = count_ready_rows()
        _log(f"📊 Ready rows: {ready_count} (threshold: {TRIGGER_THRESHOLD})")
        
        if ready_count < TRIGGER_THRESHOLD:
            _log(f"⏳ Not enough rows. Waiting for {TRIGGER_THRESHOLD - ready_count} more.")
            return False
        
        # Acquire lock and run pipeline
        if not acquire_lock():
            _log("❌ Could not acquire lock. Another process may be running.")
            return False
        
        try:
            success = run_pipeline()
            return success
            
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
    
    _log(f"🚀 Starting Ultra Validated watcher loop (interval: {interval_minutes}m)")
    
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
    
    p = argparse.ArgumentParser(description="Ultra Validated Watcher")
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
