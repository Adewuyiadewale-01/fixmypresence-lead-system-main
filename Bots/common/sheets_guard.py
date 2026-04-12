from __future__ import annotations

import json
import os
import random
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Literal, Optional, TypeVar

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover
    fcntl = None

OperationKind = Literal["read", "write"]
T = TypeVar("T")

_ROOT_DIR = Path(__file__).resolve().parents[2]
_STATE_DIR = _ROOT_DIR / "controller"
_RATE_STATE_FILE = _STATE_DIR / "sheets_rate_gate_state.json"
_RATE_LOCK_FILE = _STATE_DIR / "sheets_rate_gate.lock"

_DEFAULT_READ_LIMIT_PER_MINUTE = 45
_DEFAULT_WRITE_LIMIT_PER_MINUTE = 45
_DEFAULT_RETRY_ATTEMPTS = 5
_DEFAULT_BACKOFF_SECONDS = 1.5
_DEFAULT_MAX_BACKOFF_SECONDS = 30.0
_WINDOW_SECONDS = 60.0


def _rate_limit_for(kind: OperationKind) -> int:
    env_name = "SHEETS_WRITE_LIMIT_PER_MINUTE" if kind == "write" else "SHEETS_READ_LIMIT_PER_MINUTE"
    fallback = _DEFAULT_WRITE_LIMIT_PER_MINUTE if kind == "write" else _DEFAULT_READ_LIMIT_PER_MINUTE
    try:
        return max(1, int(os.environ.get(env_name, fallback)))
    except (TypeError, ValueError):
        return fallback


def _retry_attempts() -> int:
    try:
        return max(1, int(os.environ.get("SHEETS_RETRY_ATTEMPTS", _DEFAULT_RETRY_ATTEMPTS)))
    except (TypeError, ValueError):
        return _DEFAULT_RETRY_ATTEMPTS


def _base_backoff_seconds() -> float:
    try:
        return max(0.25, float(os.environ.get("SHEETS_RETRY_BASE_SECONDS", _DEFAULT_BACKOFF_SECONDS)))
    except (TypeError, ValueError):
        return _DEFAULT_BACKOFF_SECONDS


def _max_backoff_seconds() -> float:
    try:
        return max(_base_backoff_seconds(), float(os.environ.get("SHEETS_RETRY_MAX_SECONDS", _DEFAULT_MAX_BACKOFF_SECONDS)))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_BACKOFF_SECONDS


def _ensure_state_dir() -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def _rate_gate_lock():
    _ensure_state_dir()
    with open(_RATE_LOCK_FILE, "a+", encoding="utf-8") as fh:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield fh
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _load_rate_state() -> Dict[str, Any]:
    if not _RATE_STATE_FILE.exists():
        return {"read": [], "write": [], "telemetry": {}}
    try:
        data = json.loads(_RATE_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"read": [], "write": [], "telemetry": {}}
    if not isinstance(data, dict):
        return {"read": [], "write": [], "telemetry": {}}
    return {
        "read": list(data.get("read") or []),
        "write": list(data.get("write") or []),
        "telemetry": dict(data.get("telemetry") or {}),
    }


