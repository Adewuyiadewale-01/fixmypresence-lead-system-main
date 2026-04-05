"""Pipeline Status - Unified status collector for all subsystems.

Reads lock files, process state, and progress data to build a single
pipeline snapshot for the overview dashboard. Most reads are local files;
live metrics (enrichment counts, settlement totals) are fetched from
Google Sheets with a 30-second TTL cache via _get_live_metrics().
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import settings

sys.path.insert(0, str(settings.BOTS_DIR))

# Lock file paths
CONTROLLER_DIR = settings.CONTROLLER_DIR
ENRICHMENT_LOCK = CONTROLLER_DIR / "enrichment_lock.json"
ULTRA_LOCK = CONTROLLER_DIR / "ultra_validated_lock.json"
SCRAPER_V3_LOCK = CONTROLLER_DIR / "scraper_v3_lock.json"
ENRICHMENT_BATCH_COUNTER = CONTROLLER_DIR / "enrichment_batch_counter.txt"
SCRAPER_V3_SUMMARY_DIR = settings.BOTS_DIR / "enricher" / "outputs" / "summaries"

LIVE_STATUS_TTL_SECONDS = 30
_LIVE_STATUS_CACHE: Dict[str, Any] = {"fetched_at": 0.0, "data": None}

# Scraper recommendation thresholds are intentionally conservative and are
# calibrated to current Maps Scraper behavior:
# - leads/100 searches below 120 usually means weak keyword/location yield
# - 220+ leads/100 searches usually means the current mix is producing well
# - website-found rate below 55% often indicates noisy/non-business results
# - 75%+ website-found rate is a healthy signal for safely increasing workers
SCRAPER_LOW_YIELD_PER_100 = 120.0
SCRAPER_HIGH_YIELD_PER_100 = 220.0
SCRAPER_LOW_WEBSITE_RATE = 55.0
SCRAPER_HIGH_WEBSITE_RATE = 75.0

CREDENTIALS_PATH = Path(
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json")
)
SOURCE_SHEET_ID = os.environ.get(
    "SHEET_ID", "1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc"
)
DEST_SHEET_ID = os.environ.get(
    "DEST_SHEET_ID", "1YF8WvLTPu-Raf22rHaaphauj-w-n64CRu83ZeAtHYAc"
)
VALIDATED_TAB = os.environ.get("TAB_NAME", "Validated_leads")
ULTRA_TAB = os.environ.get("ULTRA_TAB_NAME", "Ultra_validated")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

try:
    from common.google_client import GoogleClient
    from common.config import SOURCE_WEBHOOK_URL, WEBHOOK_SECRET
except ImportError:
    GoogleClient = None
    SOURCE_WEBHOOK_URL = None
    WEBHOOK_SECRET = None

try:
    from maps_scraper.config import (
        SCRAPER_HEALTH_FILE,
        MASTER_OUTPUT as SCRAPER_MASTER_OUTPUT,
        NUM_WORKERS as SCRAPER_NUM_WORKERS,
    )
except ImportError:
    SCRAPER_HEALTH_FILE = None
    SCRAPER_MASTER_OUTPUT = None
    SCRAPER_NUM_WORKERS = None


# ============================================================================
# HELPERS
# ============================================================================

def _read_json(path: Path) -> Optional[dict]:
    """Safely read a JSON file."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_latest_json(directory: Path, pattern: str) -> Optional[dict]:
    try:
        matches = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in matches:
        data = _read_json(path)
        if data is not None:
            return data
    return None


def _auth_client():
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(
        os.path.expanduser(str(CREDENTIALS_PATH)),
        scopes=SCOPES,
    )
    return gspread.authorize(creds)


def _fetch_scraper_v3_ready_counts() -> Dict[str, int]:
    if SOURCE_WEBHOOK_URL is None or WEBHOOK_SECRET is None:
        return {"alpha": None, "beta": None, "gamma": None, "total": None}
    import httpx

    try:
        response = httpx.get(
            SOURCE_WEBHOOK_URL,
            params={"token": WEBHOOK_SECRET},
            timeout=20.0,
            verify=False,
        )
        response.raise_for_status()
        rows = (response.json() or {}).get("urls", [])
    except Exception:
        return {"alpha": None, "beta": None, "gamma": None, "total": None}

    counts = {"alpha": 0, "beta": 0, "gamma": 0, "total": 0}
    for row in rows:
        tab = str(row.get("tab") or "").strip().lower()
        if tab in ("alpha", "beta", "gamma"):
            counts[tab] += 1
        counts["total"] += 1
    return counts


