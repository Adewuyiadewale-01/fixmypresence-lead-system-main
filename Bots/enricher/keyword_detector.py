"""Keyword Detector - Checks websites for finance keywords.

Reads from Ultra_validated tab, checks each website for keywords,
and sets Keyword status to "Cat 1" (found) or "Cat 2" (not found).

Usage:
    python keyword_detector.py              # Process one batch (5 rows)
    python keyword_detector.py --drain      # Process all pending rows
    python keyword_detector.py --reset-pointer  # Reset to row 2
"""

import os
import time
from typing import List, Tuple, Optional

import gspread
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
}


# Keywords to look for
# English finance keywords
KEYWORDS_EN = [
    "capital",
    "fund",
    "funds",
    "asset",
    "assets",
    "wealth",
    "investment",
    "investments",
    "invest in",
    "investment strategy",
    "partners",
    "equity",
    "interest rate",
    "interest rates",
    "mortgage",
    "mortgages",
    "financial expert",
    "financial advisor",
    "strategy",
]

# Dutch equivalents / common Dutch finance terms
KEYWORDS_NL = [
    "kapitaal",
    "fonds",
    "fondsen",
    "vermogen",
    "vermogensbeheer",
    "vermogensbeheerder",
    "belegging",
    "beleggingen",
    "beleggingsfonds",
    "beleggingsfondsen",
    "investeer in",
    "investeren in",
    "beleggingsstrategie",
    "partners",
    "equity",
    "hypotheek",
    "hypotheken",
    "rente",
    "rentetarief",
    "rentepercentage",
    "financieel expert",
    "financiële expert",
    "financieel adviseur",
    "financieel planner",
    "strategie",
    "financiële planning",
    "estate planning",
]


# Final keyword list (lowercased, deduplicated)
KEYWORDS = list(dict.fromkeys(
    [kw.lower() for kw in (KEYWORDS_EN + KEYWORDS_NL)]
))

# Sheet config (override via env vars)
CREDS_PATH = os.path.expanduser(
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json")
)
SHEET_ID = os.environ.get(
    "SHEET_ID", "1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc"
)
TAB_NAME = os.environ.get("TAB_NAME", "Ultra_validated")
META_TAB_NAME = "_meta"
WEBSITE_COLUMN = "website"
STATUS_COLUMN = "Keyword status"
BATCH_SIZE = 25

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ============================================================================
# _meta POINTER FUNCTIONS
# ============================================================================

def get_or_create_meta(sh):
    """Get or create the _meta worksheet for storing pointer."""
    try:
        return sh.worksheet(META_TAB_NAME)
    except gspread.WorksheetNotFound:
        meta = sh.add_worksheet(title=META_TAB_NAME, rows=10, cols=2)
        meta.update("A1:B2", [["key", "value"], ["keyword_next_row", "2"]])
        return meta


def meta_get_next_row(meta) -> int:
    """Get the keyword_next_row value from _meta tab."""
    try:
        cell = meta.find("keyword_next_row")
        if cell:
            val = meta.cell(cell.row, 2).value
            return int(val) if val else 2
    except:
        pass
    return 2


def meta_set_next_row(meta, row: int):
    """Set the keyword_next_row value in _meta tab."""
    try:
        cell = meta.find("keyword_next_row")
        if cell:
            meta.update_cell(cell.row, 2, row)
        else:
            # Add the row
            meta.append_row(["keyword_next_row", row])
    except Exception as e:
        print(f"Warning: Could not update _meta: {e}")


# ============================================================================
# CORE FUNCTIONS
# ============================================================================

def auth_client():
    """Authenticate with Google Sheets."""
    creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)


def add_dropdown_validation(ws, col_idx):
    """Apply Cat 1/Cat 2 dropdown validation to the status column."""
    try:
        ws.spreadsheet.batch_update(
            {
                "requests": [
                    {
                        "setDataValidation": {
                            "range": {
                                "sheetId": ws.id,
                                "startRowIndex": 1,
                                "startColumnIndex": col_idx - 1,
                                "endRowIndex": ws.row_count,
                                "endColumnIndex": col_idx,
                            },
                            "rule": {
                                "condition": {
                                    "type": "ONE_OF_LIST",
                                    "values": [
                                        {"userEnteredValue": "Cat 1"},
                                        {"userEnteredValue": "Cat 2"},
                                    ],
                                },
                                "showCustomUi": True,
                                "strict": True,
                            },
                        }
                    }
                ]
            }
        )
    except Exception:
        pass