def _save_rate_state(state: Dict[str, Any]) -> None:
    _RATE_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _telemetry_with_defaults(telemetry: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data = dict(telemetry or {})
    data.setdefault("retry_events_total", 0)
    data.setdefault("rate_gate_wait_events_total", 0)
    data.setdefault("last_retry_at", None)
    data.setdefault("last_retry_operation", None)
    data.setdefault("last_retry_error", None)
    data.setdefault("last_backoff_seconds", None)
    data.setdefault("backoff_until", None)
    data.setdefault("last_wait_at", None)
    data.setdefault("last_wait_seconds", None)
    data.setdefault("rate_gate_wait_until", None)
    data.setdefault("last_operation_at", None)
    data.setdefault("last_operation_kind", None)
    data.setdefault("last_success_at", None)
    return data


def _update_telemetry(mutator: Callable[[Dict[str, Any]], None]) -> None:
    with _rate_gate_lock():
        state = _load_rate_state()
        telemetry = _telemetry_with_defaults(state.get("telemetry"))
        mutator(telemetry)
        telemetry["updated_at"] = time.time()
        state["telemetry"] = telemetry
        _save_rate_state(state)


def _iso_or_none(value: Any) -> Optional[str]:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def get_sheets_guard_status() -> Dict[str, Any]:
    with _rate_gate_lock():
        state = _load_rate_state()
    telemetry = _telemetry_with_defaults(state.get("telemetry"))
    now = time.time()
    read_bucket = [float(ts) for ts in (state.get("read") or []) if now - float(ts) <= _WINDOW_SECONDS]
    write_bucket = [float(ts) for ts in (state.get("write") or []) if now - float(ts) <= _WINDOW_SECONDS]
    backoff_until = float(telemetry["backoff_until"]) if telemetry.get("backoff_until") is not None else None
    wait_until = float(telemetry["rate_gate_wait_until"]) if telemetry.get("rate_gate_wait_until") is not None else None
    return {
        "read_limit_per_minute": _rate_limit_for("read"),
        "write_limit_per_minute": _rate_limit_for("write"),
        "read_requests_last_minute": len(read_bucket),
        "write_requests_last_minute": len(write_bucket),
        "retry_events_total": int(telemetry.get("retry_events_total") or 0),
        "rate_gate_wait_events_total": int(telemetry.get("rate_gate_wait_events_total") or 0),
        "last_retry_at": _iso_or_none(telemetry.get("last_retry_at")),
        "last_retry_operation": telemetry.get("last_retry_operation"),
        "last_retry_error": telemetry.get("last_retry_error"),
        "last_backoff_seconds": telemetry.get("last_backoff_seconds"),
        "backoff_until": _iso_or_none(telemetry.get("backoff_until")),
        "backoff_active": bool(backoff_until and backoff_until > now),
        "last_wait_at": _iso_or_none(telemetry.get("last_wait_at")),
        "last_wait_seconds": telemetry.get("last_wait_seconds"),
        "rate_gate_wait_until": _iso_or_none(telemetry.get("rate_gate_wait_until")),
        "rate_gate_waiting": bool(wait_until and wait_until > now),
        "last_operation_at": _iso_or_none(telemetry.get("last_operation_at")),
        "last_operation_kind": telemetry.get("last_operation_kind"),
        "last_success_at": _iso_or_none(telemetry.get("last_success_at")),
    }


def _throttle(kind: OperationKind) -> None:
    limit = _rate_limit_for(kind)
    while True:
        now = time.time()
        sleep_for = 0.0
        with _rate_gate_lock():
            state = _load_rate_state()
            bucket = [float(ts) for ts in (state.get(kind) or [])]
            cutoff = now - _WINDOW_SECONDS
            bucket = [ts for ts in bucket if ts >= cutoff]
            if len(bucket) >= limit:
                sleep_for = max((bucket[0] + _WINDOW_SECONDS) - now, 0.25)
                telemetry = _telemetry_with_defaults(state.get("telemetry"))
                telemetry["rate_gate_wait_events_total"] = int(telemetry.get("rate_gate_wait_events_total") or 0) + 1
                telemetry["last_wait_at"] = now
                telemetry["last_wait_seconds"] = round(sleep_for, 3)
                telemetry["rate_gate_wait_until"] = now + sleep_for
                state["telemetry"] = telemetry
                state[kind] = bucket
                _save_rate_state(state)
            else:
                bucket.append(now)
                telemetry = _telemetry_with_defaults(state.get("telemetry"))
                telemetry["last_operation_at"] = now
                telemetry["last_operation_kind"] = kind
                state["telemetry"] = telemetry
                state[kind] = bucket
                _save_rate_state(state)
                return
        time.sleep(sleep_for + random.uniform(0.0, 0.15))


def _status_code_from_exception(exc: Exception) -> Optional[int]:
    response = getattr(exc, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            return status_code
    text = str(exc)
    for code in (429, 500, 502, 503, 504):
        if f"[{code}]" in text or f" {code} " in text:
            return code
    return None


def _is_retryable_sheets_error(exc: Exception) -> bool:
    text = str(exc).lower()
    status_code = _status_code_from_exception(exc)
    if status_code in {429, 500, 502, 503, 504}:
        return True
    return any(
        marker in text
        for marker in (
            "quota exceeded",
            "too many requests",
            "rate limit",
            "resource exhausted",
            "internal error encountered",
            "backend error",
            "try again later",
        )
    )


def guarded_gspread_call(
    fn: Callable[[], T],
    *,
    operation: OperationKind,
    description: str = "Google Sheets call",
    attempts: Optional[int] = None,
) -> T:
    max_attempts = attempts or _retry_attempts()
    base_sleep = _base_backoff_seconds()
    max_sleep = _max_backoff_seconds()
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        _throttle(operation)
        try:
            result = fn()
            _update_telemetry(
                lambda telemetry: telemetry.update(
                    {
                        "last_success_at": time.time(),
                        "last_operation_kind": operation,
                    }
                )
            )
            return result
        except Exception as exc:
            last_exc = exc
            if attempt >= max_attempts or not _is_retryable_sheets_error(exc):
                raise
            sleep_for = min(max_sleep, base_sleep * (2 ** (attempt - 1))) + random.uniform(0.0, 0.75)
            _update_telemetry(
                lambda telemetry: telemetry.update(
                    {
                        "retry_events_total": int(telemetry.get("retry_events_total") or 0) + 1,
                        "last_retry_at": time.time(),
                        "last_retry_operation": operation,
                        "last_retry_error": str(exc),
                        "last_backoff_seconds": round(sleep_for, 3),
                        "backoff_until": time.time() + sleep_for,
                    }
                )
            )
            time.sleep(sleep_for)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{description} failed without raising a concrete exception")


def authorize_gspread_client(*, creds_path: str, scopes: list[str]):
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(os.path.expanduser(creds_path), scopes=scopes)
    return gspread.authorize(creds)