def _safe_cell(row: list[str], idx: Optional[int]) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def _compute_live_metrics() -> Dict[str, Any]:
    client = _auth_client()
    src_book = client.open_by_key(SOURCE_SHEET_ID)
    validated_ws = src_book.worksheet(VALIDATED_TAB)
    ultra_ws = src_book.worksheet(ULTRA_TAB)
    dest_book = client.open_by_key(DEST_SHEET_ID)

    # LinkedIn enrichment metrics
    validated_values = validated_ws.get_all_values()
    v_header = [h.strip().lower() for h in validated_values[0]] if validated_values else []
    v_rows = validated_values[1:]
    v_idx = {h: i for i, h in enumerate(v_header)}

    def vv(row: list[str], name: str) -> str:
        return _safe_cell(row, v_idx.get(name))

    valid_live_statuses = {"Live", "Live " , "LIVE"}
    enrichment_pending = 0
    enrichment_checked = 0
    enrichment_found = 0
    enrichment_person = 0
    enrichment_employees = 0

    for row in v_rows:
        website = vv(row, "website")
        web_status = vv(row, "website status")
        linkedin_val = vv(row, "linkedin")
        status_val = vv(row, "linkedin status")
        emp_count_v = vv(row, "linkedin employees")
        emp_link_v = vv(row, "linkedin people link")
        company_id_v = vv(row, "linkedin company id")
        last_checked_v = vv(row, "last checked")

        if last_checked_v:
            enrichment_checked += 1
        if status_val == "FOUND":
            enrichment_found += 1
        elif status_val == "PERSON_PROFILE_ONLY":
            enrichment_person += 1
        if emp_count_v and emp_link_v and company_id_v:
            enrichment_employees += 1

        if not website or web_status not in valid_live_statuses:
            continue

        has_formula = linkedin_val.startswith("=")
        has_real_li = bool(linkedin_val) and not has_formula and "linkedin.com" in linkedin_val.lower()
        is_none = status_val == "NONE"
        is_person_profile = status_val == "PERSON_PROFILE_ONLY"
        needs_link_lookup = (not has_real_li) and not is_none and not is_person_profile
        employee_missing = not (emp_count_v and emp_link_v and company_id_v)
        needs_backfill = has_real_li and employee_missing
        if needs_link_lookup or needs_backfill:
            enrichment_pending += 1

    # Keyword + scoring metrics
    ultra_values = ultra_ws.get_all_values()
    u_header = [h.strip().lower() for h in ultra_values[0]] if ultra_values else []
    u_rows = ultra_values[1:]
    u_idx = {h: i for i, h in enumerate(u_header)}

    def uv(row: list[str], name: str) -> str:
        return _safe_cell(row, u_idx.get(name))

    keyword_pending = 0
    keyword_cat1 = 0
    keyword_cat2 = 0
    scoring_ready = 0
    scoring_moved = 0

    for row in u_rows:
        website = uv(row, "website")
        kw_status = uv(row, "keyword status").lower()
        moved = uv(row, "moved").upper()

        if not website:
            continue
        if not kw_status:
            keyword_pending += 1
        elif kw_status == "cat 1":
            keyword_cat1 += 1
        elif kw_status == "cat 2":
            keyword_cat2 += 1

        if kw_status in ("cat 1", "cat 2") and moved != "MOVED":
            scoring_ready += 1
        if moved == "MOVED":
            scoring_moved += 1

    # Settlement counts
    settlement = {}
    for tab in ("Alpha", "Beta", "Gamma"):
        try:
            ws = dest_book.worksheet(tab)
            settlement[tab.lower()] = max(0, len(ws.get_all_values()) - 1)
        except Exception:
            settlement[tab.lower()] = None

    # Scraper V3 ready counts from source webhook
    scraper_v3_ready = _fetch_scraper_v3_ready_counts()

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "enrichment": {
            "pending": enrichment_pending,
            "checked": enrichment_checked,
            "found": enrichment_found,
            "person": enrichment_person,
            "employees": enrichment_employees,
        },
        "keyword_detection": {
            "pending": keyword_pending,
            "cat1": keyword_cat1,
            "cat2": keyword_cat2,
        },
        "scoring": {
            "ready": scoring_ready,
            "moved": scoring_moved,
        },
        "settlement": settlement,
        "scraper_v3_ready": scraper_v3_ready,
    }