def keyword_check(url: str) -> dict:
    """Check a URL for finance keywords."""
    try:
        resp = requests.get(
            url,
            timeout=15,
            headers=HEADERS,
            allow_redirects=True,
        )
        status = resp.status_code

        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        lower = text.lower()
        words = lower.split()
        word_count = len(words)

        found = [kw for kw in KEYWORDS if kw in lower]

        blocked_markers = [
            "access denied",
            "forbidden",
            "are you a robot",
            "unusual traffic",
            "captcha",
            "cloudflare",
            "checking your browser",
            "enable javascript",
        ]
        blocked = any(m in lower for m in blocked_markers)

        return {
            "found": found,
            "word_count": word_count,
            "js_rendered": word_count < 100,
            "http_status": status,
            "blocked": blocked,
        }

    except Exception as exc:
        return {
            "found": [],
            "word_count": 0,
            "js_rendered": False,
            "http_status": None,
            "blocked": True,
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> bool:
    """Process one batch of rows. Returns True if work was done."""
    gc = auth_client()
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(TAB_NAME)
    meta = get_or_create_meta(sh)
    
    # Get headers and column indices
    headers = [h.strip() for h in ws.row_values(1)]
    header_map = {h.lower(): idx + 1 for idx, h in enumerate(headers)}
    
    website_col = header_map.get(WEBSITE_COLUMN.lower())
    if not website_col:
        print(f"❌ Website column '{WEBSITE_COLUMN}' not found in sheet.")
        return False
    
    status_col = header_map.get(STATUS_COLUMN.lower())
    if not status_col:
        ws.add_cols(1)
        status_col = ws.col_count
        ws.update_cell(1, status_col, STATUS_COLUMN)
        add_dropdown_validation(ws, status_col)
    
    # Get all values and pointer
    values = ws.get_all_values()
    next_row = meta_get_next_row(meta)
    
    if next_row > len(values):
        print("✅ All rows processed. Nothing to do.")
        return False
    
    # Find rows to process (have website, no status yet)
    work_rows = []
    for r in range(next_row, len(values) + 1):
        if len(work_rows) >= BATCH_SIZE:
            break
        
        row = values[r - 1]
        website = row[website_col - 1] if website_col - 1 < len(row) else ""
        status = row[status_col - 1] if status_col - 1 < len(row) else ""
        
        if website.strip() and not status.strip():
            work_rows.append((r, website.strip()))
    
    if not work_rows:
        # Advance pointer to end
        meta_set_next_row(meta, len(values) + 1)
        print("✅ No rows need keyword checking.")
        return False
    
    print(f"📋 Processing {len(work_rows)} rows (starting from row {next_row})...")
    
    # Process rows
    updates = []
    last_row = next_row
    
    for row_idx, url in work_rows:
        print(f"[{row_idx}] {url}")
        result = keyword_check(url)
        
        if "error" in result:
            print(f"  ❌ Error: {result['error']}")
            status = ""
        else:
            print(f"  → Found: {result['found'][:3]}..." if len(result['found']) > 3 else f"  → Found: {result['found']}")
            status = "Cat 1" if result["found"] else "Cat 2"
            print(f"  → Status: {status}")
        
        a1 = rowcol_to_a1(row_idx, status_col)
        updates.append({"range": f"{ws.title}!{a1}", "values": [[status]]})
        last_row = row_idx
        time.sleep(0.25)
    
    # Batch update
    if updates:
        ws.spreadsheet.values_batch_update(
            {"valueInputOption": "USER_ENTERED", "data": updates}
        )
        print(f"✅ Updated {len(updates)} rows.")
    
    # Advance pointer
    meta_set_next_row(meta, last_row + 1)
    
    return len(work_rows) > 0


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    p = argparse.ArgumentParser(description="Keyword Detector")
    p.add_argument("--drain", action="store_true", 
                   help="Process all pending rows until done")
    p.add_argument("--reset-pointer", action="store_true",
                   help="Reset keyword_next_row to 2")
    args = p.parse_args()
    
    if args.reset_pointer:
        gc = auth_client()
        sh = gc.open_by_key(SHEET_ID)
        meta = get_or_create_meta(sh)
        meta_set_next_row(meta, 2)
        print("🔁 Pointer reset to row 2.")
        if not args.drain:
            exit(0)
    
    if args.drain:
        while True:
            try:
                progress = main()
            except Exception as e:
                print(f"Run error: {e}")
                progress = False
            if not progress:
                print("✅ Drain complete – no remaining rows to check.")
                break
            print(f"🔁 Processed batch of {BATCH_SIZE}; continuing...")
            time.sleep(1)
    else:
        main()
