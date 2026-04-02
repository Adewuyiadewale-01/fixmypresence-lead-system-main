"""Score and Classify - Classifies leads and moves them to destination tabs.

Reads from Ultra_validated tab, classifies leads based on keyword status
and team size, moves them to appropriate destination tabs (Alpha/Beta/Gamma/etc),
and marks source rows as "MOVED".

Usage:
    python score_and_classify.py              # Process one batch (5 rows)
    python score_and_classify.py --drain      # Process all pending rows
"""

import os
import time
import gspread
from google.oauth2.service_account import Credentials

# === CONFIG ===
CREDS_PATH = os.path.expanduser(
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json")
)

SOURCE_SHEET_ID = os.environ.get(
    "SHEET_ID", "1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc"
)
SOURCE_TAB_NAME = os.environ.get("TAB_NAME", "Ultra_validated")

DEST_SHEET_ID = os.environ.get(
    "DEST_SHEET_ID", "1YF8WvLTPu-Raf22rHaaphauj-w-n64CRu83ZeAtHYAc"
)
CONFIG_TAB_NAME = "config"

BATCH_SIZE = 50

# Column indices (0-based for Python list access)
COL_NAME = 0
COL_WEBSITE = 1
COL_LINKEDIN = 2
COL_TEAM_SIZE = 3
COL_PEOPLE_LINK = 4
COL_KW_STATUS = 5  # Column F: Keyword status
COL_MOVED = 6      # Column G: MOVED marker

DEST_TABS = {
    "Alpha": "Alpha",
    "Beta": "Beta",
    "Gamma": "Gamma",
    "I-gamma": "I-gamma",
    "pBin": "pBin",
    "Hydro": "Hydro"
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]


def auth_client():
    """Authenticate with Google Sheets."""
    creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)


def classify(kw_status: str, team_size: int, linkedin_url: str) -> str:
    """Classify a lead based on keyword status and team size."""
    # Hydro Check (Broken/Formula links)
    if not linkedin_url or linkedin_url.startswith("="):
        return "Hydro"
    
    # Category Logic
    if kw_status == "cat 2":
        return "pBin"

    if kw_status == "cat 1":
        if team_size < 15:
            return "Alpha"
        elif team_size < 65:
            return "Beta"
        elif team_size < 250:
            return "Gamma"
        else:
            return "I-gamma"
            
    # Default fallback
    return "pBin"


