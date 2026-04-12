"""
Auto-scaler for the maps scraper worker count.

After STABLE_MINUTES of continuous "Safe to increase" or "Reduce aggression"
health signal with no manual config changes, autonomously applies a 20%
worker count adjustment and records the action to state.

State file: controller/auto_scale_state.json
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_DASHBOARD_DIR = Path(__file__).parent
_PROJECT_ROOT = _DASHBOARD_DIR.parent
_BOTS_DIR = _PROJECT_ROOT / "Bots"
if str(_BOTS_DIR) not in sys.path:
    sys.path.insert(0, str(_BOTS_DIR))

AUTO_SCALE_STATE_FILE = _PROJECT_ROOT / "controller" / "auto_scale_state.json"
STABLE_MINUTES: int = 5          # recommendation must hold this long before acting
CHECK_INTERVAL_SECONDS: int = 60   # background loop cadence
MAX_WORKERS: int = 16
MIN_WORKERS: int = 1
CHANGE_FACTOR: float = 0.20      # 20% step

# Health thresholds — kept in sync with pipeline_status.py
_HIGH_YIELD = 220.0
_LOW_YIELD = 120.0
_HIGH_WEBSITE_RATE = 75.0
_LOW_WEBSITE_RATE = 55.0


# =============================================================================
# State I/O
# =============================================================================

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_state() -> Dict[str, Any]:
    if not AUTO_SCALE_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(AUTO_SCALE_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(state: Dict[str, Any]) -> None:
    AUTO_SCALE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AUTO_SCALE_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(AUTO_SCALE_STATE_FILE)


# =============================================================================
# Health signal
# =============================================================================

def _compute_action() -> str:
    """
    Read the scraper health file and return the canonical scaling action:
    "increase" | "decrease" | "none"
    """
    try:
        from maps_scraper.config import SCRAPER_HEALTH_FILE
        health_path = Path(SCRAPER_HEALTH_FILE) if SCRAPER_HEALTH_FILE else None
    except ImportError:
        return "none"

    if not health_path or not health_path.exists():
        return "none"

    try:
        data: Dict[str, Any] = json.loads(health_path.read_text(encoding="utf-8"))
    except Exception:
        return "none"

    run_searches = int(data.get("run_searches") or 0)
    run_batches = int(data.get("run_batches") or 0)
    run_leads = data.get("run_leads")
    run_website_found = data.get("run_website_found")
    run_website_total = data.get("run_website_total")
    captcha_count = int(data.get("captcha_count") or 0)
    error_count = int(data.get("error_count") or 0)

    # Not enough data yet
    if run_searches < 20 or run_batches < 1:
        return "none"

    if captcha_count > 0:
        return "decrease"

    # Error spike → human should inspect, not auto-scale
    if error_count >= 5:
        return "none"

    leads_per_100: Optional[float] = None
    if run_searches > 0 and run_leads is not None:
        leads_per_100 = (float(run_leads) / run_searches) * 100

    website_rate: Optional[float] = None
    if run_website_total and run_website_total > 0 and run_website_found is not None:
        website_rate = (float(run_website_found) / float(run_website_total)) * 100

    if website_rate is None:
        return "none"

    if website_rate >= _HIGH_WEBSITE_RATE and (leads_per_100 or 0) >= _HIGH_YIELD:
        return "increase"
    if website_rate < _LOW_WEBSITE_RATE or (leads_per_100 or 0) < _LOW_YIELD:
        return "decrease"
    return "none"


# =============================================================================
# Worker count arithmetic
# =============================================================================

def _compute_new_workers(current: int, action: str) -> int:
    if action == "increase":
        new = round(current * (1 + CHANGE_FACTOR))
        # Always move at least 1 step up
        return max(MIN_WORKERS, min(MAX_WORKERS, max(new, current + 1)))
    if action == "decrease":
        new = round(current * (1 - CHANGE_FACTOR))
        # Always move at least 1 step down
        return max(MIN_WORKERS, min(current - 1, new))
    return current


# =============================================================================
# Public API
# =============================================================================

def record_manual_change() -> None:
    """
    Call this whenever a user saves scraper config manually via the dashboard.
    Resets the stability clock so we wait another 30 min before auto-acting.
    """
    state = _read_state()
    state["last_manual_change_at"] = _now().isoformat()
    # Reset tracking so the 30-min window restarts
    state["recommendation"] = None
    state["recommendation_first_seen_at"] = None
    _write_state(state)
    print("[AutoScale] Manual change recorded — stability clock reset.")


def check_and_apply() -> Dict[str, Any]:
    """
    Single check-and-apply pass. Returns a dict describing what happened.
    Called by the background loop every CHECK_INTERVAL_SECONDS.
    """
    now = _now()
    state = _read_state()

    # --- Guard: manual change too recent ---
    last_manual_raw = state.get("last_manual_change_at")
    if last_manual_raw:
        try:
            elapsed = (now - datetime.fromisoformat(last_manual_raw)).total_seconds() / 60
            if elapsed < STABLE_MINUTES:
                return {
                    "applied": False,
                    "reason": "manual_too_recent",
                    "minutes_left": round(STABLE_MINUTES - elapsed, 1),
                }
        except Exception:
            pass

    # --- Compute current health signal ---
    action = _compute_action()

    if action == "none":
        if state.get("recommendation") != "none":
            state["recommendation"] = "none"
            state["recommendation_first_seen_at"] = None
            _write_state(state)
        return {"applied": False, "reason": "no_actionable_recommendation"}

    # --- Track stability: reset clock if recommendation changed ---
    if state.get("recommendation") != action:
        state["recommendation"] = action
        state["recommendation_first_seen_at"] = now.isoformat()
        _write_state(state)
        return {
            "applied": False,
            "reason": "recommendation_changed",
            "action": action,
            "minutes_left": STABLE_MINUTES,
        }

    first_seen_raw = state.get("recommendation_first_seen_at")
    if not first_seen_raw:
        state["recommendation_first_seen_at"] = now.isoformat()
        _write_state(state)
        return {"applied": False, "reason": "started_tracking", "action": action, "minutes_left": STABLE_MINUTES}

    try:
        stable_minutes = (now - datetime.fromisoformat(first_seen_raw)).total_seconds() / 60
    except Exception:
        state["recommendation_first_seen_at"] = now.isoformat()
        _write_state(state)
        return {"applied": False, "reason": "invalid_timestamp"}

    if stable_minutes < STABLE_MINUTES:
        return {
            "applied": False,
            "reason": "waiting_for_stability",
            "action": action,
            "stable_minutes": round(stable_minutes, 1),
            "minutes_left": round(STABLE_MINUTES - stable_minutes, 1),
        }

    # --- Guard: don't re-apply within the same stability window ---
    last_auto_raw = state.get("last_auto_apply_at")
    if last_auto_raw:
        try:
            auto_elapsed = (now - datetime.fromisoformat(last_auto_raw)).total_seconds() / 60
            if auto_elapsed < STABLE_MINUTES:
                return {"applied": False, "reason": "auto_applied_too_recently", "action": action}
        except Exception:
            pass

    # --- Read config, compute new count, apply ---
    try:
        from maps_scraper.config import get_dashboard_config, save_dashboard_config
        cfg = get_dashboard_config()
    except Exception as exc:
        return {"applied": False, "reason": f"config_read_error: {exc}"}

    current_workers = int(cfg.get("runtime", {}).get("num_workers") or 4)
    new_workers = _compute_new_workers(current_workers, action)

    if new_workers == current_workers:
        # Already at hard boundary — reset clock so we don't spin here forever
        state["recommendation_first_seen_at"] = now.isoformat()
        _write_state(state)
        return {"applied": False, "reason": "already_at_boundary", "workers": current_workers, "action": action}

    try:
        payload = {**cfg, "runtime": {**cfg.get("runtime", {}), "num_workers": new_workers}}
        save_dashboard_config(payload)
    except Exception as exc:
        return {"applied": False, "reason": f"save_error: {exc}"}

    state["last_auto_apply_at"] = now.isoformat()
    state["last_auto_applied_workers"] = new_workers
    state["last_auto_from_workers"] = current_workers
    state["last_auto_action"] = action
    # Reset stability clock — next auto-action needs another 30 min of stability
    state["recommendation_first_seen_at"] = now.isoformat()
    _write_state(state)

    print(
        f"[AutoScale] Applied: {action} {current_workers} → {new_workers} workers "
        f"(stable for {stable_minutes:.1f}min)"
    )
    return {
        "applied": True,
        "action": action,
        "from_workers": current_workers,
        "new_workers": new_workers,
        "stable_minutes": round(stable_minutes, 1),
    }


def get_auto_scale_status() -> Dict[str, Any]:
    """Return auto-scale tracking state for the dashboard status API."""
    state = _read_state()
    now = _now()

    action = state.get("recommendation") or "none"
    first_seen_raw = state.get("recommendation_first_seen_at")
    last_manual_raw = state.get("last_manual_change_at")
    last_auto_raw = state.get("last_auto_apply_at")

    stable_minutes: Optional[float] = None
    minutes_left: Optional[float] = None
    manual_blocking = False

    if first_seen_raw and action != "none":
        try:
            stable_minutes = round(
                (now - datetime.fromisoformat(first_seen_raw)).total_seconds() / 60, 1
            )
            minutes_left = max(0.0, round(STABLE_MINUTES - stable_minutes, 1))
        except Exception:
            pass

    if last_manual_raw:
        try:
            manual_elapsed = (now - datetime.fromisoformat(last_manual_raw)).total_seconds() / 60
            manual_blocking = manual_elapsed < STABLE_MINUTES
        except Exception:
            pass

    return {
        "enabled": True,
        "action": action,                                          # "increase" | "decrease" | "none"
        "stable_minutes": stable_minutes,                         # how long current signal has held
        "minutes_left": minutes_left,                             # minutes until auto-apply fires
        "stable_threshold_minutes": STABLE_MINUTES,
        "manual_blocking": manual_blocking,                       # True if manual change is still cooling down
        "last_manual_change_at": last_manual_raw,
        "last_auto_apply_at": last_auto_raw,
        "last_auto_applied_workers": state.get("last_auto_applied_workers"),
        "last_auto_from_workers": state.get("last_auto_from_workers"),
        "last_auto_action": state.get("last_auto_action"),
    }


# =============================================================================
# Background loop
# =============================================================================

_bg_thread: Optional[threading.Thread] = None
_bg_stop: Optional[threading.Event] = None


def start_background_loop() -> None:
    global _bg_thread, _bg_stop
    if _bg_thread and _bg_thread.is_alive():
        return
    _bg_stop = threading.Event()
    _bg_thread = threading.Thread(target=_loop, daemon=True, name="auto-scaler")
    _bg_thread.start()
    print(
        f"[AutoScale] Background loop started "
        f"(check every {CHECK_INTERVAL_SECONDS}s, stable threshold = {STABLE_MINUTES}min)"
    )


def stop_background_loop() -> None:
    global _bg_stop
    if _bg_stop:
        _bg_stop.set()


def _loop() -> None:
    while _bg_stop and not _bg_stop.is_set():
        try:
            result = check_and_apply()
            if result.get("applied"):
                print(f"[AutoScale] Auto-scale applied: {result}")
        except Exception as exc:
            print(f"[AutoScale] Loop error: {exc}")
        _bg_stop.wait(timeout=CHECK_INTERVAL_SECONDS)
