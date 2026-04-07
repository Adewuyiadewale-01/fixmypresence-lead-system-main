"""Shared system config for delivery sheets and watcher policies."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_COMMON_DIR = Path(__file__).resolve().parent
_BOTS_DIR = _COMMON_DIR.parent
if str(_BOTS_DIR) not in sys.path:
    sys.path.insert(0, str(_BOTS_DIR))

from common.config import (
    OUTPUT_SPREADSHEET_ID,
    OUTPUT_TAB_NAME,
    SOURCE_WEBHOOK_URL,
    WEBHOOK_SPREADSHEET_ID,
    WEBHOOK_TAB_NAME,
)

DEFAULT_VALIDATED_TAB = "Validated_leads"
DEFAULT_ULTRA_TAB = "Ultra_validated"
DEFAULT_CONFIG_TAB = "config"
DEFAULT_INBOX_QUOTA = 5
DEFAULT_ENRICH_QUOTA = 5
DEFAULT_HYDRO_SHEET_ID = "1YF8WvLTPu-Raf22rHaaphauj-w-n64CRu83ZeAtHYAc"
DEFAULT_HYDRO_FORMULA_TAB = "Hydro_formula"
DEFAULT_HYDRO_INCOMPLETE_TAB = "Hydro_incomplete"
DEFAULT_ENRICHMENT_BATCH_SIZE = 25
DEFAULT_ENRICHMENT_TIMEOUT_SECONDS = 12
DEFAULT_ENRICHMENT_WEBSITE_WORKERS = 2
DEFAULT_ENRICHMENT_LINKEDIN_WORKERS = 1
DEFAULT_ENRICHMENT_FLUSH_INTERVAL_SECONDS = 10.0
DEFAULT_ENRICHMENT_FLUSH_BATCH = 5
DEFAULT_ENRICHMENT_LINKEDIN_DELAY_MIN_SECONDS = 2.0
DEFAULT_ENRICHMENT_LINKEDIN_DELAY_MAX_SECONDS = 6.0
DEFAULT_ENRICHMENT_MAX_BROWSERS = 3
DEFAULT_ENRICHMENT_DRAIN_THRESHOLD = 200
DEFAULT_ENRICHMENT_MAX_WAIT_MINUTES = 60
DEFAULT_ENRICHMENT_WATCHER_INTERVAL_MINUTES = 10
DEFAULT_KEYWORD_BATCH_SIZE = 25
DEFAULT_KEYWORD_REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_KEYWORD_WATCHER_TRIGGER_THRESHOLD = 50
DEFAULT_KEYWORD_WATCHER_INTERVAL_MINUTES = 10
DEFAULT_KEYWORD_WATCHER_LOCK_TIMEOUT_MINUTES = 30
DEFAULT_SCORING_BATCH_SIZE = 50
DEFAULT_CAT3_MIN_MATCHES = 2
DEFAULT_SCRAPER_V3_TRIGGER_THRESHOLD = 30
DEFAULT_SCRAPER_V3_MAX_WAIT_MINUTES = 60
DEFAULT_SCRAPER_V3_WATCHER_INTERVAL_MINUTES = 10
DEFAULT_SCRAPER_V3_STALE_LOCK_TIMEOUT_MINUTES = 180
DEFAULT_SCRAPER_V3_TIMEOUT_SECONDS = 20
DEFAULT_SCRAPER_V3_CONCURRENT_SITES = 3
DEFAULT_SCRAPER_V3_PLAYWRIGHT_LIMIT = 6
DEFAULT_SCRAPER_V3_SUBPAGE_LIMIT = 40
DEFAULT_SCRAPER_V3_LEVEL2_LIMIT = 30
DEFAULT_SCRAPER_V3_EVIDENCE_PROBE_LIMIT = 50
DEFAULT_SCRAPER_V3_PLAYWRIGHT_CONCURRENCY = 3
DEFAULT_SCRAPER_V3_OUTPUT_WRITE_CHUNK_SIZE = 3
DEFAULT_KEYWORD_RELEVANCE_KEYWORDS = [
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
    "hypotheek",
    "hypotheken",
    "rente",
    "rentetarief",
    "rentepercentage",
    "financieel expert",
    "financiele expert",
    "financieel adviseur",
    "financieel planner",
    "strategie",
    "financiele planning",
    "estate planning",
]
DEFAULT_KEYWORD_QUALIFIER_KEYWORDS = [
    "trading strategies",
    "trading strategy",
    "currency pairs",
    "forex market",
    "forex pairs",
    "factsheet",
    "fact sheet",
    "portfolio factsheet",
    "market commentary",
    "investment outlook",
]

SYSTEM_CONFIG_FILE = Path(__file__).with_name("system_config.json")


def _extract_drive_folder_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"/folders/([a-zA-Z0-9-_]+)", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9-_]+", text):
        return text
    return ""


def _default_config() -> Dict[str, Any]:
    return {
        "watchers": {
            "delivery_sheet_capacity": {
                "enabled": True,
                "warning_row_threshold": 42000,
                "stop_row_threshold": 45000,
            }
        },
        "pipeline_sheets": {
            "active_sheet_key": "primary",
            "sheets": [
                {
                    "key": "primary",
                    "label": "Primary Pipeline",
                    "sheet_ref": WEBHOOK_SPREADSHEET_ID,
                    "sheet_id": WEBHOOK_SPREADSHEET_ID,
                    "inbox_tab": WEBHOOK_TAB_NAME,
                    "validated_tab": DEFAULT_VALIDATED_TAB,
                    "ultra_tab": DEFAULT_ULTRA_TAB,
                    "config_tab": DEFAULT_CONFIG_TAB,
                    "inbox_quota": DEFAULT_INBOX_QUOTA,
                    "enrich_quota": DEFAULT_ENRICH_QUOTA,
                    "hydro_sheet_id": DEFAULT_HYDRO_SHEET_ID,
                    "hydro_formula_tab": DEFAULT_HYDRO_FORMULA_TAB,
                    "hydro_incomplete_tab": DEFAULT_HYDRO_INCOMPLETE_TAB,
                    "enabled": True,
                }
            ],
        },
        "source_settlement": {
            "active_sheet_key": "primary_source",
            "sheets": [
                {
                    "key": "primary_source",
                    "label": "Primary Source Settlement",
                    "sheet_ref": DEFAULT_HYDRO_SHEET_ID,
                    "sheet_id": DEFAULT_HYDRO_SHEET_ID,
                    "config_tab": "config",
                    "alpha_tab": "Alpha",
                    "alpha_high_tab": "Alpha_high",
                    "beta_tab": "Beta",
                    "beta_high_tab": "Beta_high",
                    "gamma_tab": "Gamma",
                    "gamma_high_tab": "Gamma_high",
                    "i_gamma_tab": "I-gamma",
                    "pbin_tab": "pBin",
                    "hydro_tab": "Hydro",
                    "hydro_formula_tab": "Hydro_formula",
                    "source_webhook_url": SOURCE_WEBHOOK_URL,
                    "enabled": True,
                }
            ],
        },
        "output_target": {
            "active_sheet_key": "primary_output",
            "sheets": [
                {
                    "key": "primary_output",
                    "label": "Primary Output",
                    "sheet_ref": OUTPUT_SPREADSHEET_ID,
                    "sheet_id": OUTPUT_SPREADSHEET_ID,
                    "tab_name": OUTPUT_TAB_NAME,
                    "enabled": True,
                }
            ],
        },
        "managed_sheets": {
            "entries": [
                {
                    "key": "pipeline_inbox",
                    "label": "Primary Pipeline",
                    "mode": "active_pipeline_inbox",
                    "enabled": True,
                    "warning_row_threshold": 42000,
                    "stop_row_threshold": 45000,
                },
                {
                    "key": "validated",
                    "label": "Enrichment Sheet",
                    "mode": "active_pipeline_validated",
                    "enabled": True,
                    "warning_row_threshold": 42000,
                    "stop_row_threshold": 45000,
                },
                {
                    "key": "ultra",
                    "label": "Ultra Queue",
                    "mode": "active_pipeline_ultra",
                    "enabled": True,
                    "warning_row_threshold": 42000,
                    "stop_row_threshold": 45000,
                },
                {
                    "key": "source_settlement",
                    "label": "Source Settlement Sheet",
                    "mode": "active_source_settlement",
                    "enabled": True,
                    "warning_row_threshold": 42000,
                    "stop_row_threshold": 45000,
                },
                {
                    "key": "output",
                    "label": "Final Output Sheet",
                    "mode": "active_output_sheet",
                    "enabled": True,
                    "warning_row_threshold": 42000,
                    "stop_row_threshold": 45000,
                },
            ],
        },
        "sheet_rotation": {
            "copy_folder_id": _extract_drive_folder_id(os.environ.get("ROTATION_COPY_FOLDER_ID", "")),
        },
        "enrichment": {
            "linkedin": {
                "runtime": {
                    "batch_size": DEFAULT_ENRICHMENT_BATCH_SIZE,
                    "request_timeout_seconds": DEFAULT_ENRICHMENT_TIMEOUT_SECONDS,
                    "website_workers": DEFAULT_ENRICHMENT_WEBSITE_WORKERS,
                    "linkedin_workers": DEFAULT_ENRICHMENT_LINKEDIN_WORKERS,
                    "writer_flush_interval_seconds": DEFAULT_ENRICHMENT_FLUSH_INTERVAL_SECONDS,
                    "writer_flush_batch": DEFAULT_ENRICHMENT_FLUSH_BATCH,
                    "linkedin_delay_min_seconds": DEFAULT_ENRICHMENT_LINKEDIN_DELAY_MIN_SECONDS,
                    "linkedin_delay_max_seconds": DEFAULT_ENRICHMENT_LINKEDIN_DELAY_MAX_SECONDS,
                    "max_browsers": DEFAULT_ENRICHMENT_MAX_BROWSERS,
                },
                "watcher": {
                    "min_backlog_to_drain": DEFAULT_ENRICHMENT_DRAIN_THRESHOLD,
                    "max_wait_before_forced_drain_minutes": DEFAULT_ENRICHMENT_MAX_WAIT_MINUTES,
                    "watcher_interval_minutes": DEFAULT_ENRICHMENT_WATCHER_INTERVAL_MINUTES,
                },
            },
            "keyword_scoring": {
                "keyword_detector": {
                    "batch_size": DEFAULT_KEYWORD_BATCH_SIZE,
                    "request_timeout_seconds": DEFAULT_KEYWORD_REQUEST_TIMEOUT_SECONDS,
                    "relevance_keywords": DEFAULT_KEYWORD_RELEVANCE_KEYWORDS,
                    "qualifier_keywords": DEFAULT_KEYWORD_QUALIFIER_KEYWORDS,
                    "qualifier_min_matches": DEFAULT_CAT3_MIN_MATCHES,
                },
                "watcher": {
                    "trigger_threshold": DEFAULT_KEYWORD_WATCHER_TRIGGER_THRESHOLD,
                    "watcher_interval_minutes": DEFAULT_KEYWORD_WATCHER_INTERVAL_MINUTES,
                    "lock_timeout_minutes": DEFAULT_KEYWORD_WATCHER_LOCK_TIMEOUT_MINUTES,
                },
                "scoring": {
                    "batch_size": DEFAULT_SCORING_BATCH_SIZE,
                },
            },
            "scraper_v3": {
                "runtime": {
                    "site_timeout_seconds": DEFAULT_SCRAPER_V3_TIMEOUT_SECONDS,
                    "concurrent_sites": DEFAULT_SCRAPER_V3_CONCURRENT_SITES,
                    "playwright_limit": DEFAULT_SCRAPER_V3_PLAYWRIGHT_LIMIT,
                    "subpage_limit": DEFAULT_SCRAPER_V3_SUBPAGE_LIMIT,
                    "level2_limit": DEFAULT_SCRAPER_V3_LEVEL2_LIMIT,
                    "evidence_probe_limit": DEFAULT_SCRAPER_V3_EVIDENCE_PROBE_LIMIT,
                    "playwright_concurrency": DEFAULT_SCRAPER_V3_PLAYWRIGHT_CONCURRENCY,
                    "output_write_chunk_size": DEFAULT_SCRAPER_V3_OUTPUT_WRITE_CHUNK_SIZE,
                },
                "watcher": {
                    "alpha_beta_trigger_threshold": DEFAULT_SCRAPER_V3_TRIGGER_THRESHOLD,
                    "max_wait_before_forced_run_minutes": DEFAULT_SCRAPER_V3_MAX_WAIT_MINUTES,
                    "watcher_interval_minutes": DEFAULT_SCRAPER_V3_WATCHER_INTERVAL_MINUTES,
                    "stale_lock_timeout_minutes": DEFAULT_SCRAPER_V3_STALE_LOCK_TIMEOUT_MINUTES,
                },
            },
        },
    }


def _read_raw() -> Dict[str, Any]:
    if not SYSTEM_CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(SYSTEM_CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_raw(data: Dict[str, Any]) -> None:
    tmp = SYSTEM_CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SYSTEM_CONFIG_FILE)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _as_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _as_float(value: Any, default: float, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _extract_sheet_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9-_]+", text):
        return text
    return ""


def _as_str_list(value: Any, default: List[str]) -> List[str]:
    if not isinstance(value, list):
        return list(default)
    cleaned = []
    for item in value:
        text = str(item or "").strip()
        if text:
            cleaned.append(text)
    return cleaned or list(default)


def _clean_delivery_sheet(item: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    label = str(item.get("label") or f"Pipeline Sheet {index + 1}").strip()
    sheet_ref = str(item.get("sheet_ref") or item.get("sheet_id") or "").strip()
    sheet_id = _extract_sheet_id(sheet_ref)
    inbox_tab = str(item.get("inbox_tab") or item.get("tab_name") or WEBHOOK_TAB_NAME).strip()
    validated_tab = str(item.get("validated_tab") or DEFAULT_VALIDATED_TAB).strip()
    ultra_tab = str(item.get("ultra_tab") or DEFAULT_ULTRA_TAB).strip()
    config_tab = str(item.get("config_tab") or DEFAULT_CONFIG_TAB).strip()
    key = str(item.get("key") or f"sheet_{index + 1}").strip().lower().replace(" ", "_")
    enabled = _as_bool(item.get("enabled"), True)
    if not sheet_id or not inbox_tab or not validated_tab or not ultra_tab or not config_tab:
        return None
    return {
        "key": key,
        "label": label or key,
        "sheet_ref": sheet_ref or sheet_id,
        "sheet_id": sheet_id,
        "tab_name": inbox_tab,
        "inbox_tab": inbox_tab,
        "validated_tab": validated_tab,
        "ultra_tab": ultra_tab,
        "config_tab": config_tab,
        "inbox_quota": _as_int(item.get("inbox_quota"), DEFAULT_INBOX_QUOTA, minimum=1),
        "enrich_quota": _as_int(item.get("enrich_quota"), DEFAULT_ENRICH_QUOTA, minimum=1),
        "hydro_sheet_id": _extract_sheet_id(item.get("hydro_sheet_id") or DEFAULT_HYDRO_SHEET_ID),
        "hydro_formula_tab": str(item.get("hydro_formula_tab") or DEFAULT_HYDRO_FORMULA_TAB).strip(),
        "hydro_incomplete_tab": str(item.get("hydro_incomplete_tab") or DEFAULT_HYDRO_INCOMPLETE_TAB).strip(),
        "enabled": enabled,
    }


def _clean_watchers(value: Dict[str, Any] | None) -> Dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    delivery = data.get("delivery_sheet_capacity") if isinstance(data.get("delivery_sheet_capacity"), dict) else {}
    warning = _as_int(delivery.get("warning_row_threshold"), 42000, minimum=1)
    stop = max(warning, _as_int(delivery.get("stop_row_threshold"), 45000, minimum=1))
    return {
        "delivery_sheet_capacity": {
            "enabled": _as_bool(delivery.get("enabled"), True),
            "warning_row_threshold": warning,
            "stop_row_threshold": stop,
        }
    }


def _clean_delivery_sheets(value: Dict[str, Any] | None) -> Dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    raw_sheets = data.get("sheets") if isinstance(data.get("sheets"), list) else []
    sheets = []
    for i, raw_sheet in enumerate(raw_sheets):
        cleaned = _clean_delivery_sheet(raw_sheet, i)
        if cleaned:
            sheets.append(cleaned)
    if not sheets:
        sheets = _default_config()["pipeline_sheets"]["sheets"]

    active_key = str(data.get("active_sheet_key") or "").strip()
    enabled_keys = [sheet["key"] for sheet in sheets if sheet.get("enabled")]
    valid_keys = [sheet["key"] for sheet in sheets]
    if active_key not in valid_keys:
        active_key = enabled_keys[0] if enabled_keys else sheets[0]["key"]

    return {
        "active_sheet_key": active_key,
        "sheets": sheets,
    }


def _clean_managed_sheet(item: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    key = str(item.get("key") or f"managed_sheet_{index + 1}").strip().lower().replace(" ", "_")
    mode = str(item.get("mode") or "static").strip().lower()
    if mode not in {"active_pipeline_inbox", "active_pipeline_validated", "active_pipeline_ultra", "active_source_settlement", "active_output_sheet", "static"}:
        mode = "static"

    default_label_map = {
        "active_pipeline_inbox": "Pipeline Inbox",
        "active_pipeline_validated": "Enrichment Sheet",
        "active_pipeline_ultra": "Ultra Queue",
        "active_source_settlement": "Source Settlement Sheet",
        "active_output_sheet": "Final Output Sheet",
        "static": f"Managed Sheet {index + 1}",
    }
    label = str(item.get("label") or default_label_map.get(mode) or f"Managed Sheet {index + 1}").strip()
    enabled = _as_bool(item.get("enabled"), True)
    warning = _as_int(item.get("warning_row_threshold"), 42000, minimum=1)
    stop = max(warning, _as_int(item.get("stop_row_threshold"), 45000, minimum=1))

    cleaned = {
        "key": key,
        "label": label or key,
        "mode": mode,
        "enabled": enabled,
        "warning_row_threshold": warning,
        "stop_row_threshold": stop,
    }
    if mode == "static":
        sheet_ref = str(item.get("sheet_ref") or item.get("sheet_id") or "").strip()
        sheet_id = _extract_sheet_id(sheet_ref)
        tab_name = str(item.get("tab_name") or "").strip()
        if not sheet_id or not tab_name:
            return None
        cleaned.update({
            "sheet_ref": sheet_ref or sheet_id,
            "sheet_id": sheet_id,
            "tab_name": tab_name,
        })
    return cleaned


def _clean_managed_sheets(value: Dict[str, Any] | None, watchers: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    watcher_cfg = ((watchers or {}).get("delivery_sheet_capacity") if isinstance(watchers, dict) else {}) or {}
    warning_default = _as_int(watcher_cfg.get("warning_row_threshold"), 42000, minimum=1)
    stop_default = max(warning_default, _as_int(watcher_cfg.get("stop_row_threshold"), 45000, minimum=1))

    default_entries = _default_config()["managed_sheets"]["entries"]
    raw_entries = data.get("entries") if isinstance(data.get("entries"), list) else []
    raw_map = {}
    for item in raw_entries:
        if isinstance(item, dict):
            raw_key = str(item.get("key") or "").strip().lower().replace(" ", "_")
            if raw_key:
                raw_map[raw_key] = item

    entries: List[Dict[str, Any]] = []
    for index, default_entry in enumerate(default_entries):
        merged = {
            **default_entry,
            "warning_row_threshold": warning_default,
            "stop_row_threshold": stop_default,
            **(raw_map.get(default_entry["key"]) or {}),
        }
        cleaned = _clean_managed_sheet(merged, index)
        if cleaned:
            entries.append(cleaned)

    next_index = len(entries)
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            continue
        raw_key = str(raw_entry.get("key") or "").strip().lower().replace(" ", "_")
        if raw_key in {entry["key"] for entry in entries}:
            continue
        merged = {
            "warning_row_threshold": warning_default,
            "stop_row_threshold": stop_default,
            **raw_entry,
        }
        cleaned = _clean_managed_sheet(merged, next_index)
        if cleaned:
            entries.append(cleaned)
            next_index += 1

    return {"entries": entries}


def _clean_source_settlement_sheet(item: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    label = str(item.get("label") or f"Source Settlement {index + 1}").strip()
    sheet_ref = str(item.get("sheet_ref") or item.get("sheet_id") or "").strip()
    sheet_id = _extract_sheet_id(sheet_ref)
    key = str(item.get("key") or f"source_sheet_{index + 1}").strip().lower().replace(" ", "_")
    if not sheet_id:
        return None
    return {
        "key": key,
        "label": label or key,
        "sheet_ref": sheet_ref or sheet_id,
        "sheet_id": sheet_id,
        "config_tab": str(item.get("config_tab") or "config").strip() or "config",
        "alpha_tab": str(item.get("alpha_tab") or "Alpha").strip() or "Alpha",
        "alpha_high_tab": str(item.get("alpha_high_tab") or "Alpha_high").strip() or "Alpha_high",
        "beta_tab": str(item.get("beta_tab") or "Beta").strip() or "Beta",
        "beta_high_tab": str(item.get("beta_high_tab") or "Beta_high").strip() or "Beta_high",
        "gamma_tab": str(item.get("gamma_tab") or "Gamma").strip() or "Gamma",
        "gamma_high_tab": str(item.get("gamma_high_tab") or "Gamma_high").strip() or "Gamma_high",
        "i_gamma_tab": str(item.get("i_gamma_tab") or "I-gamma").strip() or "I-gamma",
        "pbin_tab": str(item.get("pbin_tab") or "pBin").strip() or "pBin",
        "hydro_tab": str(item.get("hydro_tab") or "Hydro").strip() or "Hydro",
        "hydro_formula_tab": str(item.get("hydro_formula_tab") or "Hydro_formula").strip() or "Hydro_formula",
        "source_webhook_url": str(item.get("source_webhook_url") or SOURCE_WEBHOOK_URL).strip(),
        "enabled": _as_bool(item.get("enabled"), True),
    }
    

def _clean_source_settlement(value: Dict[str, Any] | None) -> Dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    default_cfg = _default_config()["source_settlement"]
    raw_sheets = data.get("sheets") if isinstance(data.get("sheets"), list) else None
    if raw_sheets is None:
        raw_sheets = [data] if isinstance(data, dict) and (data.get("sheet_ref") or data.get("sheet_id")) else []

    sheets = []
    for i, raw_sheet in enumerate(raw_sheets):
        cleaned = _clean_source_settlement_sheet(raw_sheet, i)
        if cleaned:
            sheets.append(cleaned)
    if not sheets:
        sheets = default_cfg["sheets"]

    active_key = str(data.get("active_sheet_key") or "").strip()
    enabled_keys = [sheet["key"] for sheet in sheets if sheet.get("enabled")]
    valid_keys = [sheet["key"] for sheet in sheets]
    if active_key not in valid_keys:
        active_key = enabled_keys[0] if enabled_keys else sheets[0]["key"]
    return {
        "active_sheet_key": active_key,
        "sheets": sheets,
    }


def _clean_output_target_sheet(item: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    label = str(item.get("label") or f"Output Sheet {index + 1}").strip()
    sheet_ref = str(item.get("sheet_ref") or item.get("sheet_id") or "").strip()
    sheet_id = _extract_sheet_id(sheet_ref)
    tab_name = str(item.get("tab_name") or OUTPUT_TAB_NAME).strip()
    key = str(item.get("key") or f"output_sheet_{index + 1}").strip().lower().replace(" ", "_")
    if not sheet_id or not tab_name:
        return None
    return {
        "key": key,
        "label": label or key,
        "sheet_ref": sheet_ref or sheet_id,
        "sheet_id": sheet_id,
        "tab_name": tab_name,
        "enabled": _as_bool(item.get("enabled"), True),
    }


def _clean_output_target(value: Dict[str, Any] | None) -> Dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    default_cfg = _default_config()["output_target"]
    raw_sheets = data.get("sheets") if isinstance(data.get("sheets"), list) else None
    if raw_sheets is None:
        raw_sheets = [data] if isinstance(data, dict) and (data.get("sheet_ref") or data.get("sheet_id")) else []

    sheets = []
    for i, raw_sheet in enumerate(raw_sheets):
        cleaned = _clean_output_target_sheet(raw_sheet, i)
        if cleaned:
            sheets.append(cleaned)
    if not sheets:
        sheets = default_cfg["sheets"]

    active_key = str(data.get("active_sheet_key") or "").strip()
    enabled_keys = [sheet["key"] for sheet in sheets if sheet.get("enabled")]
    valid_keys = [sheet["key"] for sheet in sheets]
    if active_key not in valid_keys:
        active_key = enabled_keys[0] if enabled_keys else sheets[0]["key"]
    return {
        "active_sheet_key": active_key,
        "sheets": sheets,
    }


def _clean_enrichment(value: Dict[str, Any] | None) -> Dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    linkedin = data.get("linkedin") if isinstance(data.get("linkedin"), dict) else {}
    runtime = linkedin.get("runtime") if isinstance(linkedin.get("runtime"), dict) else {}
    watcher = linkedin.get("watcher") if isinstance(linkedin.get("watcher"), dict) else {}
    keyword_scoring = data.get("keyword_scoring") if isinstance(data.get("keyword_scoring"), dict) else {}
    keyword_detector = keyword_scoring.get("keyword_detector") if isinstance(keyword_scoring.get("keyword_detector"), dict) else {}
    keyword_watcher = keyword_scoring.get("watcher") if isinstance(keyword_scoring.get("watcher"), dict) else {}
    scoring = keyword_scoring.get("scoring") if isinstance(keyword_scoring.get("scoring"), dict) else {}
    scraper_v3 = data.get("scraper_v3") if isinstance(data.get("scraper_v3"), dict) else {}
    scraper_v3_runtime = scraper_v3.get("runtime") if isinstance(scraper_v3.get("runtime"), dict) else {}
    scraper_v3_watcher = scraper_v3.get("watcher") if isinstance(scraper_v3.get("watcher"), dict) else {}

    delay_min = _as_float(
        runtime.get("linkedin_delay_min_seconds"),
        DEFAULT_ENRICHMENT_LINKEDIN_DELAY_MIN_SECONDS,
        minimum=0.0,
    )
    delay_max = max(
        delay_min,
        _as_float(
            runtime.get("linkedin_delay_max_seconds"),
            DEFAULT_ENRICHMENT_LINKEDIN_DELAY_MAX_SECONDS,
            minimum=0.0,
        ),
    )

    max_wait = _as_int(
        watcher.get("max_wait_before_forced_drain_minutes"),
        DEFAULT_ENRICHMENT_MAX_WAIT_MINUTES,
        minimum=1,
    )

    return {
        "linkedin": {
            "runtime": {
                "batch_size": _as_int(runtime.get("batch_size"), DEFAULT_ENRICHMENT_BATCH_SIZE, minimum=1),
                "request_timeout_seconds": _as_int(
                    runtime.get("request_timeout_seconds"),
                    DEFAULT_ENRICHMENT_TIMEOUT_SECONDS,
                    minimum=1,
                ),
                "website_workers": _as_int(
                    runtime.get("website_workers"),
                    DEFAULT_ENRICHMENT_WEBSITE_WORKERS,
                    minimum=1,
                ),
                "linkedin_workers": _as_int(
                    runtime.get("linkedin_workers"),
                    DEFAULT_ENRICHMENT_LINKEDIN_WORKERS,
                    minimum=1,
                ),
                "writer_flush_interval_seconds": _as_float(
                    runtime.get("writer_flush_interval_seconds"),
                    DEFAULT_ENRICHMENT_FLUSH_INTERVAL_SECONDS,
                    minimum=0.1,
                ),
                "writer_flush_batch": _as_int(
                    runtime.get("writer_flush_batch"),
                    DEFAULT_ENRICHMENT_FLUSH_BATCH,
                    minimum=1,
                ),
                "linkedin_delay_min_seconds": delay_min,
                "linkedin_delay_max_seconds": delay_max,
                "max_browsers": _as_int(
                    runtime.get("max_browsers"),
                    DEFAULT_ENRICHMENT_MAX_BROWSERS,
                    minimum=1,
                ),
            },
            "watcher": {
                "min_backlog_to_drain": _as_int(
                    watcher.get("min_backlog_to_drain"),
                    DEFAULT_ENRICHMENT_DRAIN_THRESHOLD,
                    minimum=1,
                ),
                "max_wait_before_forced_drain_minutes": max_wait,
                "watcher_interval_minutes": _as_int(
                    watcher.get("watcher_interval_minutes"),
                    DEFAULT_ENRICHMENT_WATCHER_INTERVAL_MINUTES,
                    minimum=1,
                ),
            },
        },
        "keyword_scoring": {
            "keyword_detector": {
                "batch_size": _as_int(
                    keyword_detector.get("batch_size"),
                    DEFAULT_KEYWORD_BATCH_SIZE,
                    minimum=1,
                ),
                "request_timeout_seconds": _as_int(
                    keyword_detector.get("request_timeout_seconds"),
                    DEFAULT_KEYWORD_REQUEST_TIMEOUT_SECONDS,
                    minimum=1,
                ),
                "relevance_keywords": _as_str_list(
                    keyword_detector.get("relevance_keywords"),
                    DEFAULT_KEYWORD_RELEVANCE_KEYWORDS,
                ),
                "qualifier_keywords": _as_str_list(
                    keyword_detector.get("qualifier_keywords"),
                    DEFAULT_KEYWORD_QUALIFIER_KEYWORDS,
                ),
                "qualifier_min_matches": _as_int(
                    keyword_detector.get("qualifier_min_matches"),
                    DEFAULT_CAT3_MIN_MATCHES,
                    minimum=1,
                ),
            },
            "watcher": {
                "trigger_threshold": _as_int(
                    keyword_watcher.get("trigger_threshold"),
                    DEFAULT_KEYWORD_WATCHER_TRIGGER_THRESHOLD,
                    minimum=1,
                ),
                "watcher_interval_minutes": _as_int(
                    keyword_watcher.get("watcher_interval_minutes"),
                    DEFAULT_KEYWORD_WATCHER_INTERVAL_MINUTES,
                    minimum=1,
                ),
                "lock_timeout_minutes": _as_int(
                    keyword_watcher.get("lock_timeout_minutes"),
                    DEFAULT_KEYWORD_WATCHER_LOCK_TIMEOUT_MINUTES,
                    minimum=1,
                ),
            },
            "scoring": {
                "batch_size": _as_int(
                    scoring.get("batch_size"),
                    DEFAULT_SCORING_BATCH_SIZE,
                    minimum=1,
                ),
            },
        },
        "scraper_v3": {
            "runtime": {
                "site_timeout_seconds": _as_int(
                    scraper_v3_runtime.get("site_timeout_seconds"),
                    DEFAULT_SCRAPER_V3_TIMEOUT_SECONDS,
                    minimum=1,
                ),
                "concurrent_sites": _as_int(
                    scraper_v3_runtime.get("concurrent_sites"),
                    DEFAULT_SCRAPER_V3_CONCURRENT_SITES,
                    minimum=1,
                ),
                "playwright_limit": _as_int(
                    scraper_v3_runtime.get("playwright_limit"),
                    DEFAULT_SCRAPER_V3_PLAYWRIGHT_LIMIT,
                    minimum=1,
                ),
                "subpage_limit": _as_int(
                    scraper_v3_runtime.get("subpage_limit"),
                    DEFAULT_SCRAPER_V3_SUBPAGE_LIMIT,
                    minimum=1,
                ),
                "level2_limit": _as_int(
                    scraper_v3_runtime.get("level2_limit"),
                    DEFAULT_SCRAPER_V3_LEVEL2_LIMIT,
                    minimum=1,
                ),
                "evidence_probe_limit": _as_int(
                    scraper_v3_runtime.get("evidence_probe_limit"),
                    DEFAULT_SCRAPER_V3_EVIDENCE_PROBE_LIMIT,
                    minimum=1,
                ),
                "playwright_concurrency": _as_int(
                    scraper_v3_runtime.get("playwright_concurrency"),
                    DEFAULT_SCRAPER_V3_PLAYWRIGHT_CONCURRENCY,
                    minimum=1,
                ),
                "output_write_chunk_size": _as_int(
                    scraper_v3_runtime.get("output_write_chunk_size"),
                    DEFAULT_SCRAPER_V3_OUTPUT_WRITE_CHUNK_SIZE,
                    minimum=1,
                ),
            },
            "watcher": {
                "alpha_beta_trigger_threshold": _as_int(
                    scraper_v3_watcher.get("alpha_beta_trigger_threshold"),
                    DEFAULT_SCRAPER_V3_TRIGGER_THRESHOLD,
                    minimum=1,
                ),
                "max_wait_before_forced_run_minutes": _as_int(
                    scraper_v3_watcher.get("max_wait_before_forced_run_minutes"),
                    DEFAULT_SCRAPER_V3_MAX_WAIT_MINUTES,
                    minimum=1,
                ),
                "watcher_interval_minutes": _as_int(
                    scraper_v3_watcher.get("watcher_interval_minutes"),
                    DEFAULT_SCRAPER_V3_WATCHER_INTERVAL_MINUTES,
                    minimum=1,
                ),
                "stale_lock_timeout_minutes": _as_int(
                    scraper_v3_watcher.get("stale_lock_timeout_minutes"),
                    DEFAULT_SCRAPER_V3_STALE_LOCK_TIMEOUT_MINUTES,
                    minimum=1,
                ),
            },
        },
    }


def _auth_client():
    import gspread
    from google.oauth2.service_account import Credentials

    creds_path = os.path.expanduser(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json"))
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    return gspread.authorize(creds)


def get_active_delivery_sheet(config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    cfg = config or get_system_config(include_live=False)
    delivery = cfg.get("pipeline_sheets") if isinstance(cfg.get("pipeline_sheets"), dict) else {}
    sheets = delivery.get("sheets") if isinstance(delivery.get("sheets"), list) else []
    active_key = delivery.get("active_sheet_key")
    for sheet in sheets:
        if sheet.get("key") == active_key:
            return sheet
    return sheets[0] if sheets else None


def get_active_source_settlement_sheet(config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    cfg = config or get_system_config(include_live=False)
    source = cfg.get("source_settlement") if isinstance(cfg.get("source_settlement"), dict) else {}
    sheets = source.get("sheets") if isinstance(source.get("sheets"), list) else []
    active_key = source.get("active_sheet_key")
    for sheet in sheets:
        if sheet.get("key") == active_key:
            return sheet
    return sheets[0] if sheets else None


def get_active_output_target_sheet(config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    cfg = config or get_system_config(include_live=False)
    output = cfg.get("output_target") if isinstance(cfg.get("output_target"), dict) else {}
    sheets = output.get("sheets") if isinstance(output.get("sheets"), list) else []
    active_key = output.get("active_sheet_key")
    for sheet in sheets:
        if sheet.get("key") == active_key:
            return sheet
    return sheets[0] if sheets else None


def _get_managed_sheet_entry(key: str, config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    cfg = config or get_system_config(include_live=False)
    managed = cfg.get("managed_sheets") if isinstance(cfg.get("managed_sheets"), dict) else {}
    entries = managed.get("entries") if isinstance(managed.get("entries"), list) else []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("key") == key:
            return entry
    return None


def _resolve_managed_sheet_target(entry: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    mode = str(entry.get("mode") or "static").strip().lower()
    if mode.startswith("active_pipeline_"):
        active = get_active_delivery_sheet(config)
        if not active:
            raise ValueError("No active pipeline sheet configured.")
        if mode == "active_pipeline_inbox":
            tab_name = active.get("inbox_tab") or active.get("tab_name")
            label = active.get("label") or entry.get("label") or "Pipeline Inbox"
        elif mode == "active_pipeline_validated":
            tab_name = active.get("validated_tab") or DEFAULT_VALIDATED_TAB
            label = entry.get("label") or "Enrichment Sheet"
        else:
            tab_name = active.get("ultra_tab") or DEFAULT_ULTRA_TAB
            label = entry.get("label") or "Ultra Queue"
        return {
            "key": entry.get("key"),
            "label": label,
            "sheet_ref": active.get("sheet_ref") or active.get("sheet_id"),
            "sheet_id": active.get("sheet_id"),
            "tab_name": tab_name,
            "mode": mode,
        }

    if mode == "active_output_sheet":
        cfg = config or get_system_config(include_live=False)
        output = get_active_output_target_sheet(cfg)
        if not output:
            raise ValueError("No active output sheet configured.")
        return {
            "key": entry.get("key"),
            "label": entry.get("label") or output.get("label") or "Final Output Sheet",
            "sheet_ref": output.get("sheet_ref") or output.get("sheet_id"),
            "sheet_id": output.get("sheet_id"),
            "tab_name": output.get("tab_name"),
            "mode": mode,
        }

    if mode == "active_source_settlement":
        cfg = config or get_system_config(include_live=False)
        source = get_active_source_settlement_sheet(cfg)
        if not source:
            raise ValueError("No active source settlement sheet configured.")
        return {
            "key": entry.get("key"),
            "label": entry.get("label") or source.get("label") or "Source Settlement Sheet",
            "sheet_ref": source.get("sheet_ref") or source.get("sheet_id"),
            "sheet_id": source.get("sheet_id"),
            "tab_name": source.get("alpha_tab") or "Alpha",
            "mode": mode,
        }

    return {
        "key": entry.get("key"),
        "label": entry.get("label"),
        "sheet_ref": entry.get("sheet_ref") or entry.get("sheet_id"),
        "sheet_id": entry.get("sheet_id"),
        "tab_name": entry.get("tab_name"),
        "mode": mode,
    }


def get_managed_sheet_capacity_status(
    entry: Dict[str, Any],
    *,
    pending_rows: int = 0,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = config or get_system_config(include_live=False)
    try:
        target = _resolve_managed_sheet_target(entry, config=cfg)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "warning": False,
            "blocked": False,
            "sheet_key": entry.get("key"),
            "sheet_label": entry.get("label"),
            "tab_name": entry.get("tab_name"),
        }

    try:
        client = _auth_client()
        book = client.open_by_key(target["sheet_id"])
        ws = book.worksheet(target["tab_name"])
        used_rows = len(ws.col_values(1))
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "warning": False,
            "blocked": False,
            "sheet_key": target.get("key"),
            "sheet_label": target.get("label"),
            "sheet_id": target.get("sheet_id"),
            "tab_name": target.get("tab_name"),
            "mode": target.get("mode"),
        }

    warning_threshold = _as_int(entry.get("warning_row_threshold"), 42000, minimum=1)
    stop_threshold = max(warning_threshold, _as_int(entry.get("stop_row_threshold"), 45000, minimum=1))
    projected_rows = used_rows + max(0, int(pending_rows or 0))
    enabled = _as_bool(entry.get("enabled"), True)
    warning = enabled and projected_rows >= warning_threshold
    blocked = enabled and projected_rows >= stop_threshold

    return {
        "ok": True,
        "warning": warning,
        "blocked": blocked,
        "sheet_key": target.get("key"),
        "sheet_label": target.get("label"),
        "sheet_id": target.get("sheet_id"),
        "tab_name": target.get("tab_name"),
        "mode": target.get("mode"),
        "enabled": enabled,
        "current_rows": used_rows,
        "data_rows": max(used_rows - 1, 0),
        "pending_rows": max(0, int(pending_rows or 0)),
        "projected_rows": projected_rows,
        "warning_row_threshold": warning_threshold,
        "stop_row_threshold": stop_threshold,
        "rows_remaining_to_warning": max(warning_threshold - used_rows, 0),
        "rows_remaining_to_stop": max(stop_threshold - used_rows, 0),
    }


def get_delivery_sheet_capacity_status(
    sheet: Optional[Dict[str, Any]] = None,
    *,
    pending_rows: int = 0,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = config or get_system_config(include_live=False)
    target = sheet or get_active_delivery_sheet(cfg)
    watchers = cfg.get("watchers", {}).get("delivery_sheet_capacity", {})
    managed_entry = _get_managed_sheet_entry("pipeline_inbox", cfg)
    if not target:
        return {
            "ok": False,
            "error": "No active delivery sheet configured.",
            "warning": False,
            "blocked": True,
        }

    try:
        client = _auth_client()
        book = client.open_by_key(target["sheet_id"])
        ws = book.worksheet(target["inbox_tab"])
        used_rows = len(ws.col_values(1))
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "warning": False,
            "blocked": False,
            "sheet_key": target.get("key"),
            "sheet_label": target.get("label"),
            "sheet_id": target.get("sheet_id"),
            "tab_name": target.get("inbox_tab"),
        }

    warning_threshold = _as_int(
        (managed_entry or {}).get("warning_row_threshold"),
        _as_int(watchers.get("warning_row_threshold"), 42000, minimum=1),
        minimum=1,
    )
    stop_threshold = max(
        warning_threshold,
        _as_int(
            (managed_entry or {}).get("stop_row_threshold"),
            _as_int(watchers.get("stop_row_threshold"), 45000, minimum=1),
            minimum=1,
        ),
    )
    projected_rows = used_rows + max(0, int(pending_rows or 0))
    enabled = _as_bool((managed_entry or {}).get("enabled"), _as_bool(watchers.get("enabled"), True))
    warning = enabled and projected_rows >= warning_threshold
    blocked = enabled and projected_rows >= stop_threshold

    return {
        "ok": True,
        "warning": warning,
        "blocked": blocked,
        "sheet_key": target.get("key"),
        "sheet_label": target.get("label"),
        "sheet_id": target.get("sheet_id"),
        "tab_name": target.get("inbox_tab"),
        "current_rows": used_rows,
        "data_rows": max(used_rows - 1, 0),
        "pending_rows": max(0, int(pending_rows or 0)),
        "projected_rows": projected_rows,
        "warning_row_threshold": warning_threshold,
        "stop_row_threshold": stop_threshold,
        "rows_remaining_to_warning": max(warning_threshold - used_rows, 0),
        "rows_remaining_to_stop": max(stop_threshold - used_rows, 0),
    }


def get_system_config(*, include_live: bool = False) -> Dict[str, Any]:
    default = _default_config()
    raw = _read_raw()
    config = {
        "watchers": _clean_watchers({**default.get("watchers", {}), **(raw.get("watchers") or {})}),
        "pipeline_sheets": _clean_delivery_sheets({
            **default.get("pipeline_sheets", {}),
            **(raw.get("pipeline_sheets") or raw.get("delivery_sheets") or {}),
        }),
        "source_settlement": _clean_source_settlement({
            **default.get("source_settlement", {}),
            **(raw.get("source_settlement") or {}),
        }),
        "output_target": _clean_output_target({
            **default.get("output_target", {}),
            **(raw.get("output_target") or {}),
        }),
        "managed_sheets": _clean_managed_sheets(
            {
                **default.get("managed_sheets", {}),
                **(raw.get("managed_sheets") or {}),
            },
            {**default.get("watchers", {}), **(raw.get("watchers") or {})},
        ),
        "sheet_rotation": {
            "copy_folder_id": _extract_drive_folder_id(
                ((raw.get("sheet_rotation") or {}).get("copy_folder_id") if isinstance(raw.get("sheet_rotation"), dict) else "")
                or ((default.get("sheet_rotation") or {}).get("copy_folder_id") if isinstance(default.get("sheet_rotation"), dict) else "")
            ),
        },
        "enrichment": _clean_enrichment({
            **default.get("enrichment", {}),
            **(raw.get("enrichment") or {}),
        }),
    }
    config["delivery_sheets"] = config["pipeline_sheets"]
    if not include_live:
        return config

    sheets_with_status: List[Dict[str, Any]] = []
    for sheet in config["pipeline_sheets"]["sheets"]:
        status = get_delivery_sheet_capacity_status(sheet, config=config)
        sheets_with_status.append({**sheet, "status": status})

    managed_entries_with_status: List[Dict[str, Any]] = []
    for entry in config["managed_sheets"]["entries"]:
        status = get_managed_sheet_capacity_status(entry, config=config)
        managed_entries_with_status.append({**entry, "status": status})

    active_status = get_delivery_sheet_capacity_status(config=config)
    source_sheets = config["source_settlement"]["sheets"]
    active_source = get_active_source_settlement_sheet(config) or {}
    output_sheets = config["output_target"]["sheets"]
    active_output = get_active_output_target_sheet(config) or {}
    return {
        **config,
        "pipeline_sheets": {
            **config["pipeline_sheets"],
            "sheets": sheets_with_status,
            "active_status": active_status,
        },
        "source_settlement": {
            **config["source_settlement"],
            "sheets": source_sheets,
            "active_sheet": active_source,
        },
        "output_target": {
            **config["output_target"],
            "sheets": output_sheets,
            "active_sheet": active_output,
        },
        "managed_sheets": {
            **config["managed_sheets"],
            "entries": managed_entries_with_status,
        },
        "delivery_sheets": {
            **config["pipeline_sheets"],
            "sheets": sheets_with_status,
            "active_status": active_status,
        },
    }


def save_system_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    current = get_system_config(include_live=False)
    watchers = _clean_watchers({
        **current.get("watchers", {}),
        **(payload.get("watchers") if isinstance(payload.get("watchers"), dict) else {}),
    })
    delivery_sheets = _clean_delivery_sheets({
        **current.get("pipeline_sheets", {}),
        **(
            payload.get("pipeline_sheets")
            if isinstance(payload.get("pipeline_sheets"), dict)
            else payload.get("delivery_sheets")
            if isinstance(payload.get("delivery_sheets"), dict)
            else {}
        ),
    })
    config = {
        "watchers": watchers,
        "pipeline_sheets": delivery_sheets,
        "delivery_sheets": delivery_sheets,
        "source_settlement": _clean_source_settlement({
            **current.get("source_settlement", {}),
            **(payload.get("source_settlement") if isinstance(payload.get("source_settlement"), dict) else {}),
        }),
        "output_target": _clean_output_target({
            **current.get("output_target", {}),
            **(payload.get("output_target") if isinstance(payload.get("output_target"), dict) else {}),
        }),
        "managed_sheets": _clean_managed_sheets(
            {
                **current.get("managed_sheets", {}),
                **(payload.get("managed_sheets") if isinstance(payload.get("managed_sheets"), dict) else {}),
            },
            watchers,
        ),
        "sheet_rotation": {
            "copy_folder_id": _extract_drive_folder_id(
                ((payload.get("sheet_rotation") or {}).get("copy_folder_id") if isinstance(payload.get("sheet_rotation"), dict) else "")
                or ((current.get("sheet_rotation") or {}).get("copy_folder_id") if isinstance(current.get("sheet_rotation"), dict) else "")
            ),
        },
        "enrichment": _clean_enrichment({
            **current.get("enrichment", {}),
            **(payload.get("enrichment") if isinstance(payload.get("enrichment"), dict) else {}),
        }),
    }
    _write_raw(config)
    return get_system_config(include_live=True)