def _get_live_metrics() -> Dict[str, Any]:
    now = time.time()
    cached = _LIVE_STATUS_CACHE.get("data")
    fetched_at = float(_LIVE_STATUS_CACHE.get("fetched_at") or 0.0)
    if cached is not None and now - fetched_at < LIVE_STATUS_TTL_SECONDS:
        return cached

    try:
        data = _compute_live_metrics()
        _LIVE_STATUS_CACHE["data"] = data
        _LIVE_STATUS_CACHE["fetched_at"] = now
        return data
    except Exception:
        return cached or {
            "fetched_at": None,
            "enrichment": {"pending": None, "checked": None, "found": None, "person": None, "employees": None},
            "keyword_detection": {"pending": None, "cat1": None, "cat2": None},
            "scoring": {"ready": None, "moved": None},
            "settlement": {"alpha": None, "beta": None, "gamma": None},
            "scraper_v3_ready": {"alpha": None, "beta": None, "gamma": None, "total": None},
        }


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def _lock_age_minutes(locked_at: str) -> Optional[float]:
    """Get age of a lock in minutes from an ISO timestamp."""
    try:
        lock_time = datetime.fromisoformat(locked_at.replace("Z", "+00:00"))
        if lock_time.tzinfo is None:
            now = datetime.now()
        else:
            now = datetime.now(lock_time.tzinfo)
        return (now - lock_time).total_seconds() / 60
    except Exception:
        return None


def _elapsed_hours_between(start_time_iso: Optional[str], end_time_iso: Optional[str]) -> Optional[float]:
    """Elapsed runtime between two ISO timestamps, falling back to now when no end time exists."""
    if not start_time_iso:
        return None
    try:
        started = datetime.fromisoformat(start_time_iso.replace("Z", "+00:00"))
        if end_time_iso:
            ended = datetime.fromisoformat(end_time_iso.replace("Z", "+00:00"))
        elif started.tzinfo is None:
            ended = datetime.now()
        else:
            ended = datetime.now(started.tzinfo)
        elapsed = (ended - started).total_seconds() / 3600
        return elapsed if elapsed > 0 else None
    except Exception:
        return None


def _scraper_recommendation(
    searches: int,
    batches: int,
    leads_per_100: Optional[float],
    website_found_rate: Optional[float],
    captcha_count: int,
    error_count: int,
) -> tuple[str, str]:
    """Heuristic recommendation for scraper aggression on overview."""
    if searches < 20 or batches < 1:
        return ("Learning baseline", "info")

    if captcha_count > 0:
        return ("Reduce aggression", "warning")
    if error_count >= 5:
        return ("Inspect worker stability", "warning")

    if website_found_rate is None:
        if leads_per_100 is not None and leads_per_100 >= SCRAPER_HIGH_YIELD_PER_100:
            return ("Healthy yield", "info")
        if leads_per_100 is not None and leads_per_100 < SCRAPER_LOW_YIELD_PER_100:
            return ("Low yield, inspect inputs", "warning")
        return ("Benchmarking", "info")

    if website_found_rate >= SCRAPER_HIGH_WEBSITE_RATE and (leads_per_100 or 0) >= SCRAPER_HIGH_YIELD_PER_100:
        return ("Safe to increase ~20%", "info")
    if website_found_rate < SCRAPER_LOW_WEBSITE_RATE or ((leads_per_100 or 0) < SCRAPER_LOW_YIELD_PER_100):
        return ("Reduce aggression", "warning")
    return ("Optimal", "info")