def get_or_create_config_sheet(dest_book):
    """Get or create the config worksheet."""
    try:
        ws = dest_book.worksheet(CONFIG_TAB_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = dest_book.add_worksheet(title=CONFIG_TAB_NAME, rows=20, cols=5)
        ws.update("A1:B1", [["key", "value"]])
        init_rows = [[f"next_row_{tab}", 2] for tab in DEST_TABS.keys()]
        ws.update(f"A2:B{1 + len(init_rows)}", init_rows)
    return ws


def read_config(config_ws):
    """Read config key-value pairs from config worksheet."""
    data = config_ws.get_all_values()
    config = {}
    if len(data) > 1:
        for row in data[1:]:
            if len(row) >= 2 and row[0]:
                try:
                    config[row[0]] = int(row[1])
                except:
                    config[row[0]] = row[1]
    return config


def update_config_value(config_ws, key, value):
    """Update a config value in the config worksheet."""
    cell = config_ws.find(key)
    if cell:
        config_ws.update_cell(cell.row, 2, value)
    else:
        config_ws.append_row([key, value])


def main() -> bool:
    """Process one batch of rows. Returns True if work was done."""
    client = auth_client()

    source_ws = client.open_by_key(SOURCE_SHEET_ID).worksheet(SOURCE_TAB_NAME)
    dest_book = client.open_by_key(DEST_SHEET_ID)
    config_ws = get_or_create_config_sheet(dest_book)
    config = read_config(config_ws)

    # Read all data
    all_rows = source_ws.get_all_values()
    if len(all_rows) < 2:
        print("No data in source.")
        return False

    data_rows = all_rows[1:]  # Skip header
    
    # Find rows to process (have keyword status, not yet MOVED)
    results = {k: [] for k in DEST_TABS.keys()}
    rows_to_mark_done = []
    processed_count = 0

    for i, row in enumerate(data_rows):
        if processed_count >= BATCH_SIZE:
            break
            
        actual_row_num = i + 2  # +2 because of header and 0-index
        
        # Ensure row has enough columns (up to G)
        while len(row) < 7:
            row.append("")

        # Skip if already MOVED
        move_status = str(row[COL_MOVED]).strip().upper()
        if move_status == "MOVED":
            continue

        # Parse data
        try:
            name = row[COL_NAME].strip()
            website = row[COL_WEBSITE].strip()
            linkedin = row[COL_LINKEDIN].strip()
            team_raw = row[COL_TEAM_SIZE]
            people_link = row[COL_PEOPLE_LINK].strip()
            kw_status = str(row[COL_KW_STATUS]).strip().lower()
        except:
            continue

        # Skip if no name/website
        if not name and not website:
            continue
            
        # Skip if keyword status not set yet
        if kw_status not in ("cat 1", "cat 2"):
            continue

        try:
            team_size = int(str(team_raw).strip())
        except:
            team_size = 0

        # Classify
        label = classify(kw_status, team_size, linkedin)
        
        # Add to batch
        results[label].append([name, website, linkedin, team_size, people_link])
        rows_to_mark_done.append((actual_row_num, "MOVED"))
        processed_count += 1
        
        print(f"[{actual_row_num}] {website} → {label} (kw={kw_status}, team={team_size})")

    if processed_count == 0:
        print("✅ No rows to classify.")
        return False

    # Write to destination tabs
    for label, rows in results.items():
        if not rows:
            continue
        
        tab_name = DEST_TABS[label]
        config_key = f"next_row_{label}"
        next_row = config.get(config_key, 2)
        
        try:
            ws = dest_book.worksheet(tab_name)
        except:
            ws = dest_book.add_worksheet(title=tab_name, rows=3000, cols=10)
            ws.update("A1:E1", [["name", "website", "LinkedIn", "LinkedIn employees", "LinkedIn people link"]])
            next_row = 2

        end_row = next_row + len(rows) - 1
        
        # Auto-expand worksheet if needed
        if end_row > ws.row_count:
            new_rows = max(end_row + 500, ws.row_count + 1000)  # Add buffer
            ws.add_rows(new_rows - ws.row_count)
            print(f"  📐 Expanded {tab_name} to {new_rows} rows")
        
        ws.update(f"A{next_row}:E{end_row}", rows, value_input_option="USER_ENTERED")
        update_config_value(config_ws, config_key, end_row + 1)
        print(f"  ✅ {label}: Moved {len(rows)} leads.")

    # Mark source rows as MOVED
    if rows_to_mark_done:
        batch_updates = []
        for r_num, val in rows_to_mark_done:
            batch_updates.append({
                'range': f'G{r_num}',
                'values': [[val]]
            })
        source_ws.batch_update(batch_updates)
        print(f"🔒 Marked {len(rows_to_mark_done)} rows as MOVED.")

    return processed_count > 0


# === CLI ===
if __name__ == "__main__":
    import argparse
    
    p = argparse.ArgumentParser(description="Score and Classify")
    p.add_argument("--drain", action="store_true",
                   help="Process all pending rows until done")
    args = p.parse_args()
    
    if args.drain:
        while True:
            try:
                progress = main()
            except Exception as e:
                print(f"Run error: {e}")
                progress = False
            if not progress:
                print("✅ Drain complete – no remaining rows to classify.")
                break
            print(f"🔁 Processed batch of {BATCH_SIZE}; continuing...")
            time.sleep(1)
    else:
        main()