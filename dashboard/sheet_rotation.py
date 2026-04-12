"""Automatic sheet rotation manager.

Creates clean replacement sheets before capacity is reached, pauses the
automation when the active sheet is full, and resumes after operator
approval or timeout by switching the active target to the prepared sheet.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from gspread.utils import rowcol_to_a1

from . import settings
from .process import get_process_manager
from .scheduler import get_scheduler

sys.path.insert(0, str(settings.BOTS_DIR))

from common.config import OUTPUT_WEBHOOK_URL, SOURCE_WEBHOOK_URL, WEBHOOK_SECRET, WEBHOOK_URL
from common.sheets_guard import authorize_gspread_client, guarded_gspread_call
from common.system_config import (
    get_active_delivery_sheet,
    get_active_output_target_sheet,
    get_active_source_settlement_sheet,
    get_delivery_sheet_capacity_status,
    get_managed_sheet_capacity_status,
    get_system_config,
    save_system_config,
)

ROTATION_STATE_FILE = settings.CONTROLLER_DIR / "sheet_rotation_state.json"
ROTATION_WARNING_PERCENT = 70.0
ROTATION_PREPARE_PERCENT = 90.0
ROTATION_AUTO_CONTINUE_MINUTES = 5
ROTATION_LOOP_SECONDS = 30
ROTATION_STATUS_TTL_SECONDS = 300
ROTATION_HEALTHY_CHECK_SECONDS = 900
ROTATION_WARNING_CHECK_SECONDS = 300
ROTATION_PREPARE_CHECK_SECONDS = 120
ROTATION_BLOCKED_CHECK_SECONDS = 60
ROTATION_QUOTA_BACKOFF_SECONDS = 1800

_SNAPSHOT_CACHE: Dict[str, Any] = {"fetched_at": 0.0, "data": None}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_state() -> Dict[str, Any]:
    if not ROTATION_STATE_FILE.exists():
        return {"registries": {}}
    try:
        data = json.loads(ROTATION_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"registries": {}}
    except Exception:
        return {"registries": {}}


def _next_interval_for_status(info: Dict[str, Any], entry: Dict[str, Any]) -> int:
    error_text = str(info.get("error") or entry.get("last_error") or "").lower()
    if "quota exceeded" in error_text or "[429]" in error_text:
        return ROTATION_QUOTA_BACKOFF_SECONDS
    phase = str(info.get("phase") or "").lower()
    if phase == "blocked":
        return ROTATION_BLOCKED_CHECK_SECONDS
    if phase == "prepare":
        return ROTATION_PREPARE_CHECK_SECONDS
    if phase == "warning":
        return ROTATION_WARNING_CHECK_SECONDS
    return ROTATION_HEALTHY_CHECK_SECONDS


def _write_state(state: Dict[str, Any]) -> None:
    state = deepcopy(state)
    state["updated_at"] = _iso(_utcnow())
    ROTATION_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    _SNAPSHOT_CACHE["fetched_at"] = 0.0
    _SNAPSHOT_CACHE["data"] = None


def _auth_client():
    return authorize_gspread_client(
        creds_path=os.path.expanduser(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json")),
        scopes=SCOPES,
    )


def _new_key(base: str) -> str:
    ts = _utcnow().strftime("%Y%m%d_%H%M%S")
    return f"{base}_{ts}".lower().replace(" ", "_")


def _new_label(label: str) -> str:
    ts = _utcnow().strftime("%Y-%m-%d %H:%M")
    return f"{label} Next {ts}"


def _clear_worksheet_data(ws) -> None:
    last_row = ws.row_count
    last_col = ws.col_count
    used_rows = len(
        guarded_gspread_call(
            lambda: ws.col_values(1),
            operation="read",
            description="Read worksheet column A before clearing",
        )
    )
    if used_rows <= 1:
        return
    end = rowcol_to_a1(max(used_rows, 2), max(last_col, 1))
    guarded_gspread_call(
        lambda: ws.batch_clear([f"A2:{end}"]),
        operation="write",
        description="Clear worksheet data",
    )


def _clear_pipeline_copy(book, entry: Dict[str, Any]) -> None:
    tabs = [
        entry.get("inbox_tab"),
        entry.get("validated_tab"),
        entry.get("ultra_tab"),
        entry.get("hydro_formula_tab"),
        entry.get("hydro_incomplete_tab"),
    ]
    for tab in [t for t in tabs if t]:
        try:
            ws = guarded_gspread_call(
                lambda tab_name=tab: book.worksheet(tab_name),
                operation="read",
                description=f"Open pipeline copy worksheet {tab}",
            )
            _clear_worksheet_data(ws)
        except Exception:
            continue


def _clear_source_settlement_copy(book, entry: Dict[str, Any]) -> None:
    tabs = [
        entry.get("alpha_tab"),
        entry.get("alpha_high_tab"),
        entry.get("beta_tab"),
        entry.get("beta_high_tab"),
        entry.get("gamma_tab"),
        entry.get("gamma_high_tab"),
        entry.get("i_gamma_tab"),
        entry.get("pbin_tab"),
        entry.get("hydro_tab"),
        entry.get("hydro_formula_tab"),
    ]
    for tab in [t for t in tabs if t]:
        try:
            ws = guarded_gspread_call(
                lambda tab_name=tab: book.worksheet(tab_name),
                operation="read",
                description=f"Open source settlement copy worksheet {tab}",
            )
            _clear_worksheet_data(ws)
        except Exception:
            continue


def _clear_output_copy(book, entry: Dict[str, Any]) -> None:
    try:
        ws = guarded_gspread_call(
            lambda: book.worksheet(entry.get("tab_name")),
            operation="read",
            description="Open output copy worksheet",
        )
        _clear_worksheet_data(ws)
    except Exception:
        return


def _quote_sheet_range(sheet_name: str, range_a1: str = "A:A") -> str:
    escaped = str(sheet_name or "").replace("'", "''")
    return f"'{escaped}'!{range_a1}"


def _batch_column_counts(book, tab_names: list[str]) -> Dict[str, int]:
    valid_tabs = [str(tab or "").strip() for tab in tab_names if str(tab or "").strip()]
    if not valid_tabs:
        return {}
    result = guarded_gspread_call(
        lambda: book.values_batch_get(
            [_quote_sheet_range(tab, "A:A") for tab in valid_tabs],
            params={"majorDimension": "COLUMNS"},
        ),
        operation="read",
        description="Batch-read source settlement column counts",
    )
    value_ranges = result.get("valueRanges") if isinstance(result, dict) else []
    counts: Dict[str, int] = {}
    for tab, payload in zip(valid_tabs, value_ranges if isinstance(value_ranges, list) else []):
        columns = payload.get("values") if isinstance(payload, dict) else None
        first_column = columns[0] if isinstance(columns, list) and columns else []
        counts[tab] = len(first_column) if isinstance(first_column, list) else 0
    return counts


def _copy_clean_sheet(registry: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    config = get_system_config(include_live=False)
    rotation_cfg = config.get("sheet_rotation") if isinstance(config.get("sheet_rotation"), dict) else {}
    folder_id = str(rotation_cfg.get("copy_folder_id") or "").strip() or None
    try:
        copy_data = _create_rotation_copy_via_apps_script(
            registry,
            title=_new_label(entry.get("label") or registry.replace("_", " ").title()),
            folder_id=folder_id,
        )
        return {
            **entry,
            "key": _new_key(entry.get("key") or registry),
            "label": copy_data.get("title") or _new_label(entry.get("label") or "Sheet"),
            "sheet_ref": copy_data.get("sheet_ref") or copy_data.get("sheet_id"),
            "sheet_id": copy_data.get("sheet_id"),
            "enabled": True,
        }
    except Exception:
        pass

    client = _auth_client()
    copied = client.copy(
        entry["sheet_id"],
        title=_new_label(entry.get("label") or registry.replace("_", " ").title()),
        copy_permissions=True,
        folder_id=folder_id,
    )
    book = client.open_by_key(copied.id)
    if registry == "pipeline_sheets":
        _clear_pipeline_copy(book, entry)
    elif registry == "source_settlement":
        _clear_source_settlement_copy(book, entry)
    elif registry == "output_target":
        _clear_output_copy(book, entry)
    else:
        raise ValueError(f"Unsupported registry {registry}")
    return {
        **entry,
        "key": _new_key(entry.get("key") or registry),
        "label": _new_label(entry.get("label") or "Sheet"),
        "sheet_ref": copied.id,
        "sheet_id": copied.id,
        "enabled": True,
    }


def _create_rotation_copy_via_apps_script(registry: str, *, title: str, folder_id: Optional[str]) -> Dict[str, Any]:
    if registry == "pipeline_sheets":
        webhook_url = WEBHOOK_URL
    elif registry == "source_settlement":
        webhook_url = SOURCE_WEBHOOK_URL
    elif registry == "output_target":
        webhook_url = OUTPUT_WEBHOOK_URL
    else:
        raise ValueError(f"Unsupported registry {registry}")

    if not webhook_url:
        raise ValueError(f"No webhook configured for {registry}")

    payload = {
        "token": WEBHOOK_SECRET,
        "action": "create_rotation_copy",
        "rotation": {
            "title": title,
            "folder_id": folder_id,
        },
    }
    timeout = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.post(webhook_url, headers={"Content-Type": "application/json"}, json=payload)
        response.raise_for_status()
        parsed = json.loads((response.text or "").strip() or "{}")
    if str(parsed.get("status") or "").strip().lower() not in {"ok", "success"}:
        raise ValueError(str(parsed.get("message") or response.text or "Apps Script copy failed"))
    copy_data = parsed.get("copy") if isinstance(parsed.get("copy"), dict) else {}
    sheet_id = str(copy_data.get("sheet_id") or "").strip()
    if not sheet_id:
        raise ValueError("Apps Script copy response missing sheet_id")
    return {
        "sheet_id": sheet_id,
        "sheet_ref": str(copy_data.get("sheet_ref") or sheet_id).strip(),
        "title": str(copy_data.get("title") or title).strip(),
    }


def _find_registry_entry(config: Dict[str, Any], registry: str, key: str) -> Optional[Dict[str, Any]]:
    bucket = config.get(registry) if isinstance(config.get(registry), dict) else {}
    sheets = bucket.get("sheets") if isinstance(bucket.get("sheets"), list) else []
    return next((item for item in sheets if item.get("key") == key), None)


def _append_registry_entry(registry: str, new_entry: Dict[str, Any]) -> Dict[str, Any]:
    config = get_system_config(include_live=False)
    bucket = deepcopy(config.get(registry) if isinstance(config.get(registry), dict) else {})
    sheets = bucket.get("sheets") if isinstance(bucket.get("sheets"), list) else []
    sheets.append(new_entry)
    bucket["sheets"] = sheets
    payload = {registry: bucket}
    return save_system_config(payload)


def _sync_pipeline(config: Dict[str, Any]) -> tuple[bool, str]:
    pipeline = ((config.get("pipeline_sheets") or {}).get("sheets") if isinstance(config, dict) else None) or []
    active_key = ((config.get("pipeline_sheets") or {}).get("active_sheet_key") if isinstance(config, dict) else None) or ""
    active = next((item for item in pipeline if item.get("key") == active_key), None)
    if not active or not WEBHOOK_URL:
        return False, "No active pipeline sheet or webhook URL configured."
    payload = {
        "token": WEBHOOK_SECRET,
        "action": "sync_pipeline_config",
        "pipeline": {
            "sheet_id": active.get("sheet_id"),
            "sheet_label": active.get("label"),
            "inbox_tab": active.get("inbox_tab"),
            "validated_tab": active.get("validated_tab"),
            "ultra_tab": active.get("ultra_tab"),
            "config_tab": active.get("config_tab"),
            "inbox_quota": active.get("inbox_quota"),
            "enrich_quota": active.get("enrich_quota"),
            "hydro_sheet_id": active.get("hydro_sheet_id"),
            "hydro_formula_tab": active.get("hydro_formula_tab"),
            "hydro_incomplete_tab": active.get("hydro_incomplete_tab"),
        },
    }
    return _post_sync(WEBHOOK_URL, payload)


def _sync_source(config: Dict[str, Any]) -> tuple[bool, str]:
    source_cfg = (config.get("source_settlement") if isinstance(config, dict) else None) or {}
    sheets = source_cfg.get("sheets") if isinstance(source_cfg.get("sheets"), list) else []
    active_key = source_cfg.get("active_sheet_key")
    source = next((item for item in sheets if item.get("key") == active_key), sheets[0] if sheets else None) or {}
    webhook_url = source.get("source_webhook_url") or SOURCE_WEBHOOK_URL
    if not source or not webhook_url:
        return False, "No source settlement target or source webhook URL configured."
    payload = {
        "token": WEBHOOK_SECRET,
        "action": "sync_source_sheet_config",
        "source": {
            "sheet_id": source.get("sheet_id"),
            "tabs_to_process": [
                source.get("alpha_tab"),
                source.get("alpha_high_tab"),
                source.get("beta_tab"),
                source.get("beta_high_tab"),
                source.get("gamma_tab"),
                source.get("gamma_high_tab"),
            ],
        },
    }
    return _post_sync(webhook_url, payload)


def _sync_output(config: Dict[str, Any]) -> tuple[bool, str]:
    output_cfg = (config.get("output_target") if isinstance(config, dict) else None) or {}
    sheets = output_cfg.get("sheets") if isinstance(output_cfg.get("sheets"), list) else []
    active_key = output_cfg.get("active_sheet_key")
    output = next((item for item in sheets if item.get("key") == active_key), sheets[0] if sheets else None) or {}
    if not output or not OUTPUT_WEBHOOK_URL:
        return False, "No output target or output webhook URL configured."
    payload = {
        "token": WEBHOOK_SECRET,
        "action": "sync_output_sheet_config",
        "output": {
            "sheet_id": output.get("sheet_id"),
            "tab_name": output.get("tab_name"),
        },
    }
    return _post_sync(OUTPUT_WEBHOOK_URL, payload)


def _post_sync(url: str, payload: Dict[str, Any]) -> tuple[bool, str]:
    try:
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.post(url, headers={"Content-Type": "application/json"}, json=payload)
            response.raise_for_status()
            text = (response.text or "").strip()
            if not text:
                return False, "Empty Apps Script response."
            if text.lower().startswith("error:"):
                return False, text
            try:
                parsed = json.loads(text)
            except Exception:
                return True, text
            status = str(parsed.get("status") or "").strip().lower()
            if status in {"ok", "success"}:
                return True, text
            return False, str(parsed.get("message") or text)
    except Exception as exc:
        return False, str(exc)


def _activate_registry_entry(registry: str, sheet_key: str) -> tuple[bool, str]:
    config = get_system_config(include_live=False)
    bucket = deepcopy(config.get(registry) if isinstance(config.get(registry), dict) else {})
    bucket["active_sheet_key"] = sheet_key
    saved = save_system_config({registry: bucket})
    if registry == "pipeline_sheets":
        return _sync_pipeline(saved)
    if registry == "source_settlement":
        return _sync_source(saved)
    if registry == "output_target":
        return _sync_output(saved)
    return False, f"Unsupported registry {registry}"


def _pause_system() -> str:
    scheduler = get_scheduler()
    pm = get_process_manager()
    scheduler_state = scheduler.get_state()
    process_running = pm.is_running()
    if scheduler_state.get("enabled"):
        scheduler.manual_stop()
        return "cycle"
    if process_running:
        pm.stop()
        return "manual"
    return "idle"


def _resume_system(mode: str) -> None:
    scheduler = get_scheduler()
    pm = get_process_manager()
    if mode == "cycle":
        scheduler.start_cycle()
    elif mode == "manual":
        pm.start()


def _get_source_capacity_status(config: Dict[str, Any]) -> Dict[str, Any]:
    source = get_active_source_settlement_sheet(config)
    if not source:
        return {"ok": False, "error": "No active source settlement sheet configured."}
    managed_entry = next(
        (
            entry
            for entry in (((config.get("managed_sheets") or {}).get("entries")) or [])
            if isinstance(entry, dict) and entry.get("key") == "source_settlement"
        ),
        {"key": "source_settlement", "label": "Source Settlement Sheet", "mode": "active_source_settlement", "enabled": True, "warning_row_threshold": 42000, "stop_row_threshold": 45000},
    )
    client = _auth_client()
    book = guarded_gspread_call(
        lambda: client.open_by_key(source["sheet_id"]),
        operation="read",
        description="Open source settlement spreadsheet",
    )
    tabs = [
        source.get("alpha_tab"),
        source.get("alpha_high_tab"),
        source.get("beta_tab"),
        source.get("beta_high_tab"),
        source.get("gamma_tab"),
        source.get("gamma_high_tab"),
        source.get("i_gamma_tab"),
        source.get("pbin_tab"),
        source.get("hydro_tab"),
        source.get("hydro_formula_tab"),
    ]
    max_rows = 0
    max_tab = tabs[0] if tabs else "--"
    try:
        counts = _batch_column_counts(book, tabs)
    except Exception:
        counts = {}
    if counts:
        for tab in [t for t in tabs if t]:
            rows = int(counts.get(tab) or 0)
            if rows >= max_rows:
                max_rows = rows
                max_tab = tab
    else:
        for tab in [t for t in tabs if t]:
            try:
                ws = guarded_gspread_call(
                    lambda tab_name=tab: book.worksheet(tab_name),
                    operation="read",
                    description=f"Open source settlement worksheet {tab}",
                )
                rows = len(
                    guarded_gspread_call(
                        lambda: ws.col_values(1),
                        operation="read",
                        description=f"Read source settlement worksheet {tab} column A",
                    )
                )
                if rows >= max_rows:
                    max_rows = rows
                    max_tab = tab
            except Exception:
                continue
    warning_threshold = int(managed_entry.get("warning_row_threshold") or 42000)
    stop_threshold = max(warning_threshold, int(managed_entry.get("stop_row_threshold") or 45000))
    enabled = bool(managed_entry.get("enabled", True))
    warning = enabled and max_rows >= warning_threshold
    blocked = enabled and max_rows >= stop_threshold
    percent = round((max_rows / stop_threshold) * 100, 1) if stop_threshold else None
    return {
        "ok": True,
        "warning": warning,
        "blocked": blocked,
        "sheet_key": source.get("key"),
        "sheet_label": source.get("label"),
        "sheet_id": source.get("sheet_id"),
        "tab_name": max_tab,
        "current_rows": max_rows,
        "data_rows": max(max_rows - 1, 0),
        "warning_row_threshold": warning_threshold,
        "stop_row_threshold": stop_threshold,
        "rows_remaining_to_stop": max(stop_threshold - max_rows, 0),
        "percent_used": percent,
    }


def _registry_status(config: Dict[str, Any], registry: str) -> Dict[str, Any]:
    if registry == "pipeline_sheets":
        status = get_delivery_sheet_capacity_status(config=config)
        active = get_active_delivery_sheet(config) or {}
    elif registry == "source_settlement":
        status = _get_source_capacity_status(config)
        active = get_active_source_settlement_sheet(config) or {}
    elif registry == "output_target":
        managed = next(
            (
                entry
                for entry in (((config.get("managed_sheets") or {}).get("entries")) or [])
                if isinstance(entry, dict) and entry.get("key") == "output"
            ),
            {"key": "output", "label": "Final Output Sheet", "mode": "active_output_sheet", "enabled": True, "warning_row_threshold": 42000, "stop_row_threshold": 45000},
        )
        status = get_managed_sheet_capacity_status(managed, config=config)
        active = get_active_output_target_sheet(config) or {}
    else:
        return {"ok": False, "error": f"Unsupported registry {registry}"}

    current_rows = int(status.get("current_rows") or 0)
    stop_threshold = int(status.get("stop_row_threshold") or 0)
    percent_used = round((current_rows / stop_threshold) * 100, 1) if stop_threshold else None
    phase = "healthy"
    if stop_threshold and current_rows >= stop_threshold:
        phase = "blocked"
    elif percent_used is not None and percent_used >= ROTATION_PREPARE_PERCENT:
        phase = "prepare"
    elif percent_used is not None and percent_used >= ROTATION_WARNING_PERCENT:
        phase = "warning"

    return {
        **status,
        "registry": registry,
        "active_sheet_key": active.get("key"),
        "active_label": active.get("label"),
        "percent_used": percent_used,
        "phase": phase,
    }


def get_sheet_rotation_snapshot() -> Dict[str, Any]:
    cached = _SNAPSHOT_CACHE.get("data")
    fetched_at = float(_SNAPSHOT_CACHE.get("fetched_at") or 0.0)
    if cached is not None and (time.time() - fetched_at) < ROTATION_STATUS_TTL_SECONDS:
        return deepcopy(cached)

    state = _read_state() or {"registries": {}}
    config = get_system_config(include_live=False)
    registries = {}
    for registry in ("pipeline_sheets", "source_settlement", "output_target"):
        entry_state = ((state.get("registries") or {}).get(registry)) or {}
        if registry == "pipeline_sheets":
            bucket = config.get("pipeline_sheets") if isinstance(config.get("pipeline_sheets"), dict) else {}
        elif registry == "source_settlement":
            bucket = config.get("source_settlement") if isinstance(config.get("source_settlement"), dict) else {}
        elif registry == "output_target":
            bucket = config.get("output_target") if isinstance(config.get("output_target"), dict) else {}
        else:
            bucket = {}
        sheets = bucket.get("sheets") if isinstance(bucket.get("sheets"), list) else []
        active_key = entry_state.get("active_sheet_key") or bucket.get("active_sheet_key")
        active_entry = next((item for item in sheets if item.get("key") == active_key), None) or {}
        info = {
            "ok": not bool(entry_state.get("last_error")),
            "registry": registry,
            "active_sheet_key": active_key,
            "active_label": entry_state.get("active_label") or active_entry.get("label"),
            "tab_name": entry_state.get("tab_name"),
            "current_rows": entry_state.get("current_rows"),
            "warning_row_threshold": entry_state.get("warning_row_threshold"),
            "stop_row_threshold": entry_state.get("stop_row_threshold"),
            "percent_used": entry_state.get("percent_used"),
            "phase": entry_state.get("phase") or "unknown",
            "error": entry_state.get("last_error"),
        }
        registries[registry] = {
            **entry_state,
            "registry": registry,
            "rotation_status": entry_state.get("status"),
            "live_status": info,
        }
    snapshot = {
        "warning_percent": ROTATION_WARNING_PERCENT,
        "prepare_percent": ROTATION_PREPARE_PERCENT,
        "auto_continue_minutes": ROTATION_AUTO_CONTINUE_MINUTES,
        "registries": registries,
    }
    _SNAPSHOT_CACHE["fetched_at"] = time.time()
    _SNAPSHOT_CACHE["data"] = deepcopy(snapshot)
    return snapshot


def approve_pending_rotation(registry: str) -> Dict[str, Any]:
    state = _read_state() or {"registries": {}}
    registries = state.setdefault("registries", {})
    entry = registries.setdefault(registry, {})
    entry["approved_at"] = _iso(_utcnow())
    _write_state(state)
    return get_sheet_rotation_snapshot()


def _process_registry(registry: str, state: Dict[str, Any]) -> None:
    now = _utcnow()
    config = get_system_config(include_live=False)
    registries = state.setdefault("registries", {})
    entry = registries.setdefault(registry, {})
    next_due_at = _parse_iso(entry.get("next_check_at"))
    if next_due_at and now < next_due_at:
        return

    info = _registry_status(config, registry)
    entry["registry"] = registry
    entry["last_checked_at"] = _iso(now)
    entry["active_sheet_key"] = info.get("active_sheet_key")
    entry["active_label"] = info.get("active_label")
    entry["percent_used"] = info.get("percent_used")
    entry["tab_name"] = info.get("tab_name")
    entry["current_rows"] = info.get("current_rows")
    entry["warning_row_threshold"] = info.get("warning_row_threshold")
    entry["stop_row_threshold"] = info.get("stop_row_threshold")
    entry["phase"] = info.get("phase")
    entry["last_error"] = info.get("error")
    entry["next_check_at"] = _iso(now + timedelta(seconds=_next_interval_for_status(info, entry)))

    pending_key = entry.get("pending_sheet_key")
    if pending_key and _find_registry_entry(config, registry, pending_key) is None:
        pending_key = None
        entry.pop("pending_sheet_key", None)
        entry.pop("pending_created_at", None)

    if info.get("ok") and info.get("phase") in {"prepare", "blocked"} and not pending_key:
        active_bucket = config.get(registry) if isinstance(config.get(registry), dict) else {}
        active_sheets = active_bucket.get("sheets") if isinstance(active_bucket.get("sheets"), list) else []
        active = next((item for item in active_sheets if item.get("key") == info.get("active_sheet_key")), None)
        if active:
            prepared = _copy_clean_sheet(registry, active)
            save_system_config({registry: {"active_sheet_key": active_bucket.get("active_sheet_key"), "sheets": active_sheets + [prepared]}})
            entry["pending_sheet_key"] = prepared["key"]
            entry["pending_sheet_label"] = prepared["label"]
            entry["pending_sheet_id"] = prepared["sheet_id"]
            entry["pending_created_at"] = _iso(_utcnow())
            entry["status"] = "prepared"

    pending_key = entry.get("pending_sheet_key")
    if info.get("phase") == "blocked" and pending_key and not entry.get("pause_requested_at"):
        resume_mode = _pause_system()
        entry["pause_requested_at"] = _iso(_utcnow())
        entry["approval_deadline_at"] = _iso(_utcnow() + timedelta(minutes=ROTATION_AUTO_CONTINUE_MINUTES))
        entry["resume_mode"] = resume_mode
        entry["status"] = "paused_pending_approval"

    pause_requested_at = _parse_iso(entry.get("pause_requested_at"))
    approved_at = _parse_iso(entry.get("approved_at"))
    deadline_at = _parse_iso(entry.get("approval_deadline_at"))
    should_activate = bool(
        pending_key
        and pause_requested_at
        and (
            approved_at is not None
            or (deadline_at is not None and _utcnow() >= deadline_at)
        )
    )

    if should_activate:
        ok, message = _activate_registry_entry(registry, pending_key)
        entry["last_activation_message"] = message
        if ok:
            _resume_system(entry.get("resume_mode") or "idle")
            entry["last_switched_at"] = _iso(_utcnow())
            entry["last_switched_to"] = pending_key
            entry["status"] = "completed"
            for key in (
                "pending_sheet_key",
                "pending_sheet_label",
                "pending_sheet_id",
                "pending_created_at",
                "pause_requested_at",
                "approval_deadline_at",
                "approved_at",
                "resume_mode",
            ):
                entry.pop(key, None)
            entry["next_check_at"] = _iso(_utcnow() + timedelta(seconds=ROTATION_HEALTHY_CHECK_SECONDS))


class SheetRotationManager:
    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def run_once(self) -> None:
        state = _read_state() or {"registries": {}}
        for registry in ("pipeline_sheets", "source_settlement", "output_target"):
            try:
                _process_registry(registry, state)
            except Exception as exc:
                registries = state.setdefault("registries", {})
                entry = registries.setdefault(registry, {})
                entry["last_error"] = str(exc)
        _write_state(state)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.run_once()
            self._stop_event.wait(ROTATION_LOOP_SECONDS)


_manager: Optional[SheetRotationManager] = None


def get_sheet_rotation_manager() -> SheetRotationManager:
    global _manager
    if _manager is None:
        _manager = SheetRotationManager()
    return _manager