def _read_scraper_output_health() -> Dict[str, Optional[float]]:
    """Read scraper-owned output health metrics from local CSV output."""
    if SCRAPER_MASTER_OUTPUT is None or not Path(SCRAPER_MASTER_OUTPUT).exists():
        return {"website_found_count": None, "website_found_rate": None}

    website_found = 0
    total_rows = 0
    try:
        with Path(SCRAPER_MASTER_OUTPUT).open("r", encoding="utf-8", newline="") as fh:
            import csv

            reader = csv.DictReader(fh)
            for row in reader:
                total_rows += 1
                if (row.get("website") or "").strip():
                    website_found += 1
    except Exception:
        return {"website_found_count": None, "website_found_rate": None}

    rate = None
    if total_rows > 0:
        rate = round((website_found / total_rows) * 100, 1)
    return {"website_found_count": website_found, "website_found_rate": rate}


def _read_scraper_health() -> Dict[str, Any]:
    """Read scraper-owned current-run health data."""
    if SCRAPER_HEALTH_FILE is None:
        return {
            "started_at": None,
            "ended_at": None,
            "last_activity": None,
            "captcha_count": None,
            "error_count": None,
        }

    data = _read_json(Path(SCRAPER_HEALTH_FILE)) or {}
    return {
        "started_at": data.get("started_at"),
        "ended_at": data.get("ended_at"),
        "last_activity": data.get("last_activity"),
        "captcha_count": data.get("captcha_count"),
        "error_count": data.get("error_count"),
    }


# ============================================================================
# ENRICHMENT WATCHER STATUS
# ============================================================================

def _get_enrichment_status() -> Dict[str, Any]:
    """Read enrichment watcher state from lock file."""
    lock_data = _read_json(ENRICHMENT_LOCK)

    if lock_data is None:
        return {
            "status": "idle",
            "locked": False,
            "pid": None,
            "locked_at": None,
            "age_minutes": None,
            "is_stale": False,
            "reason": None,
        }

    # The watcher lock has {pid, reason, locked_at}
    pid = lock_data.get("pid")
    locked_at = lock_data.get("locked_at", "")
    reason = lock_data.get("reason", "")

    # Check if the enrichment_lock.py shared lock format {locked, batch_id, locked_at}
    is_shared_format = "locked" in lock_data and "batch_id" in lock_data
    if is_shared_format:
        if not lock_data.get("locked"):
            return {
                "status": "idle",
                "locked": False,
                "pid": None,
                "locked_at": None,
                "age_minutes": None,
                "is_stale": False,
                "reason": None,
            }
        locked_at = lock_data.get("locked_at", "")
        reason = lock_data.get("batch_id", "")
        pid = None

    alive = _pid_alive(pid) if isinstance(pid, int) else None
    age = _lock_age_minutes(locked_at) if locked_at else None
    is_stale = (age is not None and age > 90) or (pid is not None and not alive)

    if alive or (is_shared_format and lock_data.get("locked")):
        status = "running"
    elif is_stale:
        status = "stale"
    else:
        status = "idle"

    return {
        "status": status,
        "locked": True,
        "pid": pid,
        "locked_at": locked_at,
        "age_minutes": round(age, 1) if age is not None else None,
        "is_stale": is_stale,
        "reason": reason,
    }


# ============================================================================
# ULTRA VALIDATED WATCHER STATUS
# ============================================================================

def _get_ultra_status() -> Dict[str, Any]:
    """Read ultra validated watcher state from lock file."""
    if not ULTRA_LOCK.exists():
        return {
            "status": "idle",
            "locked": False,
            "locked_at": None,
            "age_minutes": None,
            "is_stale": False,
        }

    try:
        locked_at = ULTRA_LOCK.read_text(encoding="utf-8").strip()
        lock_time = datetime.fromisoformat(locked_at)
    except Exception:
        return {
            "status": "idle",
            "locked": False,
            "locked_at": None,
            "age_minutes": None,
            "is_stale": False,
        }

    age = _lock_age_minutes(locked_at)
    is_stale = age is not None and age > 30

    return {
        "status": "stale" if is_stale else "running",
        "locked": True,
        "locked_at": locked_at,
        "age_minutes": round(age, 1) if age is not None else None,
        "is_stale": is_stale,
    }


