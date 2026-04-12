"""Pipeline Status - Unified status collector for all subsystems.

Reads lock files, process state, and progress data to build a single
pipeline snapshot for the overview dashboard. Most reads are local files;
live metrics (enrichment counts, settlement totals) are fetched from
Google Sheets with a longer TTL cache to stay within Sheets read quotas.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import settings
from .sheet_rotation import get_sheet_rotation_snapshot

sys.path.insert(0, str(settings.BOTS_DIR))

# Lock file paths
CONTROLLER_DIR = settings.CONTROLLER_DIR
ENRICHMENT_LOCK = CONTROLLER_DIR / "enrichment_lock.json"
ENRICHMENT_WATCHER_STATE = CONTROLLER_DIR / "enrichment_watcher_state.json"
ENRICHMENT_RUN_STATE = CONTROLLER_DIR / "enrichment_run_state.json"
ULTRA_LOCK = CONTROLLER_DIR / "ultra_validated_lock.json"
SCRAPER_V3_LOCK = CONTROLLER_DIR / "scraper_v3_lock.json"
ENRICHMENT_BATCH_COUNTER = CONTROLLER_DIR / "enrichment_batch_counter.txt"
SCRAPER_V3_SUMMARY_DIR = settings.BOTS_DIR / "enricher" / "outputs" / "summaries"

LIVE_STATUS_TTL_SECONDS = 180
DELIVERY_STATUS_TTL_SECONDS = 180
SYSTEM_CONFIG_LIVE_TTL_SECONDS = 300
_LIVE_STATUS_CACHE: Dict[str, Any] = {"fetched_at": 0.0, "data": None}
_DELIVERY_SHEET_CACHE: Dict[str, Any] = {"fetched_at": 0.0, "data": None}
_SYSTEM_CONFIG_LIVE_CACHE: Dict[str, Any] = {"fetched_at": 0.0, "data": None}
_CACHE_REFRESH_LOCK = threading.Lock()
_CACHE_REFRESH_IN_PROGRESS: Dict[str, bool] = {
    "live_metrics": False,
    "system_config_live": False,
    "delivery_sheet": False,
}

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
    from common.config import SOURCE_WEBHOOK_URL, WEBHOOK_SECRET, OUTPUT_SPREADSHEET_ID, OUTPUT_TAB_NAME
    from common.system_config import get_system_config
    from common.sheets_guard import authorize_gspread_client, guarded_gspread_call
except ImportError:
    GoogleClient = None
    SOURCE_WEBHOOK_URL = None
    WEBHOOK_SECRET = None
    OUTPUT_SPREADSHEET_ID = None
    OUTPUT_TAB_NAME = "Employee_db"
    def get_system_config(*_args, **_kwargs):
        return {}
    def authorize_gspread_client(*_args, **_kwargs):
        raise RuntimeError("Google Sheets auth unavailable")
    def guarded_gspread_call(fn, **_kwargs):
        return fn()

try:
    from maps_scraper.config import (
        SCRAPER_CALLOUTS_FILE,
        SCRAPER_HEALTH_FILE,
        MASTER_OUTPUT as SCRAPER_MASTER_OUTPUT,
        SCRAPER_STATS_FILE,
        get_dashboard_config as get_scraper_dashboard_config,
    )
except ImportError:
    SCRAPER_CALLOUTS_FILE = None
    SCRAPER_HEALTH_FILE = None
    SCRAPER_MASTER_OUTPUT = None
    SCRAPER_STATS_FILE = None
    def get_scraper_dashboard_config():
        return {}


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
    return authorize_gspread_client(
        creds_path=os.path.expanduser(str(CREDENTIALS_PATH)),
        scopes=SCOPES,
    )


def _fetch_scraper_v3_ready_counts() -> Dict[str, Any]:
    _null = {
        "alpha": None, "alpha_high": None, "beta": None, "beta_high": None,
        "gamma": None, "gamma_high": None, "alpha_family": None,
        "beta_family": None, "gamma_family": None, "total": None,
        "_ok": False, "_error": None,
    }
    if SOURCE_WEBHOOK_URL is None or WEBHOOK_SECRET is None:
        err = "SOURCE_WEBHOOK_URL or WEBHOOK_SECRET not configured"
        print(f"[SourceWebhook] Skipped: {err}")
        return {**_null, "_error": err}
    import httpx

    try:
        response = httpx.get(
            SOURCE_WEBHOOK_URL,
            params={"token": WEBHOOK_SECRET},
            timeout=20.0,
            verify=False,
            follow_redirects=True,
        )
        response.raise_for_status()
        rows = (response.json() or {}).get("urls", [])
    except Exception as exc:
        err = str(exc)
        print(f"[SourceWebhook] Fetch failed: {err}")
        return {**_null, "_error": err}

    counts: Dict[str, Any] = {
        "alpha": 0, "alpha_high": 0, "beta": 0, "beta_high": 0,
        "gamma": 0, "gamma_high": 0, "alpha_family": 0,
        "beta_family": 0, "gamma_family": 0, "total": 0,
        "_ok": True, "_error": None,
    }
    for row in rows:
        tab = str(row.get("tab") or "").strip().lower()
        if tab in counts:
            counts[tab] += 1
        if tab in ("alpha", "alpha_high"):
            counts["alpha_family"] += 1
        elif tab in ("beta", "beta_high"):
            counts["beta_family"] += 1
        elif tab in ("gamma", "gamma_high"):
            counts["gamma_family"] += 1
        counts["total"] += 1
    print(
        f"[SourceWebhook] OK: {counts['total']} rows "
        f"(alpha={counts['alpha_family']} beta={counts['beta_family']} gamma={counts['gamma_family']})"
    )
    return counts


def _safe_cell(row: list[str], idx: Optional[int]) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def _quote_sheet_range(sheet_name: str, range_a1: str = "A:A") -> str:
    escaped = str(sheet_name or "").replace("'", "''")
    return f"'{escaped}'!{range_a1}"


def _values_batch_get(book: Any, ranges: List[str], *, major_dimension: Optional[str] = None) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {}
    if major_dimension:
        params["majorDimension"] = major_dimension
    result = book.values_batch_get(ranges, params=params or None)
    if isinstance(result, dict):
        value_ranges = result.get("valueRanges")
        if isinstance(value_ranges, list):
            return [item if isinstance(item, dict) else {} for item in value_ranges]
    return []


def _batch_column_counts(book: Any, tab_names: List[str]) -> Dict[str, int]:
    valid_tabs = [str(tab or "").strip() for tab in tab_names if str(tab or "").strip()]
    if not valid_tabs:
        return {}
    ranges = [_quote_sheet_range(tab, "A:A") for tab in valid_tabs]
    values = guarded_gspread_call(
        lambda: _values_batch_get(book, ranges, major_dimension="COLUMNS"),
        operation="read",
        description="Batch-read worksheet column counts",
    )
    counts: Dict[str, int] = {}
    for tab, payload in zip(valid_tabs, values):
        columns = payload.get("values") if isinstance(payload, dict) else None
        first_column = columns[0] if isinstance(columns, list) and columns else []
        counts[tab] = len(first_column) if isinstance(first_column, list) else 0
    return counts


def _get_sheet_targets_from_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    config = config or (get_system_config(include_live=False) if callable(get_system_config) else {})
    pipeline = config.get("pipeline_sheets") if isinstance(config.get("pipeline_sheets"), dict) else {}
    pipeline_sheets = pipeline.get("sheets") if isinstance(pipeline.get("sheets"), list) else []
    active_key = pipeline.get("active_sheet_key")
    active_pipeline = next((item for item in pipeline_sheets if item.get("key") == active_key), pipeline_sheets[0] if pipeline_sheets else None)
    source_cfg = config.get("source_settlement") if isinstance(config.get("source_settlement"), dict) else {}
    source_sheets = source_cfg.get("sheets") if isinstance(source_cfg.get("sheets"), list) else []
    source_active_key = source_cfg.get("active_sheet_key")
    active_source = next((item for item in source_sheets if item.get("key") == source_active_key), source_sheets[0] if source_sheets else None)
    output_cfg = config.get("output_target") if isinstance(config.get("output_target"), dict) else {}
    output_sheets = output_cfg.get("sheets") if isinstance(output_cfg.get("sheets"), list) else []
    output_active_key = output_cfg.get("active_sheet_key")
    active_output = next((item for item in output_sheets if item.get("key") == output_active_key), output_sheets[0] if output_sheets else None)
    return {
        "pipeline": active_pipeline or {},
        "source_settlement": active_source or {},
        "output_target": active_output or {},
    }


def _empty_live_metrics() -> Dict[str, Any]:
    targets = _get_sheet_targets_from_config()
    pipeline_target = targets.get("pipeline") or {}
    output_target = targets.get("output_target") or {}
    return {
        "fetched_at": None,
        "sheet_rows": {
            "validated": {"sheet_id": pipeline_target.get("sheet_id") or SOURCE_SHEET_ID, "tab_name": pipeline_target.get("validated_tab") or VALIDATED_TAB, "current_rows": None, "data_rows": None},
            "ultra": {"sheet_id": pipeline_target.get("sheet_id") or SOURCE_SHEET_ID, "tab_name": pipeline_target.get("ultra_tab") or ULTRA_TAB, "current_rows": None, "data_rows": None},
            "output": {"sheet_id": output_target.get("sheet_id") or OUTPUT_SPREADSHEET_ID, "tab_name": output_target.get("tab_name") or OUTPUT_TAB_NAME, "current_rows": None, "data_rows": None},
        },
        "enrichment": {"eligible_live": None, "pending": None, "checked": None, "found": None, "person": None, "none": None, "employees": None},
        "keyword_detection": {"pending": None, "cat1": None, "cat2": None, "cat3": None},
        "scoring": {"ready": None, "moved": None},
        "settlement": {
            "alpha": None,
            "alpha_high": None,
            "beta": None,
            "beta_high": None,
            "gamma": None,
            "gamma_high": None,
            "i_gamma": None,
            "pbin": None,
            "hydro": None,
            "hydro_formula": None,
        },
        "scraper_v3_ready": {
            "alpha": None,
            "alpha_high": None,
            "beta": None,
            "beta_high": None,
            "gamma": None,
            "gamma_high": None,
            "alpha_family": None,
            "beta_family": None,
            "gamma_family": None,
            "total": None,
        },
    }


def _managed_entry_map(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    entries = (((config.get("managed_sheets") or {}).get("entries")) if isinstance(config, dict) else None) or []
    return {
        str(entry.get("key") or "").strip().lower(): entry
        for entry in entries
        if isinstance(entry, dict) and str(entry.get("key") or "").strip()
    }


def _rotation_live_status_to_capacity_status(
    *,
    live_status: Dict[str, Any],
    target: Dict[str, Any],
    managed_entry: Optional[Dict[str, Any]] = None,
    tab_name: Optional[str] = None,
) -> Dict[str, Any]:
    managed_entry = managed_entry or {}
    current_rows_raw = live_status.get("current_rows")
    current_rows = int(current_rows_raw or 0) if current_rows_raw is not None else None
    warning_threshold = int((managed_entry.get("warning_row_threshold") or 42000))
    stop_threshold = max(
        warning_threshold,
        int(live_status.get("stop_row_threshold") or managed_entry.get("stop_row_threshold") or 45000),
    )
    phase = str(live_status.get("phase") or "").strip().lower()
    error_text = str(live_status.get("error") or "").strip()

    return {
        "ok": bool(live_status.get("ok")) and not error_text,
        "error": error_text or None,
        "warning": phase in {"warning", "prepare", "blocked"},
        "blocked": phase == "blocked",
        "phase": phase or "unknown",
        "sheet_key": target.get("key"),
        "sheet_label": live_status.get("active_label") or target.get("label"),
        "sheet_id": target.get("sheet_id"),
        "tab_name": tab_name,
        "enabled": bool(managed_entry.get("enabled", True)),
        "current_rows": current_rows,
        "data_rows": max(current_rows - 1, 0) if current_rows is not None else None,
        "pending_rows": 0,
        "projected_rows": current_rows,
        "warning_row_threshold": warning_threshold,
        "stop_row_threshold": stop_threshold,
        "rows_remaining_to_warning": max(warning_threshold - current_rows, 0) if current_rows is not None else None,
        "rows_remaining_to_stop": max(stop_threshold - current_rows, 0) if current_rows is not None else None,
        "percent_used": live_status.get("percent_used"),
    }


def _build_system_config_live_snapshot() -> Dict[str, Any]:
    config = get_system_config(include_live=False) if callable(get_system_config) else {}
    if not isinstance(config, dict):
        return {}

    snapshot = get_sheet_rotation_snapshot()
    registries = (snapshot.get("registries") if isinstance(snapshot, dict) else None) or {}
    targets = _get_sheet_targets_from_config(config)
    managed_map = _managed_entry_map(config)

    def registry_live(registry: str) -> Dict[str, Any]:
        entry = registries.get(registry)
        return ((entry.get("live_status")) if isinstance(entry, dict) else None) or {}

    pipeline_target = targets.get("pipeline") or {}
    pipeline_live = registry_live("pipeline_sheets")
    delivery_status = _rotation_live_status_to_capacity_status(
        live_status=pipeline_live,
        target=pipeline_target,
        managed_entry=managed_map.get("pipeline_inbox"),
        tab_name=pipeline_target.get("inbox_tab"),
    )

    source_target = targets.get("source_settlement") or {}
    source_live = registry_live("source_settlement")
    source_status = _rotation_live_status_to_capacity_status(
        live_status=source_live,
        target=source_target,
        managed_entry=managed_map.get("source_settlement"),
        tab_name=source_live.get("tab_name") or source_target.get("alpha_tab"),
    )

    output_target = targets.get("output_target") or {}
    output_live = registry_live("output_target")
    output_status = _rotation_live_status_to_capacity_status(
        live_status=output_live,
        target=output_target,
        managed_entry=managed_map.get("output"),
        tab_name=output_target.get("tab_name"),
    )

    base_entries = (((config.get("managed_sheets") or {}).get("entries")) if isinstance(config.get("managed_sheets"), dict) else None) or []
    live_entries: List[Dict[str, Any]] = []
    for entry in base_entries:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip().lower()
        if key == "pipeline_inbox":
            live_entries.append({**entry, "status": delivery_status})
        elif key == "source_settlement":
            live_entries.append({**entry, "status": source_status})
        elif key == "output":
            live_entries.append({**entry, "status": output_status})
        else:
            live_entries.append({**entry})

    return {
        **config,
        "delivery_sheets": {
            **((config.get("delivery_sheets") if isinstance(config.get("delivery_sheets"), dict) else {}) or {}),
            "active_status": delivery_status,
        },
        "managed_sheets": {
            **((config.get("managed_sheets") if isinstance(config.get("managed_sheets"), dict) else {}) or {}),
            "entries": live_entries,
        },
    }


def _schedule_cache_refresh(name: str, cache: Dict[str, Any], compute_fn) -> None:
    with _CACHE_REFRESH_LOCK:
        if _CACHE_REFRESH_IN_PROGRESS.get(name):
            return
        _CACHE_REFRESH_IN_PROGRESS[name] = True

    def _runner() -> None:
        try:
            data = compute_fn()
            cache["data"] = data
            cache["fetched_at"] = time.time()
        except Exception:
            pass
        finally:
            with _CACHE_REFRESH_LOCK:
                _CACHE_REFRESH_IN_PROGRESS[name] = False

    threading.Thread(target=_runner, daemon=True).start()


def _compute_live_metrics() -> Dict[str, Any]:
    targets = _get_sheet_targets_from_config()
    pipeline_target = targets.get("pipeline") or {}
    source_settlement = targets.get("source_settlement") or {}
    output_target = targets.get("output_target") or {}
    pipeline_sheet_id = pipeline_target.get("sheet_id") or SOURCE_SHEET_ID
    validated_tab = pipeline_target.get("validated_tab") or VALIDATED_TAB
    ultra_tab = pipeline_target.get("ultra_tab") or ULTRA_TAB
    settlement_sheet_id = source_settlement.get("sheet_id") or DEST_SHEET_ID
    output_sheet_id = output_target.get("sheet_id") or OUTPUT_SPREADSHEET_ID
    output_tab = output_target.get("tab_name") or OUTPUT_TAB_NAME
    client = _auth_client()
    src_book = guarded_gspread_call(
        lambda: client.open_by_key(pipeline_sheet_id),
        operation="read",
        description="Open pipeline spreadsheet",
    )
    validated_ws = guarded_gspread_call(
        lambda: src_book.worksheet(validated_tab),
        operation="read",
        description="Open validated worksheet",
    )
    ultra_ws = guarded_gspread_call(
        lambda: src_book.worksheet(ultra_tab),
        operation="read",
        description="Open ultra worksheet",
    )
    dest_book = guarded_gspread_call(
        lambda: client.open_by_key(settlement_sheet_id),
        operation="read",
        description="Open settlement spreadsheet",
    )
    output_ws = None
    if output_sheet_id:
        try:
            output_book = guarded_gspread_call(
                lambda: client.open_by_key(output_sheet_id),
                operation="read",
                description="Open output spreadsheet",
            )
            output_ws = guarded_gspread_call(
                lambda: output_book.worksheet(output_tab),
                operation="read",
                description="Open output worksheet",
            )
        except Exception:
            output_ws = None

    # LinkedIn enrichment metrics
    validated_values = guarded_gspread_call(
        lambda: validated_ws.get_all_values(),
        operation="read",
        description="Read validated worksheet values",
    )
    v_header = [h.strip().lower() for h in validated_values[0]] if validated_values else []
    v_rows = validated_values[1:]
    v_idx = {h: i for i, h in enumerate(v_header)}

    def vv(row: list[str], name: str) -> str:
        return _safe_cell(row, v_idx.get(name))

    valid_live_statuses = {"Live", "Live " , "LIVE"}
    enrichment_pending = 0
    enrichment_eligible_live = 0
    enrichment_checked = 0
    enrichment_found = 0
    enrichment_person = 0
    enrichment_none = 0
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
        elif status_val == "NONE":
            enrichment_none += 1
        if emp_count_v and emp_link_v and company_id_v:
            enrichment_employees += 1

        if not website or web_status not in valid_live_statuses:
            continue

        enrichment_eligible_live += 1

        has_formula = linkedin_val.startswith("=")
        has_real_li = bool(linkedin_val) and not has_formula and "linkedin.com" in linkedin_val.lower()
        is_none = status_val == "NONE"
        is_person_profile = status_val == "PERSON_PROFILE_ONLY"
        needs_link_lookup = (not has_real_li) and not is_none and not is_person_profile
        if needs_link_lookup:
            enrichment_pending += 1

    # Keyword + scoring metrics
    ultra_values = guarded_gspread_call(
        lambda: ultra_ws.get_all_values(),
        operation="read",
        description="Read ultra worksheet values",
    )
    u_header = [h.strip().lower() for h in ultra_values[0]] if ultra_values else []
    u_rows = ultra_values[1:]
    u_idx = {h: i for i, h in enumerate(u_header)}

    def uv(row: list[str], name: str) -> str:
        return _safe_cell(row, u_idx.get(name))

    keyword_pending = 0
    keyword_cat1 = 0
    keyword_cat2 = 0
    keyword_cat3 = 0
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
        elif kw_status == "cat 3":
            keyword_cat3 += 1

        if kw_status in ("cat 1", "cat 2", "cat 3") and moved != "MOVED":
            scoring_ready += 1
        if moved == "MOVED":
            scoring_moved += 1

    # Settlement counts
    settlement = {}
    settlement_tabs = {
        "alpha": (source_settlement.get("alpha_tab") or "Alpha",),
        "alpha_high": (source_settlement.get("alpha_high_tab") or "Alpha_high",),
        "beta": (source_settlement.get("beta_tab") or "Beta",),
        "beta_high": (source_settlement.get("beta_high_tab") or "Beta_high",),
        "gamma": (source_settlement.get("gamma_tab") or "Gamma",),
        "gamma_high": (source_settlement.get("gamma_high_tab") or "Gamma_high",),
        "i_gamma": (source_settlement.get("i_gamma_tab") or "I-gamma",),
        "pbin": (source_settlement.get("pbin_tab") or "pBin",),
        "hydro": (source_settlement.get("hydro_tab") or "Hydro",),
        "hydro_formula": (source_settlement.get("hydro_formula_tab") or "Hydro_formula", "hydro_formular"),
    }
    settlement_counts: Dict[str, int] = {}
    primary_tabs = [aliases[0] for aliases in settlement_tabs.values() if aliases and aliases[0]]
    try:
        settlement_counts = _batch_column_counts(dest_book, primary_tabs)
    except Exception:
        settlement_counts = {}

    for key, aliases in settlement_tabs.items():
        count = None
        for tab in aliases:
            if tab in settlement_counts:
                count = max(0, settlement_counts[tab] - 1)
                break
        if count is None:
            for tab in aliases:
                try:
                    ws = guarded_gspread_call(
                        lambda tab_name=tab: dest_book.worksheet(tab_name),
                        operation="read",
                        description=f"Open settlement worksheet {tab}",
                    )
                    count = max(
                        0,
                        len(
                            guarded_gspread_call(
                                lambda: ws.col_values(1),
                                operation="read",
                                description=f"Read settlement worksheet {tab} column A",
                            )
                        ) - 1,
                    )
                    break
                except Exception:
                    continue
        settlement[key] = count

    # Scraper V3 ready counts from source webhook
    scraper_v3_ready = _fetch_scraper_v3_ready_counts()
    output_values = (
        guarded_gspread_call(
            lambda: output_ws.get_all_values(),
            operation="read",
            description="Read output worksheet values",
        )
        if output_ws is not None
        else []
    )

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sheet_rows": {
            "validated": {
                "sheet_id": pipeline_sheet_id,
                "tab_name": validated_tab,
                "current_rows": len(validated_values),
                "data_rows": max(len(validated_values) - 1, 0),
            },
            "ultra": {
                "sheet_id": pipeline_sheet_id,
                "tab_name": ultra_tab,
                "current_rows": len(ultra_values),
                "data_rows": max(len(ultra_values) - 1, 0),
            },
            "output": {
                "sheet_id": output_sheet_id,
                "tab_name": output_tab,
                "current_rows": len(output_values),
                "data_rows": max(len(output_values) - 1, 0),
            },
        },
        "enrichment": {
            "eligible_live": enrichment_eligible_live,
            "pending": enrichment_pending,
            "checked": enrichment_checked,
            "found": enrichment_found,
            "person": enrichment_person,
            "none": enrichment_none,
            "employees": enrichment_employees,
        },
        "keyword_detection": {
            "pending": keyword_pending,
            "cat1": keyword_cat1,
            "cat2": keyword_cat2,
            "cat3": keyword_cat3,
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
    if cached is not None and now - fetched_at < DELIVERY_STATUS_TTL_SECONDS:
        return cached
    _schedule_cache_refresh("live_metrics", _LIVE_STATUS_CACHE, _compute_live_metrics)
    return cached or _empty_live_metrics()


def _get_delivery_sheet_status_cached() -> Dict[str, Any]:
    live_config = _get_system_config_live_cached()
    if isinstance(live_config, dict):
        return ((live_config.get("delivery_sheets") or {}).get("active_status") if isinstance(live_config.get("delivery_sheets"), dict) else {}) or {}
    return {}


def _get_system_config_live_cached() -> Dict[str, Any]:
    now = time.time()
    cached = _SYSTEM_CONFIG_LIVE_CACHE.get("data")
    fetched_at = float(_SYSTEM_CONFIG_LIVE_CACHE.get("fetched_at") or 0.0)
    if cached is not None and now - fetched_at < LIVE_STATUS_TTL_SECONDS:
        return cached
    _schedule_cache_refresh(
        "system_config_live",
        _SYSTEM_CONFIG_LIVE_CACHE,
        _build_system_config_live_snapshot,
    )
    return cached or {}


def prewarm_status_caches() -> None:
    _schedule_cache_refresh(
        "system_config_live",
        _SYSTEM_CONFIG_LIVE_CACHE,
        _build_system_config_live_snapshot,
    )
    _schedule_cache_refresh("live_metrics", _LIVE_STATUS_CACHE, _compute_live_metrics)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        try:
            proc = subprocess.run(
                ["ps", "-p", str(pid), "-o", "stat="],
                capture_output=True,
                text=True,
                check=False,
            )
            stat = (proc.stdout or "").strip()
            if stat and "Z" in stat:
                return False
        except Exception:
            pass
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


def _make_sheet_volume_entry(
    key: str,
    label: str,
    tab_name: str,
    current_rows: Optional[int],
    warning_threshold: Optional[int],
    stop_threshold: Optional[int],
    *,
    data_rows: Optional[int] = None,
    state_override: Optional[str] = None,
    note_override: Optional[str] = None,
) -> Dict[str, Any]:
    warning = warning_threshold if isinstance(warning_threshold, int) and warning_threshold > 0 else None
    stop = stop_threshold if isinstance(stop_threshold, int) and stop_threshold > 0 else None
    rows = current_rows if isinstance(current_rows, int) and current_rows >= 0 else None
    pct = None
    if rows is not None and stop:
        pct = round(min((rows / stop) * 100, 100.0), 1)

    state = state_override or "normal"
    note = note_override or ""
    if rows is None:
        state = state_override or "unknown"
        note = note_override or "Volume unavailable"
    elif stop:
        if rows >= stop:
            state = state_override or "danger"
            note = note_override or "Cap reached"
        elif warning and rows >= warning:
            state = state_override or "warning"
            note = note_override or f"{max(stop - rows, 0):,} rows remaining"
        else:
            state = state_override or "normal"
            note = note_override or f"{max(stop - rows, 0):,} rows remaining"

    return {
        "key": key,
        "label": label,
        "tab_name": tab_name,
        "current_rows": rows,
        "data_rows": data_rows,
        "warning_threshold": warning,
        "stop_threshold": stop,
        "progress_pct": pct,
        "state": state,
        "note": note,
    }


def _read_scraper_output_health() -> Dict[str, Optional[float]]:
    """Read scraper-owned output health metrics from local CSV output."""
    if SCRAPER_MASTER_OUTPUT is None or not Path(SCRAPER_MASTER_OUTPUT).exists():
        return {"website_found_count": None, "website_total": None, "website_found_rate": None}

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
        return {"website_found_count": None, "website_total": None, "website_found_rate": None}

    rate = None
    if total_rows > 0:
        rate = round((website_found / total_rows) * 100, 1)
    return {"website_found_count": website_found, "website_total": total_rows, "website_found_rate": rate}


def _read_scraper_all_time_stats() -> Dict[str, Optional[float]]:
    """Read cumulative scraper metrics from persisted stats, with file-based fallback."""
    derived = {
        "total_searches": None,
        "total_leads": None,
        "total_batches": None,
        "total_website_found": None,
        "total_website_total": None,
        "total_captchas": None,
        "total_errors": None,
    }

    output_health = _read_scraper_output_health()
    if output_health.get("website_found_count") is not None:
        derived["total_website_found"] = output_health.get("website_found_count")
        derived["total_website_total"] = output_health.get("website_total")

    if SCRAPER_STATS_FILE is None:
        return derived

    data = _read_json(Path(SCRAPER_STATS_FILE)) or {}
    for key in derived:
        if key in data:
            derived[key] = data.get(key)
    return derived


def _read_scraper_callouts(limit: int = 20) -> List[Dict[str, Any]]:
    """Return the most recent scraper callouts, newest first."""
    if SCRAPER_CALLOUTS_FILE is None:
        return []

    path = Path(SCRAPER_CALLOUTS_FILE)
    if not path.exists():
        return []

    items: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    items.append(data)
    except OSError:
        return []

    return list(reversed(items[-limit:]))


def _read_scraper_health() -> Dict[str, Any]:
    """Read scraper-owned current-run health data."""
    if SCRAPER_HEALTH_FILE is None:
        return {
            "started_at": None,
            "ended_at": None,
            "last_activity": None,
            "current_phase": None,
            "captcha_count": None,
            "error_count": None,
            "run_searches": None,
            "run_leads": None,
            "run_batches": None,
            "run_website_found": None,
            "run_website_total": None,
            "current_batch_number": None,
        }

    data = _read_json(Path(SCRAPER_HEALTH_FILE)) or {}
    return {
        "started_at": data.get("started_at"),
        "ended_at": data.get("ended_at"),
        "last_activity": data.get("last_activity"),
        "current_phase": data.get("current_phase"),
        "captcha_count": data.get("captcha_count"),
        "error_count": data.get("error_count"),
        "run_searches": data.get("run_searches"),
        "run_leads": data.get("run_leads"),
        "run_batches": data.get("run_batches"),
        "run_website_found": data.get("run_website_found"),
        "run_website_total": data.get("run_website_total"),
        "current_batch_number": data.get("current_batch_number"),
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


def _get_enrichment_watcher_state() -> Dict[str, Any]:
    state = _read_json(ENRICHMENT_WATCHER_STATE)
    if not isinstance(state, dict):
        return {}
    return state


def _get_enrichment_run_state() -> Dict[str, Any]:
    state = _read_json(ENRICHMENT_RUN_STATE)
    if not isinstance(state, dict):
        return {}
    pid = state.get("pid")
    active = bool(state.get("active"))
    if active and isinstance(pid, int) and not _pid_alive(pid):
        state["active"] = False
        state["ended_at"] = state.get("ended_at") or datetime.now().isoformat()
        try:
            ENRICHMENT_RUN_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError:
            pass
        return state
    return state


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

def _get_scraper_v3_status(stale_timeout_minutes: int = 180) -> Dict[str, Any]:
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
    is_stale = (age is not None and age > stale_timeout_minutes) or (pid is not None and not alive)

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
    delivery_sheet: dict,
    managed_sheet_statuses: List[dict],
    sheet_rotation: dict,
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

    if delivery_sheet.get("ok"):
        label = delivery_sheet.get("sheet_label") or "Delivery sheet"
        tab_name = delivery_sheet.get("tab_name") or "Leads"
        current_rows = delivery_sheet.get("current_rows")
        if delivery_sheet.get("blocked"):
            flags.append({
                "level": "danger",
                "source": "Pipeline Sheet",
                "message": f"{label} / {tab_name} reached its stop threshold at row {current_rows}. Add and activate a new pipeline sheet before resuming.",
            })
        elif delivery_sheet.get("warning"):
            remaining = delivery_sheet.get("rows_remaining_to_stop")
            flags.append({
                "level": "warning",
                "source": "Pipeline Sheet",
                "message": f"{label} / {tab_name} is approaching its row cap. {remaining} rows remain before the stop threshold.",
            })
    elif delivery_sheet.get("error"):
        flags.append({
            "level": "warning",
            "source": "Pipeline Sheet",
            "message": f"Could not inspect delivery sheet capacity: {delivery_sheet.get('error')}",
        })

    for entry in managed_sheet_statuses:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
        if key == "pipeline_inbox" or not entry.get("enabled"):
            continue
        label = status.get("sheet_label") or entry.get("label") or "Managed sheet"
        tab_name = status.get("tab_name") or entry.get("tab_name") or "--"
        current_rows = status.get("current_rows")
        if status.get("blocked"):
            flags.append({
                "level": "danger",
                "source": label,
                "message": f"{label} / {tab_name} reached its stop threshold at row {current_rows}.",
            })
        elif status.get("warning"):
            remaining = status.get("rows_remaining_to_stop")
            flags.append({
                "level": "warning",
                "source": label,
                "message": f"{label} / {tab_name} is approaching its row cap. {remaining} rows remain before the stop threshold.",
            })
        elif status.get("error"):
            flags.append({
                "level": "warning",
                "source": label,
                "message": f"Could not inspect {label} capacity: {status.get('error')}",
            })

    registries = (sheet_rotation.get("registries") if isinstance(sheet_rotation, dict) else {}) or {}
    for registry_key, registry in registries.items():
        if not isinstance(registry, dict):
            continue
        status = registry.get("live_status") if isinstance(registry.get("live_status"), dict) else {}
        label = status.get("active_label") or registry.get("active_label") or registry_key.replace("_", " ").title()
        tab_name = status.get("tab_name") or "--"
        percent_used = status.get("percent_used")
        phase = status.get("phase")
        pending_label = registry.get("pending_sheet_label")
        deadline = registry.get("approval_deadline_at")
        rotation_status = registry.get("rotation_status")

        if rotation_status == "paused_pending_approval":
            flags.append({
                "level": "danger",
                "source": label,
                "message": f"{label} / {tab_name} is full. System paused pending approval to switch to {pending_label or 'the prepared replacement'}." + (f" Auto-continue at {deadline}." if deadline else ""),
            })
        elif rotation_status == "prepared" and pending_label:
            flags.append({
                "level": "danger",
                "source": label,
                "message": f"{label} / {tab_name} prepared a replacement sheet ({pending_label}) at {percent_used}% capacity.",
            })
        elif phase == "warning" and percent_used is not None:
            flags.append({
                "level": "danger",
                "source": label,
                "message": f"{label} / {tab_name} crossed {percent_used}% capacity. Replacement planning is active.",
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
    enrichment_watcher_state = _get_enrichment_watcher_state()
    enrichment_run_state = _get_enrichment_run_state()
    ultra = _get_ultra_status()
    delivery_sheet = _get_delivery_sheet_status_cached()
    system_config_live = _get_system_config_live_cached()
    managed_sheet_statuses = (((system_config_live.get("managed_sheets") or {}).get("entries")) if isinstance(system_config_live, dict) else None) or []
    sheet_rotation = get_sheet_rotation_snapshot()
    live = _get_live_metrics()
    fresh_scraper_v3_ready = _fetch_scraper_v3_ready_counts()
    if any(value is not None for value in fresh_scraper_v3_ready.values()):
        live = dict(live)
        live["scraper_v3_ready"] = fresh_scraper_v3_ready
    system_config = get_system_config(include_live=False) if callable(get_system_config) else {}
    scraper_v3_cfg = ((((system_config.get("enrichment") or {}).get("scraper_v3") or {}).get("watcher")) if isinstance(system_config, dict) else {}) or {}
    scraper_v3 = _get_scraper_v3_status(int(scraper_v3_cfg.get("stale_lock_timeout_minutes") or 180))
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
    scraper_status_raw = str(scraper_progress.get("status") or "").strip().lower()
    overall = progress.get("overall", {})
    loc = progress.get("current_location", {})
    cat = progress.get("category", {})
    settlement_alpha = live["settlement"].get("alpha")
    settlement_alpha_high = live["settlement"].get("alpha_high")
    if settlement_alpha_high is None:
        settlement_alpha_high = 0
    settlement_beta = live["settlement"].get("beta")
    settlement_beta_high = live["settlement"].get("beta_high")
    if settlement_beta_high is None:
        settlement_beta_high = 0
    settlement_gamma = live["settlement"].get("gamma")
    settlement_gamma_high = live["settlement"].get("gamma_high")
    if settlement_gamma_high is None:
        settlement_gamma_high = 0
    settlement_i_gamma = live["settlement"].get("i_gamma")
    if settlement_i_gamma is None:
        settlement_i_gamma = 0
    settlement_pbin = live["settlement"].get("pbin")
    if settlement_pbin is None:
        settlement_pbin = 0
    settlement_hydro = live["settlement"].get("hydro")
    if settlement_hydro is None:
        settlement_hydro = 0
    settlement_hydro_formula = live["settlement"].get("hydro_formula")
    if settlement_hydro_formula is None:
        settlement_hydro_formula = 0
    usable_total = None
    if None not in (
        settlement_alpha,
        settlement_alpha_high,
        settlement_beta,
        settlement_beta_high,
        settlement_gamma,
        settlement_gamma_high,
    ):
        usable_total = (
            settlement_alpha
            + settlement_alpha_high
            + settlement_beta
            + settlement_beta_high
            + settlement_gamma
            + settlement_gamma_high
        )

    all_time_stats = _read_scraper_all_time_stats()
    scraper_callouts = _read_scraper_callouts()
    current_completed_searches = (overall.get("completed_searches", 0) or 0)
    current_total_searches = (overall.get("total_searches", 0) or 0)

    scraped_total = (
        all_time_stats.get("total_leads")
        if all_time_stats.get("total_leads") is not None
        else (overall.get("total_leads_collected", 0) or 0)
    )
    all_time_completed_searches = (
        all_time_stats.get("total_searches")
        if all_time_stats.get("total_searches") is not None
        else current_completed_searches
    )
    batch_count = (
        all_time_stats.get("total_batches")
        if all_time_stats.get("total_batches") is not None
        else (overall.get("batch_number", 0) or 0)
    )
    leads_per_100_searches = None
    if all_time_completed_searches and all_time_completed_searches > 0 and scraped_total is not None:
        leads_per_100_searches = round((scraped_total / all_time_completed_searches) * 100, 1)

    usable_rate = None
    if usable_total is not None and scraped_total > 0:
        usable_rate = round((usable_total / scraped_total) * 100, 1)

    leads_per_batch = None
    if batch_count > 0:
        leads_per_batch = round(scraped_total / batch_count, 1)

    scraper_health = _read_scraper_health()
    run_searches = scraper_health.get("run_searches")
    run_leads = scraper_health.get("run_leads")
    run_batches = scraper_health.get("run_batches")
    run_website_found = scraper_health.get("run_website_found")
    run_website_total = scraper_health.get("run_website_total")

    runtime_hours = _elapsed_hours_between(
        scraper_health.get("started_at"),
        None if is_running else scraper_health.get("ended_at"),
    )
    searches_per_hour = None
    if runtime_hours is not None and runtime_hours > 0 and run_searches and run_searches > 0:
        searches_per_hour = round(run_searches / runtime_hours, 1)

    scraper_output_health = _read_scraper_output_health()
    if scraped_total <= 0:
        scraper_output_health = {"website_found_count": None, "website_total": None, "website_found_rate": None}

    run_leads_per_100_searches = None
    if run_searches and run_searches > 0 and run_leads is not None:
        run_leads_per_100_searches = round((run_leads / run_searches) * 100, 1)

    run_leads_per_batch = None
    if run_batches and run_batches > 0 and run_leads is not None:
        run_leads_per_batch = round(run_leads / run_batches, 1)

    run_website_found_rate = None
    if run_website_total and run_website_total > 0 and run_website_found is not None:
        run_website_found_rate = round((run_website_found / run_website_total) * 100, 1)

    scraper_runtime_cfg = {}
    scraper_dashboard_cfg = get_scraper_dashboard_config() if callable(get_scraper_dashboard_config) else {}
    if isinstance(scraper_dashboard_cfg, dict):
        scraper_runtime_cfg = scraper_dashboard_cfg.get("runtime") or {}

    category_timeout_minutes = int(scraper_runtime_cfg.get("category_advance_timeout_minutes") or 30)
    category_completed_at = progress.get("last_saved")
    category_auto_progress_deadline = None
    category_auto_progress_overdue = False
    if scraper_status_raw == "category_complete" and category_completed_at:
        try:
            completed_dt = datetime.fromisoformat(str(category_completed_at).replace("Z", "+00:00"))
            if completed_dt.tzinfo is None:
                deadline_dt = completed_dt + timedelta(minutes=category_timeout_minutes)
                category_auto_progress_deadline = deadline_dt.isoformat()
                category_auto_progress_overdue = datetime.now() >= deadline_dt
            else:
                deadline_dt = completed_dt + timedelta(minutes=category_timeout_minutes)
                category_auto_progress_deadline = deadline_dt.isoformat()
                category_auto_progress_overdue = datetime.now(deadline_dt.tzinfo) >= deadline_dt
        except Exception:
            category_auto_progress_deadline = None
            category_auto_progress_overdue = False

    run_error_rate = None
    if run_searches and run_searches > 0 and scraper_health.get("error_count") is not None:
        run_error_rate = round((float(scraper_health.get("error_count") or 0) / run_searches) * 100, 1)

    run_captcha_rate = None
    if run_searches and run_searches > 0 and scraper_health.get("captcha_count") is not None:
        run_captcha_rate = round((float(scraper_health.get("captcha_count") or 0) / run_searches) * 100, 1)

    all_time_error_rate = None
    total_errors = all_time_stats.get("total_errors")
    if all_time_completed_searches and all_time_completed_searches > 0 and total_errors is not None:
        all_time_error_rate = round((float(total_errors) / all_time_completed_searches) * 100, 1)

    all_time_captcha_rate = None
    total_captchas = all_time_stats.get("total_captchas")
    if all_time_completed_searches and all_time_completed_searches > 0 and total_captchas is not None:
        all_time_captcha_rate = round((float(total_captchas) / all_time_completed_searches) * 100, 1)

    if (run_searches or 0) <= 0 and not is_running:
        scraper_health = {
            "started_at": None,
            "ended_at": None,
            "last_activity": progress.get("last_saved"),
            "current_phase": None,
            "captcha_count": None,
            "error_count": None,
            "run_searches": None,
            "run_leads": None,
            "run_batches": None,
            "run_website_found": None,
            "run_website_total": None,
            "current_batch_number": None,
        }
        run_searches = None
        run_leads = None
        run_batches = None
        run_website_found = None
        run_website_total = None
        run_leads_per_100_searches = None
        run_leads_per_batch = None
        run_website_found_rate = None
        run_error_rate = None
        run_captcha_rate = None
        searches_per_hour = None

    if scraper_health.get("current_phase") == "paused_critical":
        recommendation, recommendation_tone = ("Auto-paused for review", "danger")
    else:
        recommendation, recommendation_tone = _scraper_recommendation(
            int(run_searches or 0),
            int(run_batches or 0),
            run_leads_per_100_searches,
            run_website_found_rate,
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
        "alpha_high": settlement_alpha_high,
        "beta": settlement_beta,
        "beta_high": settlement_beta_high,
        "gamma": settlement_gamma,
        "gamma_high": settlement_gamma_high,
        "searches": current_completed_searches,
        "total_searches": current_total_searches,
        "all_time_searches": all_time_completed_searches,
        "batches": batch_count,
        "leads_per_batch": leads_per_batch,
        "searches_per_hour": searches_per_hour,
        "website_found_count": scraper_output_health.get("website_found_count"),
        "website_found_rate": scraper_output_health.get("website_found_rate"),
        "captcha_count": scraper_health.get("captcha_count"),
        "error_count": scraper_health.get("error_count"),
        "category": f"{cat.get('index', 0) + 1}/{cat.get('total', '?')}" if cat else None,
        "worker_count": scraper_runtime_cfg.get("num_workers"),
        "batch_size": scraper_runtime_cfg.get("batch_size"),
        "website_status_workers": scraper_runtime_cfg.get("website_status_workers"),
        "recommendation": recommendation,
        "recommendation_tone": recommendation_tone,
        "delivery_sheet": delivery_sheet,
        "last_saved": progress.get("last_saved"),
        "last_activity": scraper_health.get("last_activity") or progress.get("last_saved"),
        "run_started_at": scraper_health.get("started_at"),
        "run_ended_at": scraper_health.get("ended_at"),
        "current_phase": scraper_health.get("current_phase"),
        "current_batch_number": scraper_health.get("current_batch_number"),
        "category_progression": {
            "awaiting_approval": scraper_status_raw == "category_complete" and not is_running,
            "state": "awaiting_approval" if scraper_status_raw == "category_complete" and not is_running else (
                "all_complete" if scraper_progress.get("is_complete") else "idle"
            ),
            "current_category_index": cat.get("index"),
            "current_category_number": (cat.get("index", 0) + 1) if cat.get("index") is not None else None,
            "next_category_number": (cat.get("index", 0) + 2) if cat.get("index") is not None and not scraper_progress.get("is_complete") else None,
            "timeout_minutes": category_timeout_minutes,
            "completed_at": category_completed_at,
            "auto_progress_deadline": category_auto_progress_deadline,
            "auto_progress_overdue": category_auto_progress_overdue,
        },
        "run": {
            "searches": run_searches,
            "leads": run_leads,
            "batches": run_batches,
            "leads_per_100_searches": run_leads_per_100_searches,
            "leads_per_batch": run_leads_per_batch,
            "website_found": run_website_found,
            "website_total": run_website_total,
            "website_found_rate": run_website_found_rate,
            "captcha_count": scraper_health.get("captcha_count"),
            "error_count": scraper_health.get("error_count"),
            "captcha_rate": run_captcha_rate,
            "error_rate": run_error_rate,
            "searches_per_hour": searches_per_hour,
        },
        "all_time": {
            "searches": all_time_completed_searches,
            "leads": scraped_total,
            "batches": batch_count,
            "leads_per_100_searches": leads_per_100_searches,
            "leads_per_batch": leads_per_batch,
            "website_found": scraper_output_health.get("website_found_count"),
            "website_total": scraper_output_health.get("website_total"),
            "website_found_rate": scraper_output_health.get("website_found_rate"),
            "captcha_count": total_captchas,
            "error_count": total_errors,
            "captcha_rate": all_time_captcha_rate,
            "error_rate": all_time_error_rate,
        },
        "recent_callouts": scraper_callouts,
    }

    # Enrichment card
    enrichment_live = live["enrichment"]
    enrichment_eligible_live = enrichment_live.get("eligible_live")
    enrichment_checked = enrichment_live.get("checked")
    enrichment_found = enrichment_live.get("found")
    enrichment_person = enrichment_live.get("person")
    enrichment_none = enrichment_live.get("none")
    enrichment_employees = enrichment_live.get("employees")
    enrichment_pending = enrichment_live.get("pending")
    enrichment_watcher_ready = enrichment_watcher_state.get("ready_count")
    enrichment_run_active = bool(enrichment_run_state.get("active"))
    enrichment_run_current_pending = enrichment_run_state.get("current_pending")

    enrichment_pending_display = enrichment_pending
    if enrichment_run_active and enrichment_run_current_pending is not None:
        enrichment_pending_display = enrichment_run_current_pending
    elif enrichment_watcher_ready is not None:
        if enrichment_pending_display in (None, 0):
            enrichment_pending_display = enrichment_watcher_ready
        else:
            try:
                enrichment_pending_display = max(
                    int(enrichment_pending_display),
                    int(enrichment_watcher_ready),
                )
            except (TypeError, ValueError):
                enrichment_pending_display = enrichment_watcher_ready

    enrichment_coverage_rate = None
    if enrichment_eligible_live and enrichment_eligible_live > 0 and enrichment_checked is not None:
        enrichment_coverage_rate = round((enrichment_checked / enrichment_eligible_live) * 100, 1)

    enrichment_employee_completion_rate = None
    if enrichment_found and enrichment_found > 0 and enrichment_employees is not None:
        enrichment_employee_completion_rate = round((enrichment_employees / enrichment_found) * 100, 1)

    enrichment_card = {
        "status": enrichment["status"],
        "locked": enrichment["locked"],
        "pid": enrichment.get("pid"),
        "reason": enrichment.get("reason"),
        "lock_age": enrichment.get("age_minutes"),
        "is_stale": enrichment.get("is_stale", False),
        "eligible_live": enrichment_eligible_live,
        "pending": enrichment_pending_display,
        "pending_live": enrichment_pending,
        "ready_count": enrichment_watcher_ready,
        "checked": enrichment_checked,
        "employees": enrichment_employees,
        "found": enrichment_found,
        "person": enrichment_person,
        "none": enrichment_none,
        "coverage_rate": enrichment_coverage_rate,
        "employee_completion_rate": enrichment_employee_completion_rate,
        "trigger_threshold": enrichment_watcher_state.get("min_backlog_to_drain"),
        "max_wait_minutes": enrichment_watcher_state.get("max_wait_before_forced_drain_minutes"),
        "watcher_interval_minutes": enrichment_watcher_state.get("watcher_interval_minutes"),
        "waiting_minutes": enrichment_watcher_state.get("waiting_minutes"),
        "wait_progress_pct": enrichment_watcher_state.get("wait_progress_pct"),
        "backlog_since": enrichment_watcher_state.get("backlog_since"),
        "last_tick_at": enrichment_watcher_state.get("last_tick_at"),
        "last_launch_at": enrichment_watcher_state.get("last_launch_at"),
        "last_trigger": enrichment_watcher_state.get("last_trigger"),
        "trigger_state": enrichment_watcher_state.get("trigger_state"),
        "run_active": enrichment_run_active,
        "run_mode": enrichment_run_state.get("mode"),
        "run_started_at": enrichment_run_state.get("started_at"),
        "run_ended_at": enrichment_run_state.get("ended_at"),
        "run_initial_pending": enrichment_run_state.get("initial_pending"),
        "run_processed_rows": enrichment_run_state.get("processed_rows_completed"),
        "run_remaining_estimate": enrichment_run_state.get("remaining_estimate"),
        "run_current_pending": enrichment_run_current_pending,
        "run_batches_completed": enrichment_run_state.get("batches_completed"),
        "run_current_batch_total": enrichment_run_state.get("current_batch_total"),
        "run_current_batch_processed": enrichment_run_state.get("current_batch_processed"),
        "run_current_batch_started_at": enrichment_run_state.get("current_batch_started_at"),
        "run_last_flush_at": enrichment_run_state.get("last_flush_at"),
        "run_last_row_num": enrichment_run_state.get("last_row_num"),
        "run_next_pointer": enrichment_run_state.get("next_pointer"),
        "run_last_error": enrichment_run_state.get("last_error"),
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
        "cat3": live["keyword_detection"].get("cat3"),
        "threshold": (
            (((system_config.get("enrichment") or {}).get("keyword_scoring") or {}).get("watcher") or {}).get("trigger_threshold")
            or 50
        ),
    }

    # Scoring — runs immediately after keyword detection (same watcher)
    scoring_card = {
        "status": ultra["status"],  # same pipeline
        "locked": ultra["locked"],
        "ready": live["scoring"].get("ready"),
        "moved": live["scoring"].get("moved"),
        "alpha": live["settlement"].get("alpha"),
        "alpha_high": settlement_alpha_high,
        "beta": live["settlement"].get("beta"),
        "beta_high": settlement_beta_high,
        "gamma": live["settlement"].get("gamma"),
        "gamma_high": settlement_gamma_high,
        "i_gamma": settlement_i_gamma,
        "pbin": settlement_pbin,
        "hydro": settlement_hydro,
        "hydro_formula": settlement_hydro_formula,
    }

    settlement_card = {
        "status": "idle",
        "alpha": live["settlement"].get("alpha"),
        "alpha_high": settlement_alpha_high,
        "beta": live["settlement"].get("beta"),
        "beta_high": settlement_beta_high,
        "gamma": live["settlement"].get("gamma"),
        "gamma_high": settlement_gamma_high,
        "i_gamma": settlement_i_gamma,
        "pbin": settlement_pbin,
        "hydro": settlement_hydro,
        "hydro_formula": settlement_hydro_formula,
    }

    scraper_v3_card = {
        "status": scraper_v3["status"],
        "locked": scraper_v3["locked"],
        "lock_age": scraper_v3.get("age_minutes"),
        "is_stale": scraper_v3.get("is_stale", False),
        "reason": scraper_v3.get("reason"),
        "alpha": live["scraper_v3_ready"].get("alpha"),
        "alpha_high": live["scraper_v3_ready"].get("alpha_high"),
        "beta": live["scraper_v3_ready"].get("beta"),
        "beta_high": live["scraper_v3_ready"].get("beta_high"),
        "gamma": live["scraper_v3_ready"].get("gamma"),
        "gamma_high": live["scraper_v3_ready"].get("gamma_high"),
        "alpha_family": live["scraper_v3_ready"].get("alpha_family"),
        "beta_family": live["scraper_v3_ready"].get("beta_family"),
        "gamma_family": live["scraper_v3_ready"].get("gamma_family"),
        "total": live["scraper_v3_ready"].get("total"),
        "alpha_beta": (
            (live["scraper_v3_ready"].get("alpha_family") or 0)
            + (live["scraper_v3_ready"].get("beta_family") or 0)
            if live["scraper_v3_ready"].get("alpha_family") is not None
            and live["scraper_v3_ready"].get("beta_family") is not None
            else None
        ),
        "threshold": int(scraper_v3_cfg.get("alpha_beta_trigger_threshold") or os.environ.get("MIN_ALPHA_BETA_BACKLOG_TO_RUN", "30")),
        "max_wait_minutes": int(scraper_v3_cfg.get("max_wait_before_forced_run_minutes") or os.environ.get("SCRAPER_V3_MAX_WAIT_MINUTES", "60")),
        "watcher_interval_minutes": int(scraper_v3_cfg.get("watcher_interval_minutes") or os.environ.get("WATCHER_INTERVAL_MINUTES", "10")),
        "stale_lock_timeout_minutes": int(scraper_v3_cfg.get("stale_lock_timeout_minutes") or 180),
        "last_finished": latest_scraper_v3_summary.get("finished"),
        "last_batch": latest_scraper_v3_summary.get("batch_size"),
        "last_written": latest_scraper_v3_summary.get("written_ok"),
        "last_started": latest_scraper_v3_summary.get("started"),
        "last_elapsed_seconds": latest_scraper_v3_summary.get("elapsed_seconds"),
        "last_per_site_seconds": latest_scraper_v3_summary.get("per_site_seconds"),
        "last_attempted": latest_scraper_v3_summary.get("attempted"),
        "last_resumed_mark_only": latest_scraper_v3_summary.get("resumed_mark_only"),
        "last_scraped_ok": latest_scraper_v3_summary.get("scraped_ok"),
        "source_webhook_ok": fresh_scraper_v3_ready.get("_ok"),
        "source_webhook_error": fresh_scraper_v3_ready.get("_error"),
        "last_scraped_error": latest_scraper_v3_summary.get("scraped_error"),
        "last_write_failed": latest_scraper_v3_summary.get("write_failed"),
        "last_marked_done": latest_scraper_v3_summary.get("marked_done"),
        "last_marked_error": latest_scraper_v3_summary.get("marked_error"),
        "last_mark_failed": latest_scraper_v3_summary.get("mark_failed"),
        "last_graceful_shutdown": latest_scraper_v3_summary.get("graceful_shutdown"),
        "last_timeout_count": latest_scraper_v3_summary.get("timeout_count"),
        "last_blocked_count": latest_scraper_v3_summary.get("blocked_count"),
        "last_linkedin_urls": latest_scraper_v3_summary.get("linkedin_urls"),
        "last_emails": latest_scraper_v3_summary.get("emails"),
        "last_persons_extracted": latest_scraper_v3_summary.get("persons_extracted"),
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
        process_info, scheduler_state, scraper_progress, enrichment, ultra, scraper_v3, delivery_sheet, managed_sheet_statuses, sheet_rotation
    )

    sheet_volume = []
    for entry in managed_sheet_statuses:
        if not isinstance(entry, dict):
            continue
        status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
        sheet_volume.append(
            _make_sheet_volume_entry(
                entry.get("key") or "managed_sheet",
                status.get("sheet_label") or entry.get("label") or "Managed Sheet",
                status.get("tab_name") or entry.get("tab_name") or "--",
                status.get("current_rows"),
                status.get("warning_row_threshold") or entry.get("warning_row_threshold"),
                status.get("stop_row_threshold") or entry.get("stop_row_threshold"),
                data_rows=status.get("data_rows"),
                state_override=(
                    "danger" if status.get("blocked")
                    else "warning" if status.get("warning")
                    else "unknown" if status.get("error")
                    else None
                ),
                note_override=(
                    f"{status.get('rows_remaining_to_stop'):,} rows remaining"
                    if status.get("ok") and status.get("rows_remaining_to_stop") is not None and not status.get("blocked")
                    else "Cap reached"
                    if status.get("blocked")
                    else "Volume unavailable"
                    if status.get("error")
                    else None
                ),
            )
        )

    return {
        "snapshot_fetched_at": live.get("fetched_at"),
        "maps_scraper": maps_scraper,
        "enrichment": enrichment_card,
        "keyword_detection": keyword_card,
        "scoring": scoring_card,
        "settlement": settlement_card,
        "scraper_v3": scraper_v3_card,
        "sheet_volume": sheet_volume,
        "sheet_rotation": sheet_rotation,
        "watchers": watchers,
        "scheduler": scheduler_card,
        "attention": attention,
    }
