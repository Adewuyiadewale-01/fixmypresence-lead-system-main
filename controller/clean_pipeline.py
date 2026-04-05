"""Pipeline Cleanup Utility - Resets system state for fresh runs.

Usage:
    python clean_pipeline.py --help
    python clean_pipeline.py --kill
    python clean_pipeline.py --kill --reset-enricher
    python clean_pipeline.py --kill --reset-enricher --reset-ultra --clear-dest
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List

import gspread
from google.oauth2.service_account import Credentials

# === CONFIG ===
CREDS_PATH = Path(
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json")
)
SHEET_ID = os.environ.get(
    "SHEET_ID", "1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc"
)
DEST_SHEET_ID = os.environ.get(
    "DEST_SHEET_ID", "1YF8WvLTPu-Raf22rHaaphauj-w-n64CRu83ZeAtHYAc"
)

# Tab Names
TAB_LEADS = "Leads"
TAB_VALIDATED = os.environ.get("TAB_NAME", "Validated_leads")
TAB_ULTRA = os.environ.get("ULTRA_TAB_NAME", "Ultra_validated")
TAB_META = "_meta"
TAB_CONFIG = "config"

# Destination Tabs
DEST_TABS = ["Alpha", "Beta", "Gamma", "I-gamma", "pBin", "Hydro", "hydro_formular", "Hydro_formula"]

# Lock Files
LOCK_FILES = [
    "enrichment_lock.json",
    "ultra_validated_lock.json",
    "scraper_v3_lock.json",
]

# Processes to kill
PROCESS_NAMES = [
    "enrichment_watcher.py",
    "ultra_validated_watcher.py",
    "scraper_v3_watcher.py",
    "enrich_linkedin.py",
    "keyword_detector.py",
    "score_and_classify.py",
    "scraper_v3.py",
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _log(msg: str):
    print(f"[Cleanup] {msg}")


def auth_client():
    creds = Credentials.from_service_account_file(
        os.path.expanduser(str(CREDS_PATH)), scopes=SCOPES
    )
    return gspread.authorize(creds)


def kill_processes():
    _log("🔪 Killing processes...")
    cmd = ["pkill", "-f", "|".join(PROCESS_NAMES)]
    try:
        subprocess.run(cmd, check=False, capture_output=True)
        time.sleep(1) # Wait for them to die
        _log("✅ Sent kill signal to watchers and bots.")
    except Exception as e:
        _log(f"⚠️ Error killing processes: {e}")

    # Remove lock files
    controller_dir = Path(__file__).parent
    for lock_name in LOCK_FILES:
        lock_path = controller_dir / lock_name
        if lock_path.exists():
            try:
                lock_path.unlink()
                _log(f"🔓 Removed lock file: {lock_name}")
            except Exception as e:
                _log(f"❌ Failed to remove lock {lock_name}: {e}")


def reset_enricher(client):
    _log("🧹 Resetting Enricher (Validated_leads)...")
    sh = client.open_by_key(SHEET_ID)
    
    # 1. Reset Pointer
    try:
        meta = sh.worksheet(TAB_META)
        cell = meta.find("next_row")
        if cell:
            meta.update_cell(cell.row, 2, "2")
            _log("  - Reset _meta.next_row to 2")
    except gspread.WorksheetNotFound:
        _log("  - _meta tab not found, skipping pointer reset.")
    except Exception as e:
        _log(f"  ❌ Error resetting pointer: {e}")

    # 2. Clear Columns
    try:
        ws = sh.worksheet(TAB_VALIDATED)
        headers = [h.strip().lower() for h in ws.row_values(1)]
        
        cols_to_clear = [
            "linkedin", "linkedin status", "last checked", 
            "linkedin employees", "linkedin people link", 
            "company linkedin"
        ]
        
        indices = []
        for col_name in cols_to_clear:
            if col_name in headers:
                indices.append(headers.index(col_name) + 1)
        
        if indices:
            # Clear manually or via batch update?
            # Batch update is safer for large sheets
            updates = []
            row_count = len(ws.get_all_values())
            if row_count > 1:
                for col_idx in indices:
                    # Update usage requires range string: Sheet!C2:C1000
                    # Using gspread batch_clear is arguably easier if available, 
                    # but batch_update with empty strings is standard.
                    # Or simpler: ws.batch_clear(["C2:C...", "D2:D..."])
                    
                    letter = gspread.utils.rowcol_to_a1(1, col_idx) # Get 'A', 'B' etc
                    letter = letter.replace("1", "") # just the letter
                    ranges = [f"{letter}2:{letter}{row_count}"]
                    ws.batch_clear(ranges)
                _log(f"  - Cleared {len(indices)} columns in {TAB_VALIDATED}")
            else:
                _log("  - No data rows to clear.")
        else:
            _log("  - No target columns found to clear.")
            
    except Exception as e:
        _log(f"  ❌ Error clearing columns: {e}")


def clear_validated(client):
    """Clear ALL data from the Validated_leads tab (keeps header row)."""
    _log("💣 Clearing Validated_leads tab (ALL data)...")
    try:
        sh = client.open_by_key(SHEET_ID)
        ws = sh.worksheet(TAB_VALIDATED)
        row_count = len(ws.get_all_values())
        if row_count > 1:
            ws.batch_clear(["A2:Z10000"])
            _log(f"  - Cleared {row_count - 1} rows from {TAB_VALIDATED}")
        else:
            _log("  - Validated_leads tab already empty.")
    except gspread.WorksheetNotFound:
        _log(f"  ⚠️ {TAB_VALIDATED} tab not found.")
    except Exception as e:
        _log(f"  ❌ Error clearing {TAB_VALIDATED}: {e}")


def reset_ultra(client):
    _log("🧹 Resetting Ultra Validated...")
    sh = client.open_by_key(SHEET_ID)

    # 1. Reset Pointer
    try:
        meta = sh.worksheet(TAB_META)
        cell = meta.find("keyword_next_row")
        if cell:
            meta.update_cell(cell.row, 2, "2")
            _log("  - Reset _meta.keyword_next_row to 2")
    except Exception as e:
        pass

    # 2. Clear ALL Data (keep header)
    try:
        ws = sh.worksheet(TAB_ULTRA)
        ws.resize(rows=2)
        ws.batch_clear(["A2:Z1000"])
        _log(f"  - Cleared ALL data in {TAB_ULTRA}")
    except Exception as e:
        _log(f"  ❌ Error clearing {TAB_ULTRA}: {e}")


def clear_dest(client):
    _log("💥 Clearing Destination Tabs (Alpha, Beta, etc)...")
    try:
        dest_book = client.open_by_key(DEST_SHEET_ID)
    except Exception as e:
        _log(f"❌ Could not open dest sheet: {e}")
        return

    # 1. Clear Tabs
    for tab in DEST_TABS:
        try:
            ws = dest_book.worksheet(tab)
            # Keep header row (1)
            ws.resize(rows=2) # Shrink to 2 rows
            ws.batch_clear(["A2:Z1000"]) # Clear content
            _log(f"  - Cleared {tab}")
        except gspread.WorksheetNotFound:
            pass
        except Exception as e:
            _log(f"  ❌ Error clearing {tab}: {e}")

    # 2. Reset Config
    try:
        config_ws = dest_book.worksheet(TAB_CONFIG)
        # Reset all next_row_* to 2
        cells = config_ws.findall("next_row_")
        for cell in cells: # these are the keys in col A
            config_ws.update_cell(cell.row, 2, "2")
        _log("  - Reset config counters to 2")
    except Exception as e:
        _log(f"  ❌ Error resetting config: {e}")


def clear_leads(client):
    """Clear ALL data from the Leads tab (keeps header row)."""
    _log("💣 Clearing Leads tab (raw scraped data)...")
    try:
        sh = client.open_by_key(SHEET_ID)
        ws = sh.worksheet(TAB_LEADS)
        row_count = len(ws.get_all_values())
        if row_count > 1:
            ws.batch_clear(["A2:Z10000"])
            _log(f"  - Cleared {row_count - 1} rows from {TAB_LEADS}")
        else:
            _log("  - Leads tab already empty.")
    except gspread.WorksheetNotFound:
        _log(f"  ⚠️ {TAB_LEADS} tab not found.")
    except Exception as e:
        _log(f"  ❌ Error clearing {TAB_LEADS}: {e}")


def main():
    p = argparse.ArgumentParser(description="Pipeline Cleanup")
    p.add_argument("--kill", action="store_true", help="Kill processes and remove locks")
    p.add_argument("--reset-enricher", action="store_true", help="Reset Validated_leads (pointers + some cols)")
    p.add_argument("--reset-ultra", action="store_true", help="Reset Ultra_validated (pointers + cols)")
    p.add_argument("--clear-dest", action="store_true", help="Clear destination tabs (Alpha/Beta/etc)")
    p.add_argument("--clear-leads", action="store_true", help="Clear raw Leads tab (scraped data)")
    p.add_argument("--clear-validated", action="store_true", help="Clear Validated_leads tab (ALL data)")
    p.add_argument("--all", action="store_true", help="Do EVERYTHING (nuclear reset)")
    
    args = p.parse_args()
    
    if len(sys.argv) == 1:
        p.print_help()
        sys.exit(0)
    
    if args.all:
        args.kill = True
        args.reset_enricher = False  # Don't use partial reset for nuclear
        args.reset_ultra = True
        args.clear_dest = True
        args.clear_leads = True
        args.clear_validated = True  # Use full clear instead
        
    start_time = time.time()
    
    if args.kill:
        kill_processes()
        
    if args.reset_enricher or args.reset_ultra or args.clear_dest or args.clear_leads or args.clear_validated:
        client = auth_client()
        
        if args.reset_enricher:
            reset_enricher(client)
            
        if args.reset_ultra:
            reset_ultra(client)
            
        if args.clear_dest:
            print("⚠️  WARNING: You are about to DELETE ALL DATA in destination tabs (Alpha, Beta, ...).")
            print("    Waiting 3 seconds... Ctrl+C to cancel.")
            time.sleep(3)
            clear_dest(client)
        
        if args.clear_leads:
            print("💣 WARNING: You are about to DELETE ALL LEADS from the Leads tab!")
            print("    Waiting 3 seconds... Ctrl+C to cancel.")
            time.sleep(3)
            clear_leads(client)
        
        if args.clear_validated:
            print("💣 WARNING: You are about to DELETE ALL DATA from Validated_leads!")
            print("    Waiting 3 seconds... Ctrl+C to cancel.")
            time.sleep(3)
            clear_validated(client)

    elapsed = time.time() - start_time
    _log(f"✨ Done in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