# ============================================================================
# SCRAPER V3 WATCHER STATUS
# ============================================================================

def _get_scraper_v3_status() -> Dict[str, Any]:
    """Read scraper_v3 watcher state from lock file."""
    lock_data = _read_json(SCRAPER_V3_LOCK)

    if lock_data is None:
        return {
            "status": "idle",
            "locked": False,
            "pid": None,
            "locked_at": None,
            "age_minutes": None,
            "is_stale": False,
            "reason": None,
        }

    pid = lock_data.get("pid")
    locked_at = lock_data.get("locked_at", "")
    reason = lock_data.get("reason", "")
    alive = _pid_alive(pid) if isinstance(pid, int) else None

    if isinstance(pid, int) and not alive:
        try:
            SCRAPER_V3_LOCK.unlink()
        except OSError:
            pass
        return {
            "status": "idle",
            "locked": False,
            "pid": None,
            "locked_at": None,
            "age_minutes": None,
            "is_stale": False,
            "reason": None,
        }

    age = _lock_age_minutes(locked_at) if locked_at else None
    is_stale = (age is not None and age > 180) or (pid is not None and not alive)

    if alive:
        status = "running"
    elif is_stale:
        status = "stale"
    else:
        status = "idle"

    return {
        "status": status,
        "locked": True,
        "pid": pid,
        "locked_at": locked_at,
        "age_minutes": round(age, 1) if age is not None else None,
        "is_stale": is_stale,
        "reason": reason,
    }


# ============================================================================
# ATTENTION FLAGS
# ============================================================================

def _build_attention_flags(
    process_info: dict,
    scheduler_state: dict,
    scraper_progress: dict,
    enrichment: dict,
    ultra: dict,
    scraper_v3: dict,
) -> List[Dict[str, str]]:
    """Build the system attention list — top issues only."""
    flags: List[Dict[str, str]] = []

    # 1. Enrichment lock stuck
    if enrichment.get("is_stale"):
        age = enrichment.get("age_minutes", "?")
        flags.append({
            "level": "warning",
            "source": "Enrichment",
            "message": f"Lock stuck for {age}m — enricher may have crashed",
        })

    # 2. Ultra lock stuck
    if ultra.get("is_stale"):
        age = ultra.get("age_minutes", "?")
        flags.append({
            "level": "warning",
            "source": "Keywords",
            "message": f"Lock stuck for {age}m — pipeline may have crashed",
        })

    # 3. Scraper V3 lock stuck
    if scraper_v3.get("is_stale"):
        age = scraper_v3.get("age_minutes", "?")
        flags.append({
            "level": "warning",
            "source": "Scraper V3",
            "message": f"Lock stuck for {age}m — scraper_v3 may have crashed",
        })

    # 4. Scraper stopped unexpectedly (process dead but scheduler expected running)
    if (scheduler_state.get("enabled")
            and scheduler_state.get("phase") == "RUNNING"
            and not process_info.get("running")):
        flags.append({
            "level": "danger",
            "source": "Scraper",
            "message": "Process stopped while scheduler expected running",
        })

    # 5. Category complete — needs attention to advance
    if scraper_progress.get("status") == "category_complete":
        cat = scraper_progress.get("progress", {}).get("category", {})
        idx = cat.get("index", 0) + 1
        total = cat.get("total", "?")
        flags.append({
            "level": "info",
            "source": "Scraper",
            "message": f"Category {idx}/{total} complete — advance to next",
        })

    # 6. All scraping done
    if scraper_progress.get("is_complete"):
        flags.append({
            "level": "info",
            "source": "Scraper",
            "message": "All categories complete — scraping finished",
        })

    return flags


# ============================================================================
# PUBLIC API
# ============================================================================

