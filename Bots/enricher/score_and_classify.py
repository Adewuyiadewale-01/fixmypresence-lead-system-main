import os
import gspread
from google.oauth2.service_account import Credentials

# === CONFIG ===
CREDS_PATH = os.path.expanduser("~/sheet-bot-key.json")

SOURCE_SHEET_ID = "1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc"
SOURCE_TAB_NAME = "Ultra_validated"

DEST_SHEET_ID = "1YF8WvLTPu-Raf22rHaaphauj-w-n64CRu83ZeAtHYAc"
CONFIG_TAB_NAME = "config"  # Tab to store next_row values

# Updated with all your new categories
DEST_TABS = {
    "Alpha": "Alpha",
    "Beta": "Beta",
    "Gamma": "Gamma",
    "I-gamma": "I-gamma",
    "pBin": "pBin",
    "Hydro": "Hydro"
}

def classify(kw_status: str, team_size: int, linkedin_url: str) -> str:
    """
    Applies the new classification logic based on keyword status and team size.
    """
    # 1. Check for Hydro (broken links) first
    if linkedin_url.startswith("="):
        return "Hydro"
    
    # 2. Check for pBin (all cat 2)
    if kw_status == "cat 2":
        return "pBin"

    # 3. Apply the waterfall logic for all "cat 1"
    if kw_status == "cat 1":
        if team_size < 15:
            return "Alpha"
        elif team_size < 65:
            return "Beta"
        elif team_size < 250:
            return "Gamma"
        else: # team_size >= 250
            return "I-gamma"
            
    # 4. If not Hydro, cat 1, or cat 2 (e.g., blank, "ERROR"),
    #    put it in the pBin for review.
    return "pBin"


def get_or_create_config_sheet(dest_book):
    """Get or create the config sheet with default next_row values."""
    try:
        ws = dest_book.worksheet(CONFIG_TAB_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = dest_book.add_worksheet(title=CONFIG_TAB_NAME, rows=20, cols=5)
        # Create header and default values for each tab
        header = ["key", "value"]
        ws.update("A1:B1", [header])
        
        # Initialize next_row for each destination tab
        init_rows = [[f"next_row_{tab}", 2] for tab in DEST_TABS.keys()]
        ws.update(f"A2:B{1 + len(init_rows)}", init_rows)
        print(f"🆕 Created '{CONFIG_TAB_NAME}' tab with default values.")
    
    return ws


def read_config(config_ws):
    """Read all key-value pairs from config sheet."""
    data = config_ws.get_all_values()
    if len(data) < 2:
        return {}
    
    config = {}
    for row in data[1:]:  # Skip header
        if len(row) >= 2 and row[0]:
            try:
                config[row[0]] = int(row[1])
            except ValueError:
                config[row[0]] = row[1]
    return config


def update_config_value(config_ws, key, value):
    """Update a single key's value in the config sheet."""
    data = config_ws.get_all_values()
    for i, row in enumerate(data):
        if len(row) >= 1 and row[0] == key:
            config_ws.update_cell(i + 1, 2, value)
            return
    # Key not found, append it
    config_ws.append_row([key, value])


# === AUTH ===
scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_file(CREDS_PATH, scopes=scopes)
client = gspread.authorize(creds)

source_ws = client.open_by_key(SOURCE_SHEET_ID).worksheet(SOURCE_TAB_NAME)

# Get all data as a list of lists, skipping the header row (row 1)
source_data = source_ws.get_all_values()[1:] 
dest_book = client.open_by_key(DEST_SHEET_ID)

# Get or create config sheet and read values
config_ws = get_or_create_config_sheet(dest_book)
config = read_config(config_ws)

# Updated results dictionary
results = {
    "Alpha": [],
    "Beta": [],
    "Gamma": [],
    "I-gamma": [],
    "pBin": [],
    "Hydro": []
}

# Your column indexes from the screenshot:
# A = 0, B = 1, C = 2, D = 3, E = 4
# *** ASSUMING Keyword Status is in F = 5 ***
for row in source_data:
    try:
        name = str(row[0]).strip()           # Column A
        website = str(row[1]).strip()       # Column B
        linkedin = str(row[2]).strip()      # Column C
        team_raw = row[3]                   # Column D
        people_link = str(row[4]).strip()   # Column E
        kw_status = str(row[5]).strip().lower() # !! ASSUMING COLUMN F (index 5) !!
    except IndexError:
        # Skips rows that are too short or empty
        continue

    # Try to convert team size, default to 0 if blank
    try:
        team_size = int(str(team_raw).strip())
    except:
        team_size = 0

    # We only skip rows if they are truly empty
    if not all([name, website, linkedin, people_link]):
        continue
    
    # --- NO SCRAPING, NO SCORING ---
    # Just classify based on the pre-filled data
    label = classify(kw_status, team_size, linkedin)
    
    # Add the row to the correct list for batch updating
    results[label].append([name, website, linkedin, team_size, people_link])


# === WRITE TO DESTINATION TABS USING CONFIG-BASED ROW POSITIONS ===
for label, rows in results.items():
    if not rows:
        continue
    
    tab_name = DEST_TABS[label]
    config_key = f"next_row_{label}"
    
    # Get next_row from config, default to 2 (after header)
    next_row = config.get(config_key, 2)
    
    try:
        ws = dest_book.worksheet(tab_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = dest_book.add_worksheet(title=tab_name, rows=3000, cols=10)
        ws.update("A1:E1", [["name", "website", "LinkedIn", "LinkedIn employees", "LinkedIn people link"]])
        next_row = 2  # Start after header for new sheet
    
    # Write data starting at next_row using update() instead of append_rows()
    num_rows = len(rows)
    num_cols = 5  # name, website, linkedin, team_size, people_link
    end_row = next_row + num_rows - 1
    
    # Build the range string (e.g., "A2:E5")
    range_str = f"A{next_row}:E{end_row}"
    ws.update(range_str, rows, value_input_option="USER_ENTERED")
    
    # Update the config with the new next_row value
    new_next_row = end_row + 1
    update_config_value(config_ws, config_key, new_next_row)
    
    print(f"📝 {label}: Wrote {num_rows} rows at rows {next_row}-{end_row}. Next run starts at row {new_next_row}.")


# === FINAL REPORT ===
print("======================================================")
print("  SCORING & CLASSIFICATION COMPLETE")
print("======================================================")
# Print all new categories
for k in DEST_TABS.keys():
    print(f" {k:<8}: {len(results[k])}")
print("======================================================")
print("💡 To reset: Set 'next_row_<TabName>' values to 2 in the 'config' tab.")