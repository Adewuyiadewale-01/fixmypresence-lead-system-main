"""Configuration for the Google Maps lead scraper."""

from pathlib import Path

# Narrow geographic targets – add more items as your addressing partner
# delivers additional micro-locations.
LOCATIONS = [
    "De Pijp, Amsterdam, Noord-Holland, Netherlands",
]

# Keywords organized by category - scraper processes one category at a time.
# Use --next-category to advance to the next category.

KEYWORD_CATEGORY_1 = [
    "family office",
    "wealth management",
    "private banking",
]

KEYWORD_CATEGORY_2 = [
    "private equity",
    "venture capital",
    "investment fund",
]

KEYWORD_CATEGORY_3 = [
    "law firm",
    "corporate lawyer",
    "business attorney",
]

KEYWORD_CATEGORY_4 = [
    "accountant",
    "tax advisor",
    "bookkeeper",
]

KEYWORD_CATEGORY_5 = [
    "management consultant",
    "business consultant",
    "strategy consultant",
]

# All categories in processing order
ALL_KEYWORD_CATEGORIES = [
    KEYWORD_CATEGORY_1,
    KEYWORD_CATEGORY_2,
    KEYWORD_CATEGORY_3,
    KEYWORD_CATEGORY_4,
    KEYWORD_CATEGORY_5,
]

TOTAL_CATEGORIES = len(ALL_KEYWORD_CATEGORIES)

# Number of unique leads (post-deduplication) to collect before pausing to
# request operator confirmation to continue.
BATCH_SIZE = 50

# Where batch CSVs should be written.
OUTPUT_DIR = Path("output")

# Consolidated CSV containing every lead gathered during a session. Resets at
# the start of each run.
MASTER_OUTPUT = OUTPUT_DIR / "leads_full.csv"

# Progress tracking file to enable resuming after crashes/stops.
PROGRESS_FILE = OUTPUT_DIR / "scraper_progress.json"

# Webhook integration (Google Apps Script)
WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbzqD2ny083z43T6vnD2nifpYMI7RdbRXhOvrsFuhKHrJdlvF0Ealy7QwsDxJpVeSaYo/exec"
WEBHOOK_SECRET = "FMP-Scraper"
WEBHOOK_SPREADSHEET_ID = "1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc"
WEBHOOK_TAB_NAME = "Leads"

# Persistent Playwright profile directory so Google consent cookies survive
# across runs. Stored alongside the repo for transparency.
PLAYWRIGHT_USER_DATA = Path(".playwright-profile")

# Domains to drop because they belong to aggregators, directories, or social
# networks rather than to the business itself.
from common.config import AGGREGATOR_DOMAINS

# How long (seconds) we idle between Map item interactions. The throttling
# logic randomises between +/- this many seconds to appear human.
MIN_DELAY_S = 0.8
MAX_DELAY_S = 1.6

# When Google presents a captcha we back off for this many seconds before
# surfacing the issue. Keeping it generous prevents lockouts.
CAPTCHA_BACKOFF_S = 45