def get_pipeline_status(process_info: dict, scheduler_state: dict, scraper_progress: dict) -> Dict[str, Any]:
    """Build the full pipeline status snapshot.

    Args:
        process_info: from ProcessManager.get_info()
        scheduler_state: from Scheduler.get_state()
        scraper_progress: from get_progress_status()

    Returns:
        Dict with keys: maps_scraper, enrichment, keyword_detection,
        scoring, settlement, scraper_v3, watchers, attention
    """
    enrichment = _get_enrichment_status()
    ultra = _get_ultra_status()
    scraper_v3 = _get_scraper_v3_status()
    live = _get_live_metrics()
    latest_scraper_v3_summary = _read_latest_json(
        SCRAPER_V3_SUMMARY_DIR, "run_summary_*.json"
    ) or {}

    # Derive subsystem states from available data
    is_running = process_info.get("running", False)
    scheduler_phase = scheduler_state.get("phase", "DISABLED")

    # Maps scraper status
    scraper_status = "stopped"
    if is_running:
        scraper_status = "running"
    elif scheduler_phase == "RESTING":
        scraper_status = "resting"

    progress = scraper_progress.get("progress") or {}
    overall = progress.get("overall", {})
    loc = progress.get("current_location", {})
    cat = progress.get("category", {})
    settlement_alpha = live["settlement"].get("alpha")
    settlement_beta = live["settlement"].get("beta")
    settlement_gamma = live["settlement"].get("gamma")
    usable_total = None
    if None not in (settlement_alpha, settlement_beta, settlement_gamma):
        usable_total = settlement_alpha + settlement_beta + settlement_gamma

    scraped_total = overall.get("total_leads_collected", 0) or 0
    completed_searches = overall.get("completed_searches", 0) or 0
    batch_count = overall.get("batch_number", 0) or 0
    leads_per_100_searches = None
    if completed_searches > 0:
        leads_per_100_searches = round((scraped_total / completed_searches) * 100, 1)

    usable_rate = None
    if usable_total is not None and scraped_total > 0:
        usable_rate = round((usable_total / scraped_total) * 100, 1)

    leads_per_batch = None
    if batch_count > 0:
        leads_per_batch = round(scraped_total / batch_count, 1)

    scraper_health = _read_scraper_health()
    runtime_hours = _elapsed_hours_between(
        scraper_health.get("started_at"),
        None if is_running else scraper_health.get("ended_at"),
    )
    searches_per_hour = None
    if runtime_hours is not None and runtime_hours > 0 and completed_searches > 0:
        searches_per_hour = round(completed_searches / runtime_hours, 1)

    scraper_output_health = _read_scraper_output_health()
    if scraped_total <= 0:
        scraper_output_health = {"website_found_count": None, "website_found_rate": None}

    if completed_searches <= 0 and not is_running:
        scraper_health = {
            "started_at": None,
            "ended_at": None,
            "last_activity": progress.get("last_saved"),
            "captcha_count": None,
            "error_count": None,
        }

    recommendation, recommendation_tone = _scraper_recommendation(
        completed_searches,
        batch_count,
        leads_per_100_searches,
        scraper_output_health.get("website_found_rate"),
        int(scraper_health.get("captcha_count") or 0),
        int(scraper_health.get("error_count") or 0),
    )

    maps_scraper = {
        "status": scraper_status,
        "location": loc.get("name"),
        "keyword": loc.get("keyword_name"),
        "scraped_leads": scraped_total,
        "usable_leads": usable_total,
        "usable_rate": usable_rate,
        "leads_per_100_searches": leads_per_100_searches,
        "alpha": settlement_alpha,
        "beta": settlement_beta,
        "gamma": settlement_gamma,
        "searches": completed_searches,
        "total_searches": overall.get("total_searches", 0),
        "batches": batch_count,
        "leads_per_batch": leads_per_batch,
        "searches_per_hour": searches_per_hour,
        "website_found_count": scraper_output_health.get("website_found_count"),
        "website_found_rate": scraper_output_health.get("website_found_rate"),
        "captcha_count": scraper_health.get("captcha_count"),
        "error_count": scraper_health.get("error_count"),
        "category": f"{cat.get('index', 0) + 1}/{cat.get('total', '?')}" if cat else None,
        "worker_count": SCRAPER_NUM_WORKERS,
        "recommendation": recommendation,
        "recommendation_tone": recommendation_tone,
        "last_saved": progress.get("last_saved"),
        "last_activity": scraper_health.get("last_activity") or progress.get("last_saved"),
        "run_started_at": scraper_health.get("started_at"),
        "run_ended_at": scraper_health.get("ended_at"),
    }

    # Enrichment card
    enrichment_card = {
        "status": enrichment["status"],
        "locked": enrichment["locked"],
        "lock_age": enrichment.get("age_minutes"),
        "is_stale": enrichment.get("is_stale", False),
        "pending": live["enrichment"].get("pending"),
        "checked": live["enrichment"].get("checked"),
        "employees": live["enrichment"].get("employees"),
        "found": live["enrichment"].get("found"),
    }

    # Keyword detection — runs via ultra_validated_watcher
    keyword_card = {
        "status": ultra["status"],
        "locked": ultra["locked"],
        "lock_age": ultra.get("age_minutes"),
        "is_stale": ultra.get("is_stale", False),
        "pending": live["keyword_detection"].get("pending"),
        "cat1": live["keyword_detection"].get("cat1"),
        "cat2": live["keyword_detection"].get("cat2"),
    }

    # Scoring — runs immediately after keyword detection (same watcher)
    scoring_card = {
        "status": ultra["status"],  # same pipeline
        "locked": ultra["locked"],
        "ready": live["scoring"].get("ready"),
        "moved": live["scoring"].get("moved"),
        "alpha": live["settlement"].get("alpha"),
        "beta": live["settlement"].get("beta"),
        "gamma": live["settlement"].get("gamma"),
    }

    settlement_card = {
        "status": "idle",
        "alpha": live["settlement"].get("alpha"),
        "beta": live["settlement"].get("beta"),
        "gamma": live["settlement"].get("gamma"),
    }

    scraper_v3_card = {
        "status": scraper_v3["status"],
        "locked": scraper_v3["locked"],
        "lock_age": scraper_v3.get("age_minutes"),
        "is_stale": scraper_v3.get("is_stale", False),
        "reason": scraper_v3.get("reason"),
        "alpha": live["scraper_v3_ready"].get("alpha"),
        "beta": live["scraper_v3_ready"].get("beta"),
        "gamma": live["scraper_v3_ready"].get("gamma"),
        "total": live["scraper_v3_ready"].get("total"),
        "alpha_beta": (
            (live["scraper_v3_ready"].get("alpha") or 0)
            + (live["scraper_v3_ready"].get("beta") or 0)
            if live["scraper_v3_ready"].get("alpha") is not None
            and live["scraper_v3_ready"].get("beta") is not None
            else None
        ),
        "threshold": int(os.environ.get("MIN_ALPHA_BETA_BACKLOG_TO_RUN", "30")),
        "last_finished": latest_scraper_v3_summary.get("finished"),
        "last_batch": latest_scraper_v3_summary.get("batch_size"),
        "last_written": latest_scraper_v3_summary.get("written_ok"),
    }

    # Watchers summary
    watchers = {
        "enrichment_watcher": "active" if enrichment["status"] == "running" else ("stale" if enrichment["is_stale"] else "idle"),
        "ultra_watcher": "active" if ultra["status"] == "running" else ("stale" if ultra["is_stale"] else "idle"),
        "scraper_v3_watcher": "active" if scraper_v3["status"] == "running" else ("stale" if scraper_v3["is_stale"] else "idle"),
    }

    # Scheduler
    scheduler_card = {
        "enabled": scheduler_state.get("enabled", False),
        "phase": scheduler_phase,
        "next_transition": scheduler_state.get("next_transition"),
        "config": scheduler_state.get("config", {}),
    }

    attention = _build_attention_flags(
        process_info, scheduler_state, scraper_progress, enrichment, ultra, scraper_v3
    )

    return {
        "snapshot_fetched_at": live.get("fetched_at"),
        "maps_scraper": maps_scraper,
        "enrichment": enrichment_card,
        "keyword_detection": keyword_card,
        "scoring": scoring_card,
        "settlement": settlement_card,
        "scraper_v3": scraper_v3_card,
        "watchers": watchers,
        "scheduler": scheduler_card,
        "attention": attention,
    }
