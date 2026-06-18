"""
Auto-scaler for the maps scraper worker count.

The scaler now separates two kinds of decisions:

1. Emergency brakes during a live run.
   These are immediate and only used for hard safety signals such as captcha
   pressure or host saturation.

2. Next-run planning after a run finishes.
   Worker increases or non-emergency decreases are based on the completed run,
   using P75 host pressure across the run instead of reacting to a short early
   snapshot.

State file: controller/auto_scale_state.json
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_DASHBOARD_DIR = Path(__file__).parent
_PROJECT_ROOT = _DASHBOARD_DIR.parent
_BOTS_DIR = _PROJECT_ROOT / "Bots"
if str(_BOTS_DIR) not in sys.path:
    sys.path.insert(0, str(_BOTS_DIR))

AUTO_SCALE_STATE_FILE = _PROJECT_ROOT / "controller" / "auto_scale_state.json"
CHECK_INTERVAL_SECONDS: int = 60
MIN_RUN_MINUTES_FOR_PLANNING: int = 30
MAX_RUN_SAMPLES: int = 720
MAX_WORKERS: int = 16
MIN_WORKERS: int = 1
CHANGE_FACTOR: float = 0.20

# Thresholds — keep aligned with pipeline_status.py
_HIGH_YIELD = 220.0
_HIGH_WEBSITE_RATE = 75.0
_LOW_WEBSITE_RATE = 55.0
_MAX_SAFE_ERROR_RATE = 1.0
_HIGH_MEMORY_PRESSURE_PERCENT = 93.0
_TARGET_INCREASE_MEMORY_PRESSURE_PERCENT = 85.0
_HIGH_CPU_PERCENT = 95.0
_TARGET_INCREASE_CPU_PERCENT = 88.0
_EMERGENCY_MEMORY_PRESSURE_PERCENT = 97.0
_EMERGENCY_MEMORY_CONFIRM_PERCENT = 95.0
_EMERGENCY_MEMORY_CONFIRM_HITS = 2
_EMERGENCY_MEMORY_CONFIRM_WINDOW_MINUTES = 5
_EMERGENCY_COOLDOWN_MINUTES = 15


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def _minutes_between(start_raw: Any, end_raw: Any) -> Optional[float]:
    start = _parse_iso(start_raw)
    end = _parse_iso(end_raw)
    if not start or not end:
        return None
    try:
        return max(0.0, (end - start).total_seconds() / 60.0)
    except Exception:
        return None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _get_process_manager():
    try:
        from dashboard.process import get_process_manager
    except Exception:
        from process import get_process_manager  # type: ignore
    return get_process_manager()


def _get_scheduler():
    try:
        from dashboard.scheduler import get_scheduler
    except Exception:
        from scheduler import get_scheduler  # type: ignore
    return get_scheduler()


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


def _read_health_payload() -> Optional[Dict[str, Any]]:
    try:
        from maps_scraper.config import SCRAPER_HEALTH_FILE
        health_path = Path(SCRAPER_HEALTH_FILE) if SCRAPER_HEALTH_FILE else None
    except ImportError:
        return None

    if not health_path or not health_path.exists():
        return None

    try:
        data = json.loads(health_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _normalize_health(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = data or {}
    memory_pressure_pct = _as_float(data.get("memory_pressure_pct"))
    if memory_pressure_pct is None:
        memory_pressure_pct = _as_float(data.get("memory_percent"))

    memory_available_pct = _as_float(data.get("memory_available_pct"))
    if memory_available_pct is None and memory_pressure_pct is not None:
        memory_available_pct = round(max(0.0, 100.0 - memory_pressure_pct), 1)

    run_searches = _as_int(data.get("run_searches"))
    run_leads = _as_int(data.get("run_leads"))
    run_batches = _as_int(data.get("run_batches"))
    run_website_found = _as_int(data.get("run_website_found"))
    run_website_total = _as_int(data.get("run_website_total"))
    error_count = _as_int(data.get("error_count"))
    captcha_count = _as_int(data.get("captcha_count"))

    leads_per_100 = (float(run_leads) / run_searches * 100.0) if run_searches > 0 else None
    website_rate = (
        float(run_website_found) / run_website_total * 100.0
        if run_website_total > 0
        else None
    )
    error_rate = (float(error_count) / run_searches * 100.0) if run_searches > 0 else None

    started_at = data.get("started_at")
    ended_at = data.get("ended_at")

    return {
        "run_started_at": started_at,
        "run_ended_at": ended_at,
        "is_running": bool(started_at and not ended_at),
        "current_phase": data.get("current_phase"),
        "run_searches": run_searches,
        "run_leads": run_leads,
        "run_batches": run_batches,
        "run_website_found": run_website_found,
        "run_website_total": run_website_total,
        "captcha_count": captcha_count,
        "error_count": error_count,
        "error_rate": error_rate,
        "website_rate": website_rate,
        "leads_per_100": leads_per_100,
        "compute_utilization_pct": _as_float(data.get("compute_utilization_pct")),
        "cpu_percent": _as_float(data.get("cpu_percent")),
        "memory_pressure_pct": memory_pressure_pct,
        "memory_available_pct": memory_available_pct,
    }


def _percentile(values: list[float], pct: float) -> Optional[float]:
    clean = sorted(float(v) for v in values if v is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return round(clean[0], 1)
    pos = (len(clean) - 1) * pct
    low = int(pos)
    high = min(low + 1, len(clean) - 1)
    fraction = pos - low
    value = clean[low] + (clean[high] - clean[low]) * fraction
    return round(value, 1)


def _compute_action(metrics: Optional[Dict[str, Any]] = None) -> str:
    """Return an immediate live-run brake action: "decrease" | "none"."""
    metrics = metrics or _normalize_health(_read_health_payload())
    if not metrics["is_running"]:
        return "none"

    if (
        metrics["memory_pressure_pct"] is not None
        and metrics["memory_pressure_pct"] >= _HIGH_MEMORY_PRESSURE_PERCENT
    ):
        return "decrease"

    if metrics["captcha_count"] > 0:
        return "decrease"

    if metrics["cpu_percent"] is not None and metrics["cpu_percent"] >= _HIGH_CPU_PERCENT:
        return "decrease"

    return "none"


def _remember_memory_pressure_hit(state: Dict[str, Any], metrics: Dict[str, Any]) -> Tuple[bool, int]:
    changed = False
    run_started_at = metrics.get("run_started_at")
    if not run_started_at or not metrics.get("is_running"):
        if state.get("emergency_monitor") is not None:
            state["emergency_monitor"] = None
            return True, 0
        return False, 0

    monitor = state.get("emergency_monitor")
    if not isinstance(monitor, dict) or monitor.get("run_started_at") != run_started_at:
        monitor = {
            "run_started_at": run_started_at,
            "memory_confirm_hits": [],
        }
        state["emergency_monitor"] = monitor
        changed = True

    hits = [
        stamp
        for stamp in monitor.get("memory_confirm_hits", [])
        if _minutes_between(stamp, _now().isoformat()) is not None
        and (_minutes_between(stamp, _now().isoformat()) or 0.0) <= _EMERGENCY_MEMORY_CONFIRM_WINDOW_MINUTES
    ]
    if hits != monitor.get("memory_confirm_hits", []):
        monitor["memory_confirm_hits"] = hits
        changed = True

    memory_pressure = _as_float(metrics.get("memory_pressure_pct"))
    if memory_pressure is not None and memory_pressure >= _EMERGENCY_MEMORY_CONFIRM_PERCENT:
        now_iso = _now().isoformat()
        hits.append(now_iso)
        monitor["memory_confirm_hits"] = hits
        changed = True

    return changed, len(hits)


def _sync_emergency_cooldown(state: Dict[str, Any], metrics: Dict[str, Any]) -> bool:
    cooldown = state.get("emergency_cooldown")
    if not isinstance(cooldown, dict):
        return False

    current_run_started_at = metrics.get("run_started_at")
    if metrics.get("is_running") and current_run_started_at and current_run_started_at != cooldown.get("run_started_at"):
        state["emergency_cooldown"] = None
        return True

    if _manual_change_after_run_start(state, cooldown.get("run_started_at")):
        state["emergency_cooldown"] = None
        return True

    return False


def _evaluate_emergency_stop(state: Dict[str, Any], metrics: Dict[str, Any]) -> Tuple[bool, Optional[str], bool]:
    changed, memory_hit_count = _remember_memory_pressure_hit(state, metrics)
    if not metrics.get("is_running"):
        return False, None, changed

    memory_pressure = _as_float(metrics.get("memory_pressure_pct"))
    if memory_pressure is None:
        return False, None, changed

    run_started_at = metrics.get("run_started_at")
    if state.get("last_emergency_run_started_at") == run_started_at:
        return False, None, changed

    if memory_pressure >= _EMERGENCY_MEMORY_PRESSURE_PERCENT:
        return True, "ram_extreme_single_hit", changed

    if memory_hit_count >= _EMERGENCY_MEMORY_CONFIRM_HITS:
        return True, "ram_critical_confirmed", changed

    return False, None, changed


def _compute_new_workers(current: int, action: str) -> int:
    if action == "increase":
        new = round(current * (1 + CHANGE_FACTOR))
        return max(MIN_WORKERS, min(MAX_WORKERS, max(new, current + 1)))
    if action == "decrease":
        new = round(current * (1 - CHANGE_FACTOR))
        return max(MIN_WORKERS, min(current - 1, new))
    return current


def _append_run_sample(active_run: Dict[str, Any], metrics: Dict[str, Any]) -> None:
    for sample_key, metric_key in (
        ("compute_samples", "compute_utilization_pct"),
        ("cpu_samples", "cpu_percent"),
        ("memory_pressure_samples", "memory_pressure_pct"),
    ):
        samples = active_run.setdefault(sample_key, [])
        value = metrics.get(metric_key)
        if value is not None:
            samples.append(float(value))
            if len(samples) > MAX_RUN_SAMPLES:
                del samples[:-MAX_RUN_SAMPLES]

    active_run["sample_count"] = max(
        len(active_run.get("compute_samples", [])),
        len(active_run.get("cpu_samples", [])),
        len(active_run.get("memory_pressure_samples", [])),
    )
    active_run["last_seen_at"] = _now().isoformat()


def _summarize_run(active_run: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
    summary = {
        "run_started_at": metrics.get("run_started_at"),
        "run_ended_at": metrics.get("run_ended_at"),
        "runtime_minutes": _minutes_between(
            metrics.get("run_started_at"),
            metrics.get("run_ended_at"),
        ),
        "sample_count": active_run.get("sample_count", 0),
        "run_searches": metrics.get("run_searches"),
        "run_batches": metrics.get("run_batches"),
        "run_leads": metrics.get("run_leads"),
        "leads_per_100": metrics.get("leads_per_100"),
        "website_rate": metrics.get("website_rate"),
        "captcha_count": metrics.get("captcha_count"),
        "error_count": metrics.get("error_count"),
        "error_rate": metrics.get("error_rate"),
        "compute_p75": _percentile(active_run.get("compute_samples", []), 0.75),
        "cpu_p75": _percentile(active_run.get("cpu_samples", []), 0.75),
        "memory_pressure_p75": _percentile(
            active_run.get("memory_pressure_samples", []),
            0.75,
        ),
    }
    return summary


def _recommend_run_action(summary: Dict[str, Any]) -> Tuple[str, str]:
    runtime_minutes = _as_float(summary.get("runtime_minutes")) or 0.0
    sample_count = _as_int(summary.get("sample_count"))
    run_searches = _as_int(summary.get("run_searches"))
    run_batches = _as_int(summary.get("run_batches"))
    captcha_count = _as_int(summary.get("captcha_count"))
    error_count = _as_int(summary.get("error_count"))

    website_rate = _as_float(summary.get("website_rate"))
    leads_per_100 = _as_float(summary.get("leads_per_100"))
    error_rate = _as_float(summary.get("error_rate"))
    cpu_p75 = _as_float(summary.get("cpu_p75"))
    memory_pressure_p75 = _as_float(summary.get("memory_pressure_p75"))

    if runtime_minutes < MIN_RUN_MINUTES_FOR_PLANNING:
        return "none", "run_too_short"
    if sample_count < 5 or run_searches < 20 or run_batches < 1:
        return "none", "not_enough_data"

    if captcha_count > 0:
        return "decrease", "captcha_seen"

    if error_count >= 5:
        return "none", "error_spike_needs_review"

    if (
        memory_pressure_p75 is not None
        and memory_pressure_p75 >= _HIGH_MEMORY_PRESSURE_PERCENT
    ):
        return "decrease", "ram_pressure_p75_high"

    if cpu_p75 is not None and cpu_p75 >= _HIGH_CPU_PERCENT:
        return "decrease", "cpu_p75_high"

    if website_rate is not None and website_rate < _LOW_WEBSITE_RATE:
        return "decrease", "website_rate_low"

    cpu_headroom = cpu_p75 is None or cpu_p75 <= _TARGET_INCREASE_CPU_PERCENT
    memory_headroom = (
        memory_pressure_p75 is None
        or memory_pressure_p75 <= _TARGET_INCREASE_MEMORY_PRESSURE_PERCENT
    )

    if (
        cpu_headroom
        and memory_headroom
        and error_rate is not None
        and error_rate <= _MAX_SAFE_ERROR_RATE
        and website_rate is not None
        and website_rate >= _HIGH_WEBSITE_RATE
    ):
        return "increase", "completed_run_headroom"

    if (
        cpu_p75 is None
        and memory_pressure_p75 is None
        and error_rate is not None
        and error_rate <= _MAX_SAFE_ERROR_RATE
        and website_rate is not None
        and website_rate >= _HIGH_WEBSITE_RATE
        and (leads_per_100 or 0.0) >= _HIGH_YIELD
    ):
        return "increase", "fallback_yield_and_website"

    return "none", "steady_state"


def _auto_already_applied_for_run(state: Dict[str, Any], run_started_at: Optional[str]) -> bool:
    if not run_started_at:
        return False
    return state.get("last_auto_run_started_at") == run_started_at


def _manual_change_after_run_start(state: Dict[str, Any], run_started_at: Optional[str]) -> bool:
    manual_at = _parse_iso(state.get("last_manual_change_at"))
    run_started = _parse_iso(run_started_at)
    if not manual_at or not run_started:
        return False
    return manual_at >= run_started


def _sync_run_tracking(state: Dict[str, Any], metrics: Dict[str, Any]) -> bool:
    changed = False
    run_started_at = metrics.get("run_started_at")
    if not run_started_at:
        if state.get("active_run"):
            state["active_run"] = None
            changed = True
        return changed

    active_run = state.get("active_run")
    if not isinstance(active_run, dict) or active_run.get("run_started_at") != run_started_at:
        active_run = {
            "run_started_at": run_started_at,
            "first_seen_at": _now().isoformat(),
            "last_seen_at": _now().isoformat(),
            "compute_samples": [],
            "cpu_samples": [],
            "memory_pressure_samples": [],
            "sample_count": 0,
        }
        state["active_run"] = active_run
        changed = True

    if metrics.get("is_running"):
        if metrics.get("run_searches", 0) > 0 or any(
            metrics.get(key) is not None
            for key in ("compute_utilization_pct", "cpu_percent", "memory_pressure_pct")
        ):
            _append_run_sample(active_run, metrics)
            changed = True
        return changed

    if state.get("last_finalized_run_started_at") == run_started_at:
        if state.get("active_run") is not None:
            state["active_run"] = None
            changed = True
        return changed

    summary = _summarize_run(active_run, metrics)
    state["last_run_summary"] = summary
    state["last_finalized_run_started_at"] = run_started_at
    state["active_run"] = None
    changed = True

    action, reason = _recommend_run_action(summary)
    if _manual_change_after_run_start(state, run_started_at):
        state["pending_recommendation"] = None
        return changed

    if action != "none" and not _auto_already_applied_for_run(state, run_started_at):
        state["pending_recommendation"] = {
            "action": action,
            "reason": reason,
            "source": "completed_run",
            "created_at": _now().isoformat(),
            **summary,
        }
    else:
        state["pending_recommendation"] = None
    return changed


def _apply_action(
    state: Dict[str, Any],
    action: str,
    *,
    source: str,
    reason: str,
    run_started_at: Optional[str],
) -> Dict[str, Any]:
    try:
        from maps_scraper.config import get_dashboard_config, save_dashboard_config
        cfg = get_dashboard_config()
    except Exception as exc:
        return {"applied": False, "reason": f"config_read_error: {exc}"}

    current_workers = int(cfg.get("runtime", {}).get("num_workers") or 4)
    new_workers = _compute_new_workers(current_workers, action)

    if new_workers == current_workers:
        state["pending_recommendation"] = None
        _write_state(state)
        return {
            "applied": False,
            "reason": "already_at_boundary",
            "workers": current_workers,
            "action": action,
        }

    try:
        payload = {**cfg, "runtime": {**cfg.get("runtime", {}), "num_workers": new_workers}}
        save_dashboard_config(payload)
    except Exception as exc:
        return {"applied": False, "reason": f"save_error: {exc}"}

    now_iso = _now().isoformat()
    state["last_auto_apply_at"] = now_iso
    state["last_auto_applied_workers"] = new_workers
    state["last_auto_from_workers"] = current_workers
    state["last_auto_action"] = action
    state["last_auto_reason"] = reason
    state["last_auto_source"] = source
    state["last_auto_run_started_at"] = run_started_at
    state["pending_recommendation"] = None
    _write_state(state)

    print(
        f"[AutoScale] Applied: {action} {current_workers} → {new_workers} workers "
        f"(source={source}, reason={reason})"
    )
    return {
        "applied": True,
        "action": action,
        "from_workers": current_workers,
        "new_workers": new_workers,
        "source": source,
        "reason": reason,
    }


def record_manual_change() -> None:
    state = _read_state()
    state["last_manual_change_at"] = _now().isoformat()
    state["pending_recommendation"] = None
    state["emergency_cooldown"] = None
    _write_state(state)
    print("[AutoScale] Manual change recorded — cleared pending auto-scale plan.")


def _trigger_emergency_cooldown(
    state: Dict[str, Any],
    metrics: Dict[str, Any],
    *,
    reason: str,
) -> Dict[str, Any]:
    run_started_at = metrics.get("run_started_at")
    decrease_result = None
    if not _auto_already_applied_for_run(state, run_started_at):
        decrease_result = _apply_action(
            state,
            "decrease",
            source="live_brake",
            reason=reason,
            run_started_at=run_started_at,
        )

    restart_at = (_now() + timedelta(minutes=_EMERGENCY_COOLDOWN_MINUTES)).isoformat()
    scheduler = _get_scheduler()
    scheduler_enabled = bool(scheduler.is_enabled())

    state["last_emergency_run_started_at"] = run_started_at
    state["last_emergency_reason"] = reason
    state["last_emergency_at"] = _now().isoformat()
    state["emergency_cooldown"] = {
        "run_started_at": run_started_at,
        "reason": reason,
        "triggered_at": _now().isoformat(),
        "restart_at": restart_at,
        "cooldown_minutes": _EMERGENCY_COOLDOWN_MINUTES,
        "scheduler_managed": scheduler_enabled,
    }

    if scheduler_enabled:
        cooldown_result = scheduler.request_emergency_cooldown(
            _EMERGENCY_COOLDOWN_MINUTES,
            reason=reason,
        )
        if cooldown_result.get("next_transition"):
            state["emergency_cooldown"]["restart_at"] = cooldown_result["next_transition"]
    else:
        cooldown_result = _get_process_manager().stop_scraper_only()

    _write_state(state)
    print(
        "[AutoScale] Emergency cooldown triggered "
        f"(reason={reason}, restart_at={state['emergency_cooldown']['restart_at']})"
    )
    return {
        "applied": bool(decrease_result and decrease_result.get("applied")),
        "action": "decrease",
        "reason": reason,
        "source": "emergency_cooldown",
        "cooldown": state["emergency_cooldown"],
        "stop_details": cooldown_result,
        "scale_details": decrease_result,
    }


def _maybe_restart_after_cooldown(
    state: Dict[str, Any],
    metrics: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    cooldown = state.get("emergency_cooldown")
    if not isinstance(cooldown, dict):
        return None

    if cooldown.get("scheduler_managed"):
        return None

    if metrics.get("is_running"):
        return None

    restart_at = _parse_iso(cooldown.get("restart_at"))
    if not restart_at or _now() < restart_at:
        return None

    pm = _get_process_manager()
    if pm.is_controller_running():
        return None

    result = pm.start()
    if result.get("success"):
        state["last_emergency_restart_at"] = _now().isoformat()
        state["emergency_cooldown"] = None
        _write_state(state)
        print("[AutoScale] Restarted scraper after emergency cooldown.")
        return {
            "applied": False,
            "reason": "restarted_after_emergency_cooldown",
            "restart_result": result,
        }
    return {
        "applied": False,
        "reason": "emergency_restart_failed",
        "restart_result": result,
    }


def check_and_apply() -> Dict[str, Any]:
    state = _read_state()
    metrics = _normalize_health(_read_health_payload())
    state_changed = _sync_run_tracking(state, metrics)
    state_changed = _sync_emergency_cooldown(state, metrics) or state_changed

    emergency_stop, emergency_reason, emergency_changed = _evaluate_emergency_stop(state, metrics)
    state_changed = emergency_changed or state_changed
    if emergency_stop and metrics.get("is_running"):
        return _trigger_emergency_cooldown(
            state,
            metrics,
            reason=str(emergency_reason or "ram_emergency"),
        )

    live_action = _compute_action(metrics)
    if live_action == "decrease" and metrics.get("is_running"):
        if not _auto_already_applied_for_run(state, metrics.get("run_started_at")):
            return _apply_action(
                state,
                live_action,
                source="live_brake",
                reason="host_or_captcha_emergency",
                run_started_at=metrics.get("run_started_at"),
            )
        if state_changed:
            _write_state(state)
        return {
            "applied": False,
            "reason": "live_brake_already_applied_for_run",
            "action": live_action,
        }

    restart_result = _maybe_restart_after_cooldown(state, metrics)
    if restart_result is not None:
        return restart_result

    pending = state.get("pending_recommendation")
    if isinstance(pending, dict) and pending.get("action") in {"increase", "decrease"}:
        pending_run_started_at = pending.get("run_started_at")
        if _manual_change_after_run_start(state, pending_run_started_at):
            state["pending_recommendation"] = None
            _write_state(state)
            return {
                "applied": False,
                "reason": "manual_change_superseded_completed_run_plan",
            }

        if _auto_already_applied_for_run(state, pending_run_started_at):
            state["pending_recommendation"] = None
            _write_state(state)
            return {"applied": False, "reason": "run_already_auto_scaled"}

        return _apply_action(
            state,
            pending["action"],
            source=str(pending.get("source") or "completed_run"),
            reason=str(pending.get("reason") or "completed_run_signal"),
            run_started_at=pending_run_started_at,
        )

    if state_changed:
        _write_state(state)

    collecting_run = bool(metrics.get("is_running") and metrics.get("run_started_at"))
    return {
        "applied": False,
        "reason": "collecting_run_telemetry" if collecting_run else "no_actionable_recommendation",
    }


def get_auto_scale_status() -> Dict[str, Any]:
    state = _read_state()
    metrics = _normalize_health(_read_health_payload())
    pending = state.get("pending_recommendation") if isinstance(state.get("pending_recommendation"), dict) else None
    emergency_cooldown = state.get("emergency_cooldown") if isinstance(state.get("emergency_cooldown"), dict) else None

    action = pending.get("action") if pending else "none"
    note: Optional[str] = None

    if emergency_cooldown:
        restart_at = emergency_cooldown.get("restart_at")
        note = (
            "Emergency cooldown is active after critical host pressure. "
            f"Restart target: {restart_at}."
        )
    elif pending:
        action_label = "increase workers ~20%" if action == "increase" else "reduce workers ~20%"
        runtime = pending.get("runtime_minutes")
        runtime_text = f"{round(float(runtime), 1)} min" if runtime is not None else "full run"
        note = f"Auto-scale: last completed run recommends {action_label}. This plan came from {runtime_text} of telemetry and will be applied on the next check."
    elif metrics.get("is_running"):
        note = (
            "Auto-scale is observing the current run. Worker planning now happens "
            "after the run finishes; only emergency brakes apply mid-run."
        )

    return {
        "enabled": True,
        "action": action or "none",
        "source": pending.get("source") if pending else None,
        "note": note,
        "collecting_run": bool(metrics.get("is_running")),
        "current_run_started_at": metrics.get("run_started_at"),
        "minimum_runtime_minutes": MIN_RUN_MINUTES_FOR_PLANNING,
        "stable_minutes": None,
        "minutes_left": None,
        "stable_threshold_minutes": MIN_RUN_MINUTES_FOR_PLANNING,
        "manual_blocking": False,
        "last_manual_change_at": state.get("last_manual_change_at"),
        "last_auto_apply_at": state.get("last_auto_apply_at"),
        "last_auto_applied_workers": state.get("last_auto_applied_workers"),
        "last_auto_from_workers": state.get("last_auto_from_workers"),
        "last_auto_action": state.get("last_auto_action"),
        "last_auto_reason": state.get("last_auto_reason"),
        "last_auto_source": state.get("last_auto_source"),
        "last_emergency_at": state.get("last_emergency_at"),
        "last_emergency_reason": state.get("last_emergency_reason"),
        "last_emergency_restart_at": state.get("last_emergency_restart_at"),
        "emergency_cooldown": emergency_cooldown,
        "last_run_summary": state.get("last_run_summary"),
    }


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
        "[AutoScale] Background loop started "
        f"(check every {CHECK_INTERVAL_SECONDS}s, run planning threshold = {MIN_RUN_MINUTES_FOR_PLANNING}min)"
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
