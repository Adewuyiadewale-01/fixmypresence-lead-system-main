# enrich_linkedin.py
import os, re, sys, time, random, json, urllib.parse, html, datetime, contextlib
import threading
from queue import Queue, Empty
from dataclasses import dataclass
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2 import service_account

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from Bots.common.sheets_guard import authorize_gspread_client, guarded_gspread_call
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except Exception:
    sync_playwright = None
    PlaywrightTimeoutError = Exception

# ------------------ config ------------------
_SYSTEM_CONFIG_PATH = Path(__file__).resolve().parents[1] / "common" / "system_config.json"
_ENRICHMENT_RUN_STATE_FILE = _PROJECT_ROOT / "controller" / "enrichment_run_state.json"
_ENRICHMENT_LOCK_FILE = _PROJECT_ROOT / "controller" / "enrichment_lock.json"


def _release_enrichment_lock_if_owned():
    """
    Clear controller/enrichment_lock.json when this process owns it.

    The enrichment watcher writes the lock and updates it to hold this drain
    subprocess's PID. If we exit without clearing it, the dashboard flags the
    lock as stale until the watcher's next tick reaps it. This helper makes
    the drain responsible for its own cleanup on every exit path.
    """
    try:
        if not _ENRICHMENT_LOCK_FILE.exists():
            return
        data = json.loads(_ENRICHMENT_LOCK_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("pid") == os.getpid():
            with contextlib.suppress(OSError):
                _ENRICHMENT_LOCK_FILE.unlink()
    except Exception:
        pass


def _read_system_config():
    try:
        data = json.loads(_SYSTEM_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_run_state(payload: dict):
    try:
        _ENRICHMENT_RUN_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _ENRICHMENT_RUN_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        tmp.replace(_ENRICHMENT_RUN_STATE_FILE)
    except Exception:
        pass


class EnrichmentRunTracker:
    def __init__(self, mode: str, initial_pending: int, batch_size: int):
        self._lock = threading.Lock()
        self.mode = mode
        self.initial_pending = max(0, int(initial_pending or 0))
        self.batch_size = max(1, int(batch_size or 1))
        self.processed_rows_completed = 0
        self.batches_completed = 0
        self.current_batch_total = 0
        self.current_batch_processed = 0
        self.last_known_pending = self.initial_pending
        self.started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.last_flush_at = None
        self.state = {
            "active": True,
            "pid": os.getpid(),
            "mode": self.mode,
            "started_at": self.started_at,
            "ended_at": None,
            "batch_size": self.batch_size,
            "initial_pending": self.initial_pending,
            "processed_rows_completed": 0,
            "remaining_estimate": self.initial_pending,
            "current_pending": self.initial_pending,
            "batches_completed": 0,
            "current_batch_total": 0,
            "current_batch_processed": 0,
            "current_batch_started_at": None,
            "last_flush_at": None,
            "last_row_num": None,
            "next_pointer": None,
            "last_error": None,
            "target": {
                "sheet_id": SHEET_ID if "SHEET_ID" in globals() else "",
                "tab_name": TAB_NAME if "TAB_NAME" in globals() else "",
            },
        }
        self._commit()

    def _commit(self):
        _write_run_state(dict(self.state))

    def start_batch(self, batch_total: int, start_row: int, pending_before_batch: int):
        with self._lock:
            self.current_batch_total = max(0, int(batch_total or 0))
            self.current_batch_processed = 0
            self.last_known_pending = max(0, int(pending_before_batch or 0))
            self.state.update({
                "active": True,
                "pid": os.getpid(),
                "current_pending": self.last_known_pending,
                "remaining_estimate": self.last_known_pending,
                "current_batch_total": self.current_batch_total,
                "current_batch_processed": 0,
                "current_batch_started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "last_row_num": start_row,
                "last_error": None,
            })
            self._commit()

    def update_batch_progress(self, batch_processed: int, next_pointer: int | None = None, last_row_num: int | None = None):
        with self._lock:
            self.current_batch_processed = max(0, min(int(batch_processed or 0), self.current_batch_total))
            overall_processed = self.processed_rows_completed + self.current_batch_processed
            remaining_estimate = max(self.initial_pending - overall_processed, 0)
            self.last_flush_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.state.update({
                "active": True,
                "pid": os.getpid(),
                "current_batch_processed": self.current_batch_processed,
                "processed_rows_completed": overall_processed,
                "remaining_estimate": remaining_estimate,
                "last_flush_at": self.last_flush_at,
                "next_pointer": next_pointer,
                "last_row_num": last_row_num,
            })
            self._commit()

    def finish_batch(self, pending_after_batch: int | None = None, next_pointer: int | None = None):
        with self._lock:
            self.processed_rows_completed += self.current_batch_processed
            self.batches_completed += 1
            current_pending = max(0, int(pending_after_batch if pending_after_batch is not None else max(self.initial_pending - self.processed_rows_completed, 0)))
            self.last_known_pending = current_pending
            self.state.update({
                "active": True,
                "pid": os.getpid(),
                "processed_rows_completed": self.processed_rows_completed,
                "batches_completed": self.batches_completed,
                "current_pending": current_pending,
                "remaining_estimate": current_pending,
                "current_batch_total": 0,
                "current_batch_processed": 0,
                "current_batch_started_at": None,
                "next_pointer": next_pointer,
            })
            self.current_batch_total = 0
            self.current_batch_processed = 0
            self._commit()

    def finish_run(self, final_pending: int | None = None, error: str | None = None):
        with self._lock:
            final_remaining = max(0, int(final_pending if final_pending is not None else self.last_known_pending))
            self.state.update({
                "active": False,
                "pid": None,
                "ended_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "current_pending": final_remaining,
                "remaining_estimate": final_remaining,
                "current_batch_total": 0,
                "current_batch_processed": 0,
                "current_batch_started_at": None,
                "last_error": error,
            })
            self._commit()


def _get_linkedin_runtime_config():
    config = _read_system_config()
    enrichment = config.get("enrichment") if isinstance(config.get("enrichment"), dict) else {}
    linkedin = enrichment.get("linkedin") if isinstance(enrichment.get("linkedin"), dict) else {}
    runtime = linkedin.get("runtime") if isinstance(linkedin.get("runtime"), dict) else {}
    return runtime


def _get_active_pipeline_validated_target() -> tuple[str, str]:
    config = _read_system_config()
    pipeline = config.get("pipeline_sheets") if isinstance(config.get("pipeline_sheets"), dict) else {}
    sheets = pipeline.get("sheets") if isinstance(pipeline.get("sheets"), list) else []
    active_key = pipeline.get("active_sheet_key")
    active = next((item for item in sheets if item.get("key") == active_key), sheets[0] if sheets else None)
    if isinstance(active, dict):
        return (
            str(active.get("sheet_id") or "").strip(),
            str(active.get("validated_tab") or "Validated_leads").strip() or "Validated_leads",
        )
    return "", "Validated_leads"


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


def _env_float(name: str, config_value, default: float) -> float:
    raw = os.environ.get(name)
    if raw not in (None, ""):
        try:
            return float(raw)
        except ValueError:
            return default
    try:
        return float(config_value)
    except (TypeError, ValueError):
        return default


_RUNTIME_CONFIG = _get_linkedin_runtime_config()

BATCH_SIZE = max(1, _env_int("ENRICHMENT_BATCH_SIZE", _RUNTIME_CONFIG.get("batch_size"), 25))
WRITE_FLUSH_EVERY = 5
TIMEOUT = max(1, _env_int("ENRICHMENT_TIMEOUT_SECONDS", _RUNTIME_CONFIG.get("request_timeout_seconds"), 12))
WEBSITE_LOOKUP_BUDGET_SECONDS = max(
    5.0,
    _env_float("ENRICHMENT_WEBSITE_LOOKUP_BUDGET_SECONDS", _RUNTIME_CONFIG.get("website_lookup_budget_seconds"), 30.0),
)
LINKEDIN_FETCH_BUDGET_SECONDS = max(
    5.0,
    _env_float("ENRICHMENT_LINKEDIN_FETCH_BUDGET_SECONDS", _RUNTIME_CONFIG.get("linkedin_fetch_budget_seconds"), 20.0),
)
MAX_INTERNAL_PAGES = max(
    1,
    _env_int("ENRICHMENT_MAX_INTERNAL_PAGES", _RUNTIME_CONFIG.get("max_internal_pages"), 4),
)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

CREDS_PATH = os.path.expanduser(
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json")
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Default sheet wiring (env vars can still override)
DEFAULT_SHEET_PROFILE = "anthony_directory"
SHEET_PROFILES = {
    "anthony_directory": {
        "sheet_id": "1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc",
        "tab_name": "Validated_leads",
    },
}

# Column headers we use/create
COL_COMPANY        = "name"
COL_LINKEDIN       = "LinkedIn"  # leave unchanged – these will be auto-added
COL_STATUS         = "LinkedIn status"  # separate from "Website Status"
COL_LAST_CHECKED   = "Last checked"
COL_EMP_COUNT      = "LinkedIn employees"
COL_EMP_LINK       = "LinkedIn people link"
COL_COMPANY_ID     = "LinkedIn company ID"


STATUS_FOUND = "FOUND"
STATUS_NONE  = "NONE"
STATUS_PERSON_PROFILE = "PERSON_PROFILE_ONLY"

# Pipeline concurrency
WEBSITE_WORKERS       = max(1, _env_int("ENRICHMENT_WEBSITE_WORKERS", _RUNTIME_CONFIG.get("website_workers"), 2))
LINKEDIN_WORKERS      = max(1, _env_int("ENRICHMENT_LINKEDIN_WORKERS", _RUNTIME_CONFIG.get("linkedin_workers"), 1))
WRITER_FLUSH_INTERVAL = max(0.1, _env_float("ENRICHMENT_WRITER_FLUSH_INTERVAL_SECONDS", _RUNTIME_CONFIG.get("writer_flush_interval_seconds"), 10.0))   # seconds between forced flushes
WRITER_FLUSH_BATCH    = max(1, _env_int("ENRICHMENT_WRITER_FLUSH_BATCH", _RUNTIME_CONFIG.get("writer_flush_batch"), 5))      # rows before each flush
LI_DELAY_MIN          = max(0.0, _env_float("ENRICHMENT_LINKEDIN_DELAY_MIN_SECONDS", _RUNTIME_CONFIG.get("linkedin_delay_min_seconds"), 2.0))    # min seconds between LinkedIn requests
LI_DELAY_MAX          = max(LI_DELAY_MIN, _env_float("ENRICHMENT_LINKEDIN_DELAY_MAX_SECONDS", _RUNTIME_CONFIG.get("linkedin_delay_max_seconds"), 6.0))    # max seconds (randomized jitter)
MAX_BROWSERS          = max(1, _env_int("ENRICHMENT_MAX_BROWSERS", _RUNTIME_CONFIG.get("max_browsers"), 3))      # hard cap on concurrent Playwright instances
_SENTINEL = object()           # poison pill for queue shutdown (not None)
# --------------------------------------------

with contextlib.suppress(Exception):
    sys.stdout.reconfigure(line_buffering=True)
with contextlib.suppress(Exception):
    sys.stderr.reconfigure(line_buffering=True)

def err_exit(msg: str):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)

def resolve_sheet_target() -> tuple[str, str]:
    env_sheet_id = os.environ.get("SHEET_ID")
    env_tab_name = os.environ.get("TAB_NAME")
    profile_name = os.environ.get("SHEET_PROFILE", DEFAULT_SHEET_PROFILE)
    profile = SHEET_PROFILES.get(profile_name)
    config_sheet_id, config_tab_name = _get_active_pipeline_validated_target()

    if env_sheet_id:
        tab = env_tab_name or (profile["tab_name"] if profile else "Main")
        return env_sheet_id, tab

    if config_sheet_id:
        return config_sheet_id, (env_tab_name or config_tab_name)

    if profile:
        tab = env_tab_name or profile.get("tab_name", "Main")
        return profile["sheet_id"], tab

    err_exit(
        "Set SHEET_ID env var or add the sheet to SHEET_PROFILES "
        f"(unknown sheet profile '{profile_name}')."
    )

SHEET_ID, TAB_NAME = resolve_sheet_target()

if not SHEET_ID:
    err_exit("Set SHEET_ID env var or define a profile in SHEET_PROFILES.")
if not os.path.exists(CREDS_PATH):
    err_exit(f"Service-account key not found at: {CREDS_PATH}")

def auth_client():
    return authorize_gspread_client(
        creds_path=CREDS_PATH,
        scopes=SCOPES,
    )

# ===== meta helpers (progress pointer) =====
META_SHEET = "_meta"
META_KEY_NEXTROW = "next_row"

def get_or_create_meta(sh: gspread.Spreadsheet):
    worksheets = guarded_gspread_call(
        lambda: sh.worksheets(),
        operation="read",
        description="List enrichment worksheets",
    )
    meta = (
        guarded_gspread_call(
            lambda: sh.worksheet(META_SHEET),
            operation="read",
            description="Open enrichment metadata worksheet",
        )
        if META_SHEET in [w.title for w in worksheets]
        else guarded_gspread_call(
            lambda: sh.add_worksheet(META_SHEET, 10, 2),
            operation="write",
            description="Create enrichment metadata worksheet",
        )
    )
    try:
        guarded_gspread_call(
            lambda: sh.batch_update([{"updateSheetProperties": {
                "properties": {"sheetId": meta.id, "hidden": True},
                "fields": "hidden"
            }}]),
            operation="write",
            description="Hide enrichment metadata worksheet",
        )
    except Exception:
        pass
    if guarded_gspread_call(
        lambda: meta.acell("A1").value,
        operation="read",
        description="Read enrichment metadata header",
    ) != "key":
        guarded_gspread_call(
            lambda: meta.update("A1:B1", [["key", "value"]]),
            operation="write",
            description="Initialize enrichment metadata header",
        )
    return meta

def meta_get_next_row(meta) -> int:
    cells = guarded_gspread_call(
        lambda: meta.findall(META_KEY_NEXTROW),
        operation="read",
        description="Find enrichment next-row pointer",
    )
    if cells:
        row = cells[0].row
        val = guarded_gspread_call(
            lambda: meta.cell(row, 2).value,
            operation="read",
            description="Read enrichment next-row pointer",
        )
        try:
            return max(2, int(val))
        except Exception:
            return 2
    guarded_gspread_call(
        lambda: meta.append_row([META_KEY_NEXTROW, "2"]),
        operation="write",
        description="Append enrichment next-row pointer",
    )
    return 2

def meta_set_next_row(meta, next_row: int):
    cells = guarded_gspread_call(
        lambda: meta.findall(META_KEY_NEXTROW),
        operation="read",
        description="Find enrichment next-row pointer for update",
    )
    if cells:
        guarded_gspread_call(
            lambda: meta.update_cell(cells[0].row, 2, str(max(2, next_row))),
            operation="write",
            description="Update enrichment next-row pointer",
        )
    else:
        guarded_gspread_call(
            lambda: meta.append_row([META_KEY_NEXTROW, str(max(2, next_row))]),
            operation="write",
            description="Append enrichment next-row pointer value",
        )


def _row_cell(row: list[str], col_idx: int | None) -> str:
    if col_idx is None or col_idx <= 0 or col_idx - 1 >= len(row):
        return ""
    return str(row[col_idx - 1]).strip()


def _row_needs_link_lookup(row: list[str], col_website: int, col_linkedin: int, col_status: int) -> bool:
    website = _row_cell(row, col_website)
    if not website:
        return False
    linkedin_val = _row_cell(row, col_linkedin)
    status_val = _row_cell(row, col_status)
    has_formula = linkedin_val.startswith("=")
    linkedin_kind = _linkedin_url_kind(linkedin_val) if not has_formula else ""
    has_real_li = linkedin_kind in {"company", "showcase"}
    is_none = status_val == STATUS_NONE
    is_person_profile = status_val == STATUS_PERSON_PROFILE
    return (not has_real_li) and not is_none and not is_person_profile


def _count_pending_rows(values: list[list[str]], col_website: int, col_linkedin: int, col_status: int) -> int:
    if not values or len(values) <= 1:
        return 0
    return sum(
        1
        for row in values[1:]
        if _row_needs_link_lookup(row, col_website, col_linkedin, col_status)
    )


_browser_semaphore = threading.Semaphore(MAX_BROWSERS)


@dataclass
class RowTask:
    seq_num: int           # 0-based index in work_rows (for ordered commit tracking)
    row_num: int           # 1-indexed actual sheet row
    company_name: str
    website: str
    linkedin_val: str
    status_val: str
    emp_count_v: str
    emp_link_v: str
    company_id_v: str
    company_cell_a1: str   # e.g. "A12" — for Google search formula
    has_real_li: bool
    needs_link_lookup: bool
    needs_backfill: bool


@dataclass
class RowResult:
    seq_num: int           # matches RowTask.seq_num — used for ordered buffer
    row_num: int           # actual sheet row — used for _meta.next_row
    cells: list            # list of (col_num, value) tuples; empty list on failure
    meta_label: str = ""   # for logging
    advance_pointer: bool = True


class LinkedInCompanyInspector:
    """Lightweight wrapper around Playwright to extract employee stats."""

    _warned = False

    def __init__(self, browser_semaphore=None):
        self._play = None
        self._browser = None
        self._page = None
        self._semaphore = browser_semaphore
        self._semaphore_acquired = False

    def _ensure(self, fresh: bool = False) -> bool:
        if fresh:
            self.close()
        if not sync_playwright:
            if not LinkedInCompanyInspector._warned:
                print("⚠️ Playwright not installed; skipping employee stats scrape.")
                LinkedInCompanyInspector._warned = True
            return False
        if self._page:
            return True
        try:
            if self._semaphore and not self._semaphore_acquired:
                self._semaphore.acquire()
                self._semaphore_acquired = True
            self._play = sync_playwright().start()
            self._browser = self._play.chromium.launch(headless=True)
            self._page = self._browser.new_page()
            self._page.set_default_timeout(15000)
            return True
        except Exception as exc:
            if not LinkedInCompanyInspector._warned:
                print(f"⚠️ Could not start Playwright (employee stats skipped): {exc}")
                LinkedInCompanyInspector._warned = True
            self.close()
            return False

    def _reset_page(self) -> bool:
        if not self._browser:
            return False
        if self._page:
            with contextlib.suppress(Exception):
                self._page.close()
        self._page = self._browser.new_page()
        self._page.set_default_timeout(15000)
        return True

    def _safe_page_html(self) -> str:
        if not self._page:
            return ""
        with contextlib.suppress(Exception):
            root = self._page.locator("html")
            root.wait_for(state="attached", timeout=3000)
            return f"<html>{root.inner_html(timeout=3000)}</html>"
        return ""

    def _safe_link_snapshot(self):
        if not self._page:
            return []
        with contextlib.suppress(Exception):
            anchors = self._page.locator("a")
            return anchors.evaluate_all(
                """
                (els) => els.map(a => ({
                  href: a.href || "",
                  text: (a.innerText || a.textContent || "").trim()
                }))
                """
            )
        return []

    @staticmethod
    def _remaining_seconds(deadline: float) -> float:
        return max(1.0, deadline - time.monotonic())

    def close(self):
        if self._page:
            with contextlib.suppress(Exception):
                self._page.close()
            self._page = None
        if self._browser:
            with contextlib.suppress(Exception):
                self._browser.close()
            self._browser = None
        if self._play:
            with contextlib.suppress(Exception):
                self._play.stop()
            self._play = None
        if self._semaphore and self._semaphore_acquired:
            self._semaphore.release()
            self._semaphore_acquired = False

    @staticmethod
    def _deep_unquote(value: str, rounds: int = 3) -> str:
        result = value or ""
        for _ in range(rounds):
            new = urllib.parse.unquote(result)
            if new == result:
                break
            result = new
        return result

    @staticmethod
    def _extract_company_id(text: str) -> str:
        if not text:
            return ""
        decoded = LinkedInCompanyInspector._deep_unquote(text)

        def first_id(blob: str) -> str:
            match = re.search(r"\d+", blob or "")
            return match.group(0) if match else ""

        for value in (text, decoded):
            match = re.search(r"(?:currentCompany|facetCurrentCompany)=%5B([^\]]+)%5D", value)
            if match:
                found = first_id(match.group(1))
                if found:
                    return found

            match = re.search(r"(?:currentCompany|facetCurrentCompany)=\[([^\]]+)\]", value)
            if match:
                found = first_id(match.group(1))
                if found:
                    return found

            match = re.search(r"/(?:company|showcase)/(\d+)(?:[/?#]|$)", value)
            if match:
                return match.group(1)

            match = re.search(r"(?:companyId|companyUrn)=(\d+)", value)
            if match:
                return match.group(1)

        try:
            parsed = urllib.parse.urlsplit(decoded)
            params = urllib.parse.parse_qs(parsed.query)
            for key in ("session_redirect", "url", "redirect"):
                for candidate in params.get(key, []):
                    found = LinkedInCompanyInspector._extract_company_id(candidate)
                    if found:
                        return found
        except Exception:
            pass

        return ""

    @staticmethod
    def _extract_people_url(*candidates: str, company_id: str = "") -> str:
        for c in candidates:
            if not c:
                continue
            decoded = LinkedInCompanyInspector._deep_unquote(c)
            if "linkedin.com/search/results/people" in decoded:
                return decoded
        if company_id:
            return f"https://www.linkedin.com/search/results/people/?currentCompany=%5B{company_id}%5D"
        return ""

    @staticmethod
    def _parse_employee_count(text: str):
        if not text:
            return None
        # Range format: "51-200 employees" → return upper bound (200)
        range_match = re.search(r"[\d.,]+-(\s*[\d.,]+)\s*\+?\s*employees?", text, flags=re.I)
        if range_match:
            digits = re.sub(r"[^\d]", "", range_match.group(1))
            if digits:
                try:
                    return int(digits)
                except Exception:
                    pass
        # Exact or "10,001+" format: "782 employees", "10,001+ employees"
        match = re.search(r"([\d.,]+)\+?\s+employees?", text, flags=re.I)
        if not match:
            return None
        digits = re.sub(r"[^\d]", "", match.group(1))
        if not digits:
            return None
        try:
            return int(digits)
        except Exception:
            return None

    @staticmethod
    def _extract_employee_text(text: str) -> str:
        if not text:
            return ""
        for pattern in (
            r"(?:View|Discover)\s+all\s+[\d.,]+\s+employees?",
            # Standard LinkedIn bucket formats: "51-200 employees", "10,001+ employees", "782 employees"
            r"[\d.,]+(?:-[\d.,]+)?\+?\s+employees?",
        ):
            match = re.search(pattern, text, flags=re.I)
            if match:
                return match.group(0).strip()
        return ""

    @staticmethod
    def _classify_employee_gap(
        page_html: str,
        body_text: str,
        links,
        company_id: str,
        people_url: str,
    ) -> str:
        page_html = page_html or ""
        body_text = body_text or ""
        combined = f"{page_html}\n{body_text}".lower()
        login_markers = (
            "join linkedin",
            "sign in",
            "sign in to linkedin",
            "authwall",
            "session_redirect",
            "login",
        )
        if not (company_id or people_url):
            if any(marker in combined for marker in login_markers):
                return "AUTH_WALL_OR_LOGIN"
            if not body_text.strip() and not (links or []):
                return "EMPTY_GUEST_PAGE"
            return "NO_EMPLOYEE_MODULE"
        return "NO_PUBLIC_EMPLOYEE_TEXT"

    def find_linkedin_link(self, site_url: str) -> str:
        """Robust search for LinkedIn URL: Static -> Playwright -> Subpages."""
        base = _normalize_site_url(site_url)
        if not base: return ""
        deadline = time.monotonic() + WEBSITE_LOOKUP_BUDGET_SECONDS

        # 1. Static Check (Fast)
        r = _fetch_with_timeout(base, min(TIMEOUT, self._remaining_seconds(deadline)))
        if r:
            cands = _extract_candidates_from_html(r.text, r.url)
            if cands: return cands[0]

        # 2. Playwright Check (Dynamic - handles JS footers)
        if time.monotonic() < deadline and self._ensure(fresh=False):
            try:
                self._reset_page()
                self._page.goto(
                    base,
                    wait_until="domcontentloaded",
                    timeout=int(min(12000, self._remaining_seconds(deadline) * 1000)),
                )
                try:
                    self._page.wait_for_timeout(1200)  # Wait for JS hydration
                except Exception:
                    pass
                
                # Check rendered content (regex + scrape)
                content = self._safe_page_html()
                cands = _extract_candidates_from_html(content, base)
                if cands: return cands[0]
                
                # Check JS-generated anchors specifically
                links = self._safe_link_snapshot()
                for link in links or []:
                    norm = _clean_and_normalize_linkedin(str(link.get("href") or ""))
                    if norm:
                        return norm
            except Exception:
                pass

        # 3. Subpages Check (Static fallback - check common internal pages)
        try:
            parts = urllib.parse.urlsplit(base)
            root = f"{parts.scheme}://{parts.netloc}"
        except:
            root = base
            
        queue = [root + p for p in COMMON_INTERNAL if p != "/"]
        for p in queue[:MAX_INTERNAL_PAGES]:
             if time.monotonic() >= deadline:
                 break
             r = _fetch_with_timeout(p, min(TIMEOUT, self._remaining_seconds(deadline)))
             if r:
                 cands = _extract_candidates_from_html(r.text, r.url)
                 if cands: return cands[0]
        return ""

    def fetch(self, company_url: str):
        if not company_url: return None
        if "linkedin.com" not in company_url.lower(): return None
        deadline = time.monotonic() + LINKEDIN_FETCH_BUDGET_SECONDS
        
        # Retry logic: Try reusing browser first (False), then force fresh (True) if it fails
        for force_fresh in [False, True]:
            if time.monotonic() >= deadline:
                break
            if not self._ensure(fresh=force_fresh): continue
            
            try:
                self._reset_page()
                self._page.goto(
                    company_url,
                    wait_until="domcontentloaded",
                    timeout=int(min(15000, self._remaining_seconds(deadline) * 1000)),
                )
                try:
                    self._page.wait_for_timeout(1200)
                except Exception:
                    pass
                page_html = self._safe_page_html()
                body_text = ""
                with contextlib.suppress(Exception):
                    body_text = self._page.locator("body").inner_text(timeout=2000)

                links = self._safe_link_snapshot()

                # Look for employee count link
                locator = self._page.locator("a").filter(has_text=re.compile("employees?", re.I)).first
                try:
                    locator.wait_for(state="visible", timeout=6000)
                    text = locator.inner_text().strip()
                    href = locator.get_attribute("href") or ""
                except:
                    text = ""
                    href = ""
                    for link in links or []:
                        link_href = str(link.get("href") or "")
                        link_text = str(link.get("text") or "").strip()
                        if re.search(r"employees?", link_text, flags=re.I):
                            text = link_text
                            href = link_href
                            break
                        if not href and any(
                            token in link_href
                            for token in ("search/results/people", "facetCurrentCompany", "currentCompany")
                        ):
                            href = link_href
                            text = link_text
                    if not text:
                        text = self._extract_employee_text(body_text)

                    # If extraction failed and we haven't tried fresh yet, raise to trigger retry
                    if not (text or href):
                        if not force_fresh:
                            raise Exception("Retry with fresh browser")
                        # Fall through — still extract what we can from URL and page source

                decoded = self._deep_unquote(href)
                company_id = (
                    self._extract_company_id(decoded)
                    or self._extract_company_id(href)
                    or self._extract_company_id(company_url)
                )
                # Fallback: scan page HTML for urn:li:organization:{id} (present on guest pages)
                if not company_id:
                    with contextlib.suppress(Exception):
                        page_html = page_html or self._safe_page_html()
                        urn_match = re.search(r"urn:li:organization:(\d+)", page_html)
                        if urn_match:
                            company_id = urn_match.group(1)

                people_url = self._extract_people_url(decoded, href, company_url, company_id=company_id)
                employee_text = text or self._extract_employee_text(decoded)
                failure_reason = ""
                if not employee_text:
                    failure_reason = self._classify_employee_gap(
                        page_html=page_html,
                        body_text=body_text,
                        links=links,
                        company_id=company_id,
                        people_url=people_url,
                    )

                # Return partial data if we got anything useful
                if not (employee_text or company_id or people_url):
                    return {
                        "employee_text": "",
                        "employee_count": None,
                        "company_id": "",
                        "people_url": "",
                        "failure_reason": failure_reason or "UNKNOWN",
                    }

                return {
                    "employee_text": employee_text,
                    "employee_count": self._parse_employee_count(employee_text),
                    "company_id": company_id,
                    "people_url": people_url,
                    "failure_reason": failure_reason,
                }
            except Exception as e:
                if force_fresh:
                    print(f"⚠️ Employee scrape failed for {company_url}: {e}")
                # Loop continues to next attempt (fresh=True)
        
        return None

# =========== LINKEDIN FINDER CORE ===========
REQ_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
ALLOW_PERSON_FALLBACK = True

RE_LINKEDIN_URL = re.compile(
    r'((?:https?:)?\/\/(?:[a-z0-9-]+\.)?linkedin\.com\/[^\s"\'<>\]\}\),]+)',
    re.I
)

def _normalize_site_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if not re.match(r"^https?://", url, flags=re.I):
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    parts = parts._replace(fragment="")
    return urllib.parse.urlunsplit(parts)

def _clean_and_normalize_linkedin(raw: str) -> str:
    if not raw:
        return ""
    txt = html.unescape(raw)
    m = RE_LINKEDIN_URL.search(txt)
    if not m:
        return ""
    url = m.group(1)
    if url.startswith("//"):
        url = "https:" + url
    try:
        u = urllib.parse.urlsplit(url)
    except Exception:
        return ""
    host = u.netloc.lower()
    if "linkedin.com" not in host:
        return ""
    path = (u.path or "/")
    if ("share" in path) or ("shareArticle" in path) or ("embed" in path):
        return ""
    clean_path = re.sub(r"/+$", "", path)
    parts = [segment for segment in clean_path.split("/") if segment]
    lower_parts = [segment.lower() for segment in parts]

    pivot = None
    for marker in ("company", "showcase", "in"):
        if marker in lower_parts:
            pivot = marker
            break

    if pivot is None:
        return ""

    pivot_idx = lower_parts.index(pivot)
    slug = parts[pivot_idx + 1] if pivot_idx + 1 < len(parts) else ""
    if not slug or slug.isdigit():
        return ""

    p = clean_path.lower()
    is_company = pivot in {"company", "showcase"}
    is_person = pivot == "in"
    if not is_company and not (ALLOW_PERSON_FALLBACK and is_person):
        return ""
    clean_path = "/" + "/".join(parts[:pivot_idx + 2])
    return urllib.parse.urlunsplit(("https", "www.linkedin.com", clean_path, "", ""))


def _linkedin_url_kind(raw: str) -> str:
    norm = _clean_and_normalize_linkedin(raw)
    if not norm:
        return ""
    path = urllib.parse.urlsplit(norm).path.lower()
    if "/company/" in path:
        return "company"
    if "/showcase/" in path:
        return "showcase"
    if "/in/" in path:
        return "person"
    return "other"


COMMON_NAME_TOKENS = {
    "bv", "b", "v", "nv", "vof", "cv", "co", "company", "holding", "holdings",
    "group", "groep", "capital", "partners", "partner", "finance", "financial",
    "services", "service", "solutions", "solution", "international", "global",
    "europe", "european", "netherlands", "amsterdam", "nl", "the", "and",
}

STRONG_FOREIGN_SLUG_TOKENS = {
    "agency", "marketing", "media", "studio", "design", "creative",
    "communication", "communications", "advertising", "digital", "seo",
    "branding",
}


def _normalize_name_text(value: str) -> str:
    value = (value or "").lower()
    value = html.unescape(value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _name_tokens(value: str) -> list[str]:
    tokens = []
    for token in _normalize_name_text(value).split():
        if len(token) <= 1 or token in COMMON_NAME_TOKENS:
            continue
        tokens.append(token)
    return tokens


def _domain_keywords(website: str) -> list[str]:
    norm = _normalize_site_url(website)
    if not norm:
        return []
    try:
        host = urllib.parse.urlsplit(norm).netloc.lower()
    except Exception:
        return []
    parts = [p for p in re.split(r"[.\-]+", host) if p and p not in {"www", "com", "net", "org", "io", "eu", "nl"}]
    return [p for p in parts if len(p) > 1 and p not in COMMON_NAME_TOKENS]


def _name_acronym(tokens: list[str]) -> str:
    letters = "".join(token[0] for token in tokens if token)
    return letters if len(letters) >= 2 else ""


def _is_plausible_linkedin_match(company_name: str, website: str, linkedin_url: str) -> bool:
    kind = _linkedin_url_kind(linkedin_url)
    if kind not in {"company", "showcase"}:
        return True

    path = urllib.parse.urlsplit(_clean_and_normalize_linkedin(linkedin_url)).path
    parts = [p for p in path.split("/") if p]
    slug = parts[-1] if parts else ""
    if not slug or slug.isdigit():
        return True

    slug_tokens = [t for t in re.split(r"[^a-z0-9]+", slug.lower()) if t]
    slug_tokens = [t for t in slug_tokens if len(t) > 1 and t not in COMMON_NAME_TOKENS]
    if not slug_tokens:
        return True

    company_tokens = _name_tokens(company_name)
    domain_tokens = _domain_keywords(website)
    evidence_tokens = set(company_tokens) | set(domain_tokens)
    slug_set = set(slug_tokens)
    score = 0
    if evidence_tokens & slug_set:
        score += 2

    company_compact = "".join(company_tokens)
    domain_compact = "".join(domain_tokens)
    slug_compact = "".join(slug_tokens)
    if company_compact and len(company_compact) >= 5 and (
        company_compact in slug_compact or slug_compact in company_compact
    ):
        score += 1
    if domain_compact and len(domain_compact) >= 4 and (
        domain_compact in slug_compact or slug_compact in domain_compact
    ):
        score += 2

    acronym = _name_acronym(company_tokens)
    if acronym and (acronym in slug_compact or slug_compact in acronym):
        score += 1

    # Accept ambiguous cases unless there is strong evidence the slug belongs
    # to an unrelated agency/brand and we found no positive overlap at all.
    if score > 0:
        return True

    if slug_set & STRONG_FOREIGN_SLUG_TOKENS:
        return False

    return True

def _extract_candidates_from_html(html_text: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        href = urllib.parse.urljoin(base_url, a["href"].strip())
        norm = _clean_and_normalize_linkedin(href)
        if norm:
            found.append(norm)
    for tag in soup.find_all(True):
        for _attr, val in (tag.attrs or {}).items():
            vals = val if isinstance(val, (list, tuple)) else [val]
            for v in vals:
                if isinstance(v, str) and "linkedin.com" in v.lower():
                    absolute = urllib.parse.urljoin(base_url, html.unescape(v.strip()))
                    norm = _clean_and_normalize_linkedin(absolute)
                    if norm:
                        found.append(norm)
    for s in soup.find_all("script", type=lambda t: t and "ld+json" in t.lower()):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        def walk(x):
            if isinstance(x, dict):
                if "sameAs" in x:
                    items = x["sameAs"] if isinstance(x["sameAs"], list) else [x["sameAs"]]
                    for u in items:
                        n = _clean_and_normalize_linkedin(str(u))
                        if n: found.append(n)
                for v in x.values(): walk(v)
            elif isinstance(x, list):
                for i in x: walk(i)
        walk(data)
    for m in RE_LINKEDIN_URL.finditer(html_text):
        n = _clean_and_normalize_linkedin(m.group(0))
        if n:
            found.append(n)
    seen, unique = set(), []
    for u in found:
        if u not in seen:
            unique.append(u); seen.add(u)
    def sort_key(u: str):
        ul = u.lower()
        score = 0
        if "/company/" in ul or "/showcase/" in ul: score -= 10
        if "/in/" in ul: score += 5
        return (score, len(u))
    return sorted(unique, key=sort_key)

def _fetch(url: str) -> requests.Response | None:
    return _fetch_with_timeout(url, TIMEOUT)


def _fetch_with_timeout(url: str, timeout_seconds: float) -> requests.Response | None:
    try:
        r = requests.get(
            url,
            headers=REQ_HEADERS,
            timeout=max(1.0, float(timeout_seconds)),
            allow_redirects=True,
        )
        if 200 <= r.status_code < 400 and r.text:
            return r
    except Exception:
        pass
    return None

COMMON_INTERNAL = [
    "/", "/about", "/about-us", "/over", "/over-ons", "/team", "/contact",
    "/contact-us", "/company", "/who-we-are", "/wie-zijn-wij", "/overons",
    "/ons-team", "/social", "/connect", "/footer", "/imprint", "/legal",
    "/team-van", "/onze-mensen", "/organisatie", "/medewerkers",
    "/over-ons/team", "/over-ons/onze-mensen", "/over-ons/medewerkers", "/over-ons/adviseurs",
    "/our-team", "/the-team", "/people", "/staff", "/employees", "/leadership",
    "/management", "/partners", "/advisors", "/professionals"
]

def find_linkedin_on_site(site_url: str) -> str:
    base = _normalize_site_url(site_url)
    if not base:
        return ""
    try:
        parts = urllib.parse.urlsplit(base)
        host_root = f"{parts.scheme}://{parts.netloc}"
    except Exception:
        host_root = base
    queue = [host_root + p for p in COMMON_INTERNAL]
    for url in queue:
        r = _fetch(url)
        if not r:
            continue
        cands = _extract_candidates_from_html(r.text, r.url)
        if cands:
            return cands[0]
        time.sleep(0.2 + random.random() * 0.3)
    return ""

# ----- helper: build Google search formula for a given company cell -----
def build_google_formula(company_cell_a1: str) -> str:
    # One-line USER_ENTERED formula, US-style commas.
    # - Removes " and ' from the name
    # - Strips common company suffixes
    # - Collapses whitespace
    # - No quotes around the company name
    return (
        '=HYPERLINK('
        '"https://www.google.com/search?q=" & '
        'ENCODEURL('
        'REGEXREPLACE('
        'TRIM('
        'REGEXREPLACE('
        # remove punctuation quotes first
        f'SUBSTITUTE(SUBSTITUTE({company_cell_a1},CHAR(34),""),"\'",""),'
        # strip common suffixes
        '"(?i)\\\\b(bv|b\\\\.v\\\\.|nv|n\\\\.v\\\\.|vof|v\\\\.o\\\\.f\\\\.|cv|coöperatie|cooperative|holding|holdings?|groep|group|ltd|limited|inc|inc\\\\.|co\\\\.?|company|ag|sa|gmbh|plc)\\\\b",'
        '""'
        ')'
        '),'
        '"\\\\s+"," "'
        ') & " Netherlands LinkedIn"'
        '),'
        '"🔎 Google: Company + Netherlands + LinkedIn"'
        ')'
    )


# =========== PIPELINE WORKERS ===========

def _website_worker(task_queue, linkedin_queue, write_queue, columns, now, shutdown_event):
    """Pull RowTasks, find LinkedIn URLs, route to linkedin_queue or write_queue."""
    inspector = LinkedInCompanyInspector(browser_semaphore=_browser_semaphore)
    try:
        while True:
            if shutdown_event.is_set():
                break
            try:
                item = task_queue.get(timeout=1.0)
            except Empty:
                continue
            if item is _SENTINEL:
                break
            task = item
            try:
                if task.needs_link_lookup:
                    print(f"[{task.row_num}] checking {task.website} …")
                    li = inspector.find_linkedin_link(task.website)
                    if li:
                        if not _is_plausible_linkedin_match(task.company_name, task.website, li):
                            print(f"  [{task.row_num}] rejected LinkedIn match as implausible: {li}")
                            li = ""
                    if li:
                        li_kind = _linkedin_url_kind(li)
                        partial_cells = [(columns['linkedin'], li)]
                        if li_kind == "person":
                            partial_cells += [
                                (columns['status'], STATUS_PERSON_PROFILE),
                                (columns['emp_count'], ""),
                                (columns['emp_link'], ""),
                                (columns['company_id'], ""),
                                (columns['checked'], now),
                            ]
                            print(f"  -> {li} (person profile only)")
                            write_queue.put(RowResult(task.seq_num, task.row_num, partial_cells, "person profile"))
                        else:
                            partial_cells.append((columns['status'], STATUS_FOUND))
                            print(f"  -> {li}")
                            updated = RowTask(
                                seq_num=task.seq_num, row_num=task.row_num,
                                company_name=task.company_name,
                                website=task.website, linkedin_val=li,
                                status_val=STATUS_FOUND,
                                emp_count_v=task.emp_count_v, emp_link_v=task.emp_link_v,
                                company_id_v=task.company_id_v,
                                company_cell_a1=task.company_cell_a1,
                                has_real_li=True, needs_link_lookup=False, needs_backfill=True,
                            )
                            linkedin_queue.put((updated, li, partial_cells))
                    else:
                        formula = build_google_formula(task.company_cell_a1)
                        cells = [
                            (columns['linkedin'], formula),
                            (columns['status'], STATUS_NONE),
                            (columns['emp_count'], ""),
                            (columns['emp_link'], ""),
                            (columns['company_id'], ""),
                            (columns['checked'], now),
                        ]
                        print(f"  [{task.row_num}] -> (none) — inserted Google search formula")
                        write_queue.put(RowResult(task.seq_num, task.row_num, cells, "none — formula"))
                elif task.needs_backfill:
                    print(f"[{task.row_num}] backfilling {task.linkedin_val} …")
                    linkedin_queue.put((task, task.linkedin_val, []))
            except Exception as e:
                print(f"[website_worker] row {task.row_num} error: {e}")
                write_queue.put(RowResult(task.seq_num, task.row_num, [], f"ERROR: {e}", advance_pointer=False))
    finally:
        inspector.close()


def _linkedin_worker(linkedin_queue, write_queue, columns, now, shutdown_event):
    """Fetch employee data from LinkedIn with jitter delay, emit RowResult."""
    inspector = LinkedInCompanyInspector(browser_semaphore=_browser_semaphore)
    try:
        while True:
            if shutdown_event.is_set():
                break
            try:
                item = linkedin_queue.get(timeout=1.0)
            except Empty:
                continue
            if item is _SENTINEL:
                break
            task, li_url, partial_cells = item
            try:
                time.sleep(random.uniform(LI_DELAY_MIN, LI_DELAY_MAX))
                stats = inspector.fetch(li_url)
                cells = list(partial_cells)
                if stats:
                    count_value = stats.get("employee_count")
                    if count_value is None:
                        count_value = stats.get("employee_text")
                    cells += [
                        (columns['emp_count'], count_value or ""),
                        (columns['emp_link'], stats.get("people_url") or ""),
                        (columns['company_id'], stats.get("company_id") or ""),
                    ]
                    if stats.get("employee_text") or stats.get("employee_count") is not None:
                        print(
                            f"  [{task.row_num}] employees: "
                            f"{stats.get('employee_text') or stats.get('employee_count', '?')} "
                            f"| people link: {stats.get('people_url', '')}"
                        )
                    else:
                        reason = stats.get("failure_reason") or "UNKNOWN"
                        suffix = f" | people link: {stats.get('people_url', '')}" if stats.get("people_url") else ""
                        print(f"  [{task.row_num}] employee stats unavailable ({reason}){suffix}")
                else:
                    print(f"  [{task.row_num}] employee stats unavailable")
                cells.append((columns['checked'], now))
                write_queue.put(RowResult(task.seq_num, task.row_num, cells, "enriched"))
            except Exception as e:
                print(f"[linkedin_worker] row {task.row_num} error: {e}")
                # Still emit partial_cells + checked so the ordered buffer never stalls
                cells = list(partial_cells)
                cells.append((columns['checked'], now))
                write_queue.put(RowResult(task.seq_num, task.row_num, cells, f"ERROR: {e}", advance_pointer=False))
    finally:
        inspector.close()


def _writer_thread(write_queue, worksheet, meta_ws, total_tasks, seq_next_rows, shutdown_event, run_tracker=None):
    """Ordered batch writer — only advances _meta.next_row through contiguous completed rows."""
    completed = {}           # seq_num -> bool (whether this result may advance the pointer)
    next_to_commit = 0       # lowest seq_num not yet committed
    pending_updates = []     # gspread batch update payloads
    rows_since_flush = 0
    last_flush_time = time.monotonic()
    processed = 0
    pointer_dirty = False
    next_pointer = None

    def flush():
        nonlocal pending_updates, rows_since_flush, last_flush_time, pointer_dirty
        if pending_updates:
            guarded_gspread_call(
                lambda: worksheet.spreadsheet.values_batch_update({
                    "valueInputOption": "USER_ENTERED",
                    "data": pending_updates,
                }),
                operation="write",
                description="Flush enrichment batch updates",
            )
        if pointer_dirty and next_pointer is not None:
            meta_set_next_row(meta_ws, next_pointer)
            print(
                f"✅ wrote {rows_since_flush} rows "
                f"(next row {next_pointer}; {processed}/{total_tasks} total)"
            )
        if run_tracker is not None:
            run_tracker.update_batch_progress(
                batch_processed=processed,
                next_pointer=next_pointer,
                last_row_num=result.row_num if 'result' in locals() else None,
            )
        pending_updates = []
        rows_since_flush = 0
        pointer_dirty = False
        last_flush_time = time.monotonic()

    while True:
        try:
            item = write_queue.get(timeout=1.0)
        except Empty:
            if pending_updates and (time.monotonic() - last_flush_time) >= WRITER_FLUSH_INTERVAL:
                flush()
            if shutdown_event.is_set():
                break
            continue

        if item is _SENTINEL:
            break

        result = item
        completed[result.seq_num] = result.advance_pointer

        # Queue this row's cell updates immediately, even if pointer cannot advance yet.
        for col_num, value in result.cells:
            if col_num and value is not None:
                a1 = gspread.utils.rowcol_to_a1(result.row_num, col_num)
                pending_updates.append({
                    "range": f"{worksheet.title}!{a1}",
                    "values": [[value]],
                })
        processed += 1
        rows_since_flush += 1

        # Advance pointer only through contiguous successful results.
        while next_to_commit in completed:
            can_advance = completed[next_to_commit]
            if not can_advance:
                break
            completed.pop(next_to_commit)
            next_pointer = seq_next_rows[next_to_commit]
            pointer_dirty = True
            next_to_commit += 1

        if rows_since_flush >= WRITER_FLUSH_BATCH or (time.monotonic() - last_flush_time) >= WRITER_FLUSH_INTERVAL:
            flush()

    flush()  # Final flush of any remaining buffered rows


# =========== MAIN ===========
def main(run_tracker=None) -> bool:
    gc = auth_client()
    sh = guarded_gspread_call(
        lambda: gc.open_by_key(SHEET_ID),
        operation="read",
        description="Open enrichment spreadsheet",
    )
    ws = guarded_gspread_call(
        lambda: sh.worksheet(TAB_NAME),
        operation="read",
        description="Open enrichment worksheet",
    )

    meta = get_or_create_meta(sh)
    start_row = meta_get_next_row(meta)

    headers = [h.strip() for h in ws.row_values(1)]
    header_lc = {h.lower(): i+1 for i, h in enumerate(headers)}

    def col_index(*names):
        for n in names:
            idx = header_lc.get(n.lower())
            if idx: return idx
        return None

    col_company    = col_index(COL_COMPANY)
    col_website    = col_index("website")
    col_linkedin   = col_index(COL_LINKEDIN, "Find LinkedIn", "Company LinkedIn")
    col_status     = col_index(COL_STATUS)
    col_checked    = col_index(COL_LAST_CHECKED)
    col_emp_count  = col_index(COL_EMP_COUNT)
    col_emp_link   = col_index(COL_EMP_LINK)
    col_company_id = col_index(COL_COMPANY_ID)

    if not col_website:
        err_exit("Could not find the 'website' column.")
    if not col_company:
        err_exit("Could not find the 'name' column.")

    # Ensure LinkedIn/Status/Checked columns exist
    need_updates = []
    def ensure_column(current_idx, header_label):
        nonlocal need_updates
        if current_idx:
            return current_idx
        guarded_gspread_call(
            lambda: ws.add_cols(1),
            operation="write",
            description=f"Add enrichment column {header_label}",
        )
        idx = ws.col_count
        need_updates.append((1, idx, header_label))
        return idx

    col_linkedin   = ensure_column(col_linkedin, COL_LINKEDIN)
    col_status     = ensure_column(col_status, COL_STATUS)
    col_checked    = ensure_column(col_checked, COL_LAST_CHECKED)
    col_emp_count  = ensure_column(col_emp_count, COL_EMP_COUNT)
    col_emp_link   = ensure_column(col_emp_link, COL_EMP_LINK)
    col_company_id = ensure_column(col_company_id, COL_COMPANY_ID)

    if need_updates:
        for r, c, v in need_updates:
            guarded_gspread_call(
                lambda row=r, col=c, value=v: ws.update_cell(row, col, value),
                operation="write",
                description=f"Write enrichment header {v}",
            )
        headers = [
            h.strip()
            for h in guarded_gspread_call(
                lambda: ws.row_values(1),
                operation="read",
                description="Read enrichment header row",
            )
        ]
        header_lc = {h.lower(): i+1 for i, h in enumerate(headers)}

    # Read all values once
    values = guarded_gspread_call(
        lambda: ws.get_all_values(),
        operation="read",
        description="Read enrichment worksheet values",
    )
    if not values:
        print("No data.")
        return False

    pending_before_batch = _count_pending_rows(values, col_website, col_linkedin, col_status)

    # Build candidate rows list (wrap from pointer)
    all_rows = list(range(start_row, ws.row_count + 1)) + list(range(2, start_row))
    work_rows, scanned = [], 0
    for r in all_rows:
        if r > len(values):
            continue
        row = values[r-1]
        def v(col, _row=row):
            return (_row[col-1] if col and col-1 < len(_row) else "").strip()
        website      = v(col_website)
        company_name = v(col_company)
        linkedin_val = v(col_linkedin)
        status_val   = v(col_status)
        emp_count_v  = v(col_emp_count)
        emp_link_v   = v(col_emp_link)
        company_id_v = v(col_company_id)
        has_formula  = linkedin_val.startswith("=")
        linkedin_kind = _linkedin_url_kind(linkedin_val) if not has_formula else ""
        has_real_li  = linkedin_kind in {"company", "showcase"}
        is_none      = status_val == STATUS_NONE
        is_person_profile = status_val == STATUS_PERSON_PROFILE
        needs_link_lookup = (not has_real_li) and not is_none and not is_person_profile

        if not website:
            continue
        if not needs_link_lookup:
            continue
        work_rows.append(r)
        scanned += 1
        if scanned >= BATCH_SIZE:
            break

    if not work_rows:
        print("Nothing to update.")
        meta_set_next_row(meta, start_row if start_row <= 3 else 2)
        if run_tracker is not None:
            run_tracker.finish_run(final_pending=pending_before_batch)
        return False

    print(f"🔎 Processing {len(work_rows)} rows starting at sheet row {work_rows[0]}.")
    if run_tracker is not None:
        run_tracker.start_batch(
            batch_total=len(work_rows),
            start_row=work_rows[0],
            pending_before_batch=pending_before_batch,
        )

    # --- Build RowTask list ---
    columns = {
        'linkedin':   col_linkedin,
        'status':     col_status,
        'checked':    col_checked,
        'emp_count':  col_emp_count,
        'emp_link':   col_emp_link,
        'company_id': col_company_id,
        'company':    col_company,
    }
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    tasks = []
    seq_next_rows = []
    for seq, r in enumerate(work_rows):
        row = values[r-1]
        def v(col, _row=row):
            return (_row[col-1] if col and col-1 < len(_row) else "").strip()
        website      = v(col_website)
        linkedin_val = v(col_linkedin)
        status_val   = v(col_status)
        emp_count_v  = v(col_emp_count)
        emp_link_v   = v(col_emp_link)
        company_id_v = v(col_company_id)
        has_formula  = linkedin_val.startswith("=")
        linkedin_kind = _linkedin_url_kind(linkedin_val) if not has_formula else ""
        has_real_li  = linkedin_kind in {"company", "showcase"}
        is_none      = status_val == STATUS_NONE
        is_person_profile = status_val == STATUS_PERSON_PROFILE
        employee_missing = not (emp_count_v and emp_link_v and company_id_v)
        needs_link_lookup = (not has_real_li) and not is_none and not is_person_profile
        needs_backfill = has_real_li and employee_missing

        tasks.append(RowTask(
            seq_num=seq,
            row_num=r,
            company_name=company_name,
            website=website,
            linkedin_val=linkedin_val,
            status_val=status_val,
            emp_count_v=emp_count_v,
            emp_link_v=emp_link_v,
            company_id_v=company_id_v,
            company_cell_a1=gspread.utils.rowcol_to_a1(r, col_company),
            has_real_li=has_real_li,
            needs_link_lookup=needs_link_lookup,
            needs_backfill=needs_backfill,
        ))
        seq_next_rows.append(2 if r >= len(values) else r + 1)

    # --- Start pipeline ---
    task_queue    = Queue()
    linkedin_queue = Queue()
    write_queue   = Queue()
    shutdown_event = threading.Event()

    website_threads = [
        threading.Thread(
            target=_website_worker,
            args=(task_queue, linkedin_queue, write_queue, columns, now, shutdown_event),
            daemon=True,
        )
        for _ in range(WEBSITE_WORKERS)
    ]
    linkedin_threads = [
        threading.Thread(
            target=_linkedin_worker,
            args=(linkedin_queue, write_queue, columns, now, shutdown_event),
            daemon=True,
        )
        for _ in range(LINKEDIN_WORKERS)
    ]
    writer = threading.Thread(
        target=_writer_thread,
        args=(write_queue, ws, meta, len(tasks), seq_next_rows, shutdown_event, run_tracker),
        daemon=True,
    )
    try:
        for t in website_threads + linkedin_threads:
            t.start()
        writer.start()

        for task in tasks:
            task_queue.put(task)
        for _ in website_threads:
            task_queue.put(_SENTINEL)

        for t in website_threads:
            t.join()
        for _ in linkedin_threads:
            linkedin_queue.put(_SENTINEL)
        for t in linkedin_threads:
            t.join()

        write_queue.put(_SENTINEL)
        writer.join(timeout=60)
        if writer.is_alive():
            raise RuntimeError("Writer thread did not finish in time.")
        refreshed_values = guarded_gspread_call(
            lambda: ws.get_all_values(),
            operation="read",
            description="Refresh enrichment worksheet values after batch",
        )
        pending_after_batch = _count_pending_rows(refreshed_values, col_website, col_linkedin, col_status)
        if run_tracker is not None:
            next_pointer_value = meta_get_next_row(meta)
            run_tracker.finish_batch(
                pending_after_batch=pending_after_batch,
                next_pointer=next_pointer_value,
            )
        return True
    except KeyboardInterrupt:
        shutdown_event.set()
        for _ in website_threads:
            task_queue.put(_SENTINEL)
        for _ in linkedin_threads:
            linkedin_queue.put(_SENTINEL)
        write_queue.put(_SENTINEL)
        for t in website_threads + linkedin_threads:
            t.join(timeout=5)
        writer.join(timeout=60)
        raise

# Optional: --loop mode
# Optional: loop + reset controls
if __name__ == "__main__":
    import argparse, os, sys, time
    from pathlib import Path
    import gspread
    from google.oauth2 import service_account

    p = argparse.ArgumentParser()
    p.add_argument("--loop", action="store_true", help="keep running forever")
    p.add_argument("--drain", action="store_true", help="run consecutive batches until no rows remain")
    p.add_argument("--interval", type=int, default=300, help="seconds between passes in --loop mode")
    p.add_argument("--reset-pointer", action="store_true",
                   help="set _meta.next_row back to 2 (start from top)")
    p.add_argument("--reset-hard", action="store_true",
                   help="also clear LinkedIn status/Last checked (does NOT touch company/website)")

    args = p.parse_args()

    if args.loop and args.drain:
        p.error("--loop and --drain cannot be used together.")

    # Run resets if requested
    if args.reset_pointer or args.reset_hard:
        creds = service_account.Credentials.from_service_account_file(
            os.path.expanduser(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json")),
            scopes=["https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive"]
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEET_ID)

        meta = get_or_create_meta(sh)
        meta_set_next_row(meta, 2)
        print("🔁 Pointer reset to row 2.")

        if args.reset_hard:
            ws = sh.worksheet(TAB_NAME)
            headers = [h.strip() for h in ws.row_values(1)]
            hdr = {h.lower(): i+1 for i, h in enumerate(headers)}

            def idx(*names):
                for n in names:
                    i = hdr.get(n.lower())
                    if i:
                        return i

            col_linkedin   = idx("LinkedIn", "Find LinkedIn", "Company LinkedIn")
            col_status     = idx("LinkedIn status")
            col_checked    = idx("Last checked")
            col_emp_count  = idx(COL_EMP_COUNT)
            col_emp_link   = idx(COL_EMP_LINK)
            col_company_id = idx(COL_COMPANY_ID)

            # clear values in these columns (rows 2..last)
            vals = ws.get_all_values()
            last_row = max(2, len(vals))
            clears = []
            def clear_col(ci):
                if not ci: return
                start = gspread.utils.rowcol_to_a1(2, ci)
                end   = gspread.utils.rowcol_to_a1(last_row, ci)
                clears.append({"range": f"{ws.title}!{start}:{end}",
                               "values": [[""]]*(last_row-1)})
            clear_col(col_linkedin)
            clear_col(col_status)
            clear_col(col_checked)
            clear_col(col_emp_count)
            clear_col(col_emp_link)
            clear_col(col_company_id)

            if clears:
                ws.spreadsheet.values_batch_update({"valueInputOption": "RAW", "data": clears})
                print("🧹 Cleared LinkedIn/status/employee columns.")

        # if we only wanted to reset and not loop, stop here
        if not args.loop and not args.drain:
            sys.exit(0)


    # Normal execution
    if args.loop:
        tracker = None
        while True:
            try:
                if tracker is None:
                    gc = auth_client()
                    sh = guarded_gspread_call(lambda: gc.open_by_key(SHEET_ID), operation="read", description="Open enrichment spreadsheet for loop run")
                    ws = guarded_gspread_call(lambda: sh.worksheet(TAB_NAME), operation="read", description="Open enrichment worksheet for loop run")
                    values = guarded_gspread_call(lambda: ws.get_all_values(), operation="read", description="Read enrichment worksheet for loop run")
                    headers = [h.strip() for h in (values[0] if values else [])]
                    header_lc = {h.lower(): i + 1 for i, h in enumerate(headers)}
                    tracker = EnrichmentRunTracker(
                        mode="loop",
                        initial_pending=_count_pending_rows(
                            values,
                            header_lc.get("website"),
                            header_lc.get(COL_LINKEDIN.lower()),
                            header_lc.get(COL_STATUS.lower()),
                        ),
                        batch_size=BATCH_SIZE,
                    )
                main(run_tracker=tracker)
            except Exception as e:
                print("Run error:", e)
                if tracker is not None:
                    tracker.finish_run(error=str(e))
                    tracker = None
            time.sleep(max(5, args.interval))
    elif args.drain:
        # Ensure the watcher lock is cleared on every exit path (success,
        # crash, SIGINT, SIGTERM) so the dashboard never shows a ghost
        # "Stale lock" warning after a clean drain.
        import atexit, signal
        atexit.register(_release_enrichment_lock_if_owned)

        def _drain_signal_handler(signum, _frame):
            _release_enrichment_lock_if_owned()
            sys.exit(128 + signum)

        for _sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(_sig, _drain_signal_handler)
            except (ValueError, OSError):
                pass

        drain_tracker = None
        while True:
            interrupted = False
            try:
                if drain_tracker is None:
                    gc = auth_client()
                    sh = guarded_gspread_call(lambda: gc.open_by_key(SHEET_ID), operation="read", description="Open enrichment spreadsheet for drain run")
                    ws = guarded_gspread_call(lambda: sh.worksheet(TAB_NAME), operation="read", description="Open enrichment worksheet for drain run")
                    values = guarded_gspread_call(lambda: ws.get_all_values(), operation="read", description="Read enrichment worksheet for drain run")
                    headers = [h.strip() for h in (values[0] if values else [])]
                    header_lc = {h.lower(): i + 1 for i, h in enumerate(headers)}
                    drain_tracker = EnrichmentRunTracker(
                        mode="drain",
                        initial_pending=_count_pending_rows(
                            values,
                            header_lc.get("website"),
                            header_lc.get(COL_LINKEDIN.lower()),
                            header_lc.get(COL_STATUS.lower()),
                        ),
                        batch_size=BATCH_SIZE,
                    )
                progress = main(run_tracker=drain_tracker)
            except Exception as e:
                print("Run error:", e)
                progress = False
                interrupted = True
                if drain_tracker is not None:
                    drain_tracker.finish_run(error=str(e))
                    drain_tracker = None
            if interrupted:
                print("⏸️ Drain interrupted by an error. Resume will continue from the saved next_row pointer.")
                break
            if not progress:
                if drain_tracker is not None:
                    drain_tracker.finish_run(final_pending=drain_tracker.last_known_pending)
                print("✅ Drain complete – no remaining leads to process.")
                break
            print(f"🔁 Processed a batch of up to {BATCH_SIZE} rows; continuing…")
            time.sleep(1)
    else:
        single_tracker = None
        try:
            gc = auth_client()
            sh = guarded_gspread_call(lambda: gc.open_by_key(SHEET_ID), operation="read", description="Open enrichment spreadsheet for single run")
            ws = guarded_gspread_call(lambda: sh.worksheet(TAB_NAME), operation="read", description="Open enrichment worksheet for single run")
            values = guarded_gspread_call(lambda: ws.get_all_values(), operation="read", description="Read enrichment worksheet for single run")
            headers = [h.strip() for h in (values[0] if values else [])]
            header_lc = {h.lower(): i + 1 for i, h in enumerate(headers)}
            single_tracker = EnrichmentRunTracker(
                mode="single",
                initial_pending=_count_pending_rows(
                    values,
                    header_lc.get("website"),
                    header_lc.get(COL_LINKEDIN.lower()),
                    header_lc.get(COL_STATUS.lower()),
                ),
                batch_size=BATCH_SIZE,
            )
            progressed = main(run_tracker=single_tracker)
            single_tracker.finish_run(final_pending=0 if not progressed else single_tracker.last_known_pending)
        except Exception as e:
            if single_tracker is not None:
                single_tracker.finish_run(error=str(e))
            raise
