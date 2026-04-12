"""Score and Classify - Classifies leads and moves them to destination tabs.

Reads from Ultra_validated tab, classifies leads based on keyword status
and team size, moves them to appropriate destination tabs (Alpha/Beta/Gamma/etc),
and marks source rows as "MOVED".

Usage:
    python score_and_classify.py              # Process one batch (5 rows)
    python score_and_classify.py --drain      # Process all pending rows
"""

import os
import json
import time
import sys
from pathlib import Path
import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from Bots.common.sheets_guard import authorize_gspread_client, guarded_gspread_call

# === CONFIG ===
_SYSTEM_CONFIG_PATH = Path(__file__).resolve().parents[1] / "common" / "system_config.json"


def _read_system_config() -> dict:
    try:
        data = json.loads(_SYSTEM_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_scoring_config() -> dict:
    config = _read_system_config()
    enrichment = config.get("enrichment") if isinstance(config.get("enrichment"), dict) else {}
    keyword_scoring = enrichment.get("keyword_scoring") if isinstance(enrichment.get("keyword_scoring"), dict) else {}
    scoring = keyword_scoring.get("scoring") if isinstance(keyword_scoring.get("scoring"), dict) else {}
    return scoring


def _get_capacity_stop_threshold() -> int:
    config = _read_system_config()
    watchers = config.get("watchers") if isinstance(config.get("watchers"), dict) else {}
    delivery = watchers.get("delivery_sheet_capacity") if isinstance(watchers.get("delivery_sheet_capacity"), dict) else {}
    try:
        return max(1, int(delivery.get("stop_row_threshold") or 45000))
    except (TypeError, ValueError):
        return 45000


def _get_system_sheet_targets() -> dict:
    config = _read_system_config()
    pipeline = config.get("pipeline_sheets") if isinstance(config.get("pipeline_sheets"), dict) else {}
    source_settlement_cfg = config.get("source_settlement") if isinstance(config.get("source_settlement"), dict) else {}
    sheets = pipeline.get("sheets") if isinstance(pipeline.get("sheets"), list) else []
    active_key = pipeline.get("active_sheet_key")
    active = next((item for item in sheets if item.get("key") == active_key), sheets[0] if sheets else None)
    source_sheets = source_settlement_cfg.get("sheets") if isinstance(source_settlement_cfg.get("sheets"), list) else []
    source_active_key = source_settlement_cfg.get("active_sheet_key")
    active_source = next((item for item in source_sheets if item.get("key") == source_active_key), source_sheets[0] if source_sheets else None)
    return {
        "pipeline": active or {},
        "source_settlement": active_source or {},
    }


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

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

SOURCE_HEADER_ALIASES = {
    "name": ("name",),
    "website": ("website",),
    "linkedin": ("linkedin",),
    "linkedin employees": ("linkedin employees",),
    "linkedin people link": ("linkedin people link",),
    "keyword status": ("keyword status",),
    "moved": ("moved",),
}


def auth_client():
    """Authenticate with Google Sheets."""
    return authorize_gspread_client(creds_path=CREDS_PATH, scopes=SCOPES)


def resolve_sheet_targets():
    targets = _get_system_sheet_targets()
    pipeline = targets.get("pipeline") or {}
    source_settlement = targets.get("source_settlement") or {}

    source_sheet_id = os.environ.get("SHEET_ID") or pipeline.get("sheet_id") or SOURCE_SHEET_ID
    source_tab_name = os.environ.get("TAB_NAME") or pipeline.get("ultra_tab") or SOURCE_TAB_NAME
    dest_sheet_id = os.environ.get("DEST_SHEET_ID") or source_settlement.get("sheet_id") or DEST_SHEET_ID
    config_tab_name = source_settlement.get("config_tab") or CONFIG_TAB_NAME
    dest_tabs = {
        "Alpha": source_settlement.get("alpha_tab") or "Alpha",
        "Alpha_high": source_settlement.get("alpha_high_tab") or "Alpha_high",
        "Beta": source_settlement.get("beta_tab") or "Beta",
        "Beta_high": source_settlement.get("beta_high_tab") or "Beta_high",
        "Gamma": source_settlement.get("gamma_tab") or "Gamma",
        "Gamma_high": source_settlement.get("gamma_high_tab") or "Gamma_high",
        "I-gamma": source_settlement.get("i_gamma_tab") or "I-gamma",
        "pBin": source_settlement.get("pbin_tab") or "pBin",
        "Hydro": source_settlement.get("hydro_tab") or "Hydro",
    }
    return source_sheet_id, source_tab_name, dest_sheet_id, config_tab_name, dest_tabs


def classify(kw_status: str, team_size: int, linkedin_url: str) -> str:
    """Classify a lead based on keyword status and team size."""
    # Hydro Check (Broken/Formula links)
    if not linkedin_url or linkedin_url.startswith("="):
        return "Hydro"
    
    # Category Logic
    if kw_status == "cat 2":
        return "pBin"

    if kw_status in {"cat 1", "cat 3"}:
        high_confidence = kw_status == "cat 3"
        if team_size < 15:
            return "Alpha_high" if high_confidence else "Alpha"
        elif team_size < 65:
            return "Beta_high" if high_confidence else "Beta"
        elif team_size < 250:
            return "Gamma_high" if high_confidence else "Gamma"
        else:
            return "I-gamma"
            
    # Default fallback
    return "pBin"


def _get_batch_size() -> int:
    scoring_config = _get_scoring_config()
    return max(1, _env_int("SCORING_BATCH_SIZE", scoring_config.get("batch_size"), 50))


def _normalize_header(value: str) -> str:
    return str(value or "").strip().lower()


def _cell_value(row: list[str], index: int | None) -> str:
    if index is None or index < 0 or index >= len(row):
        return ""
    return str(row[index]).strip()


def _find_header_index(headers: list[str], *names: str) -> int | None:
    lookup = {_normalize_header(header): idx for idx, header in enumerate(headers)}
    for name in names:
        idx = lookup.get(_normalize_header(name))
        if idx is not None:
            return idx
    return None


def _ensure_source_columns(source_ws) -> dict[str, int]:
    headers = guarded_gspread_call(
        lambda: source_ws.row_values(1),
        operation="read",
        description="Read score/classify source header row",
    )
    if not headers:
        raise RuntimeError("Ultra_validated is missing a header row.")

    indexes: dict[str, int] = {}
    for field, aliases in SOURCE_HEADER_ALIASES.items():
        idx = _find_header_index(headers, *aliases)
        if idx is None and field == "moved":
            guarded_gspread_call(
                lambda: source_ws.add_cols(1),
                operation="write",
                description="Add source moved column",
            )
            idx = len(headers)
            guarded_gspread_call(
                lambda: source_ws.update_cell(1, idx + 1, "Moved"),
                operation="write",
                description="Write source moved header",
            )
            headers.append("Moved")
        if idx is None:
            raise RuntimeError(
                f"Required source column missing in {source_ws.title}: {aliases[0]}"
            )
        indexes[field] = idx
    return indexes


def get_or_create_config_sheet(dest_book, config_tab_name, dest_tabs):
    """Get or create the config worksheet."""
    try:
        ws = guarded_gspread_call(
            lambda: dest_book.worksheet(config_tab_name),
            operation="read",
            description="Open settlement config worksheet",
        )
    except gspread.exceptions.WorksheetNotFound:
        ws = guarded_gspread_call(
            lambda: dest_book.add_worksheet(title=config_tab_name, rows=20, cols=5),
            operation="write",
            description="Create settlement config worksheet",
        )
        guarded_gspread_call(
            lambda: ws.update("A1:B1", [["key", "value"]]),
            operation="write",
            description="Initialize settlement config header",
        )
        init_rows = [[f"next_row_{tab}", 2] for tab in dest_tabs.keys()]
        guarded_gspread_call(
            lambda: ws.update(f"A2:B{1 + len(init_rows)}", init_rows),
            operation="write",
            description="Initialize settlement config rows",
        )
    return ws


def read_config(config_ws):
    """Read config key-value pairs from config worksheet."""
    data = guarded_gspread_call(
        lambda: config_ws.get_all_values(),
        operation="read",
        description="Read settlement config worksheet values",
    )
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
    cell = guarded_gspread_call(
        lambda: config_ws.find(key),
        operation="read",
        description=f"Find settlement config key {key}",
    )
    if cell:
        guarded_gspread_call(
            lambda: config_ws.update_cell(cell.row, 2, value),
            operation="write",
            description=f"Update settlement config key {key}",
        )
    else:
        guarded_gspread_call(
            lambda: config_ws.append_row([key, value]),
            operation="write",
            description=f"Append settlement config key {key}",
        )


def main() -> bool:
    """Process one batch of rows. Returns True if work was done."""
    client = auth_client()
    source_sheet_id, source_tab_name, dest_sheet_id, config_tab_name, dest_tabs = resolve_sheet_targets()
    batch_size = _get_batch_size()

    source_book = guarded_gspread_call(
        lambda: client.open_by_key(source_sheet_id),
        operation="read",
        description="Open score/classify source spreadsheet",
    )
    source_ws = guarded_gspread_call(
        lambda: source_book.worksheet(source_tab_name),
        operation="read",
        description="Open score/classify source worksheet",
    )
    dest_book = guarded_gspread_call(
        lambda: client.open_by_key(dest_sheet_id),
        operation="read",
        description="Open settlement destination spreadsheet",
    )
    config_ws = get_or_create_config_sheet(dest_book, config_tab_name, dest_tabs)
    config = read_config(config_ws)
    source_columns = _ensure_source_columns(source_ws)

    # Read all data
    all_rows = guarded_gspread_call(
        lambda: source_ws.get_all_values(),
        operation="read",
        description="Read score/classify source worksheet values",
    )
    if len(all_rows) < 2:
        print("No data in source.")
        return False

    data_rows = all_rows[1:]  # Skip header
    
    # Find rows to process (have keyword status, not yet MOVED)
    results = {k: [] for k in dest_tabs.keys()}
    rows_to_mark_done = []
    processed_count = 0

    for i, row in enumerate(data_rows):
        if processed_count >= batch_size:
            break
            
        actual_row_num = i + 2  # +2 because of header and 0-index
        
        # Skip if already MOVED
        move_status = _cell_value(row, source_columns["moved"]).upper()
        if move_status == "MOVED":
            continue

        # Parse data
        name = _cell_value(row, source_columns["name"])
        website = _cell_value(row, source_columns["website"])
        linkedin = _cell_value(row, source_columns["linkedin"])
        team_raw = _cell_value(row, source_columns["linkedin employees"])
        people_link = _cell_value(row, source_columns["linkedin people link"])
        kw_status = _cell_value(row, source_columns["keyword status"]).lower()

        # Skip if no name/website
        if not name and not website:
            continue
            
        # Skip if keyword status not set yet
        if kw_status not in ("cat 1", "cat 2", "cat 3"):
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
    source_stop_threshold = _get_capacity_stop_threshold()
    for label, rows in results.items():
        if not rows:
            continue
        
        tab_name = dest_tabs[label]
        config_key = f"next_row_{label}"
        next_row = config.get(config_key, 2)
        
        try:
            ws = guarded_gspread_call(
                lambda tab=tab_name: dest_book.worksheet(tab),
                operation="read",
                description=f"Open destination worksheet {tab_name}",
            )
        except:
            ws = guarded_gspread_call(
                lambda tab=tab_name: dest_book.add_worksheet(title=tab, rows=3000, cols=10),
                operation="write",
                description=f"Create destination worksheet {tab_name}",
            )
            guarded_gspread_call(
                lambda: ws.update("A1:E1", [["name", "website", "LinkedIn", "LinkedIn employees", "LinkedIn people link"]]),
                operation="write",
                description=f"Initialize destination worksheet {tab_name}",
            )
            next_row = 2

        current_rows = len(
            guarded_gspread_call(
                lambda: ws.col_values(1),
                operation="read",
                description=f"Read destination worksheet {tab_name} column A",
            )
        )
        if current_rows >= source_stop_threshold:
            raise RuntimeError(
                f"Source settlement tab {tab_name} reached stop threshold "
                f"({current_rows}/{source_stop_threshold}). Await sheet rotation before continuing."
            )

        end_row = next_row + len(rows) - 1
        
        # Auto-expand worksheet if needed
        if end_row > ws.row_count:
            new_rows = max(end_row + 500, ws.row_count + 1000)  # Add buffer
            guarded_gspread_call(
                lambda delta=(new_rows - ws.row_count): ws.add_rows(delta),
                operation="write",
                description=f"Expand destination worksheet {tab_name}",
            )
            print(f"  📐 Expanded {tab_name} to {new_rows} rows")
        
        guarded_gspread_call(
            lambda: ws.update(f"A{next_row}:E{end_row}", rows, value_input_option="USER_ENTERED"),
            operation="write",
            description=f"Write destination worksheet {tab_name} rows",
        )
        update_config_value(config_ws, config_key, end_row + 1)
        print(f"  ✅ {label}: Moved {len(rows)} leads.")

    # Mark source rows as MOVED
    if rows_to_mark_done:
        batch_updates = []
        for r_num, val in rows_to_mark_done:
            batch_updates.append({
                'range': rowcol_to_a1(r_num, source_columns["moved"] + 1),
                'values': [[val]]
            })
        guarded_gspread_call(
            lambda: source_ws.batch_update(batch_updates),
            operation="write",
            description="Mark source rows as moved",
        )
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
            print(f"🔁 Processed batch of {_get_batch_size()}; continuing...")
            time.sleep(1)
    else:
        main()
