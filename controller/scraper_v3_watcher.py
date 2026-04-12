"""Scraper V3 Watcher - Monitors Alpha/Beta/Gamma source backlog and triggers scraper_v3.

This watcher polls the same source webhook used by scraper_v3 itself so the
"ready" count matches the script's real workload selection.

Trigger policy:
- Run immediately when Alpha + Beta ready rows reach the configured threshold.
- Gamma rows do not count toward the threshold, but are included in the batch.
- If any backlog exists for long enough, force a run via time fallback.

Usage:
    python scraper_v3_watcher.py --once
    python scraper_v3_watcher.py
    python scraper_v3_watcher.py --status
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp

_CONTROLLER_DIR = Path(__file__).parent
_PROJECT_ROOT = _CONTROLLER_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT / "Bots"))

from common.google_client import GoogleClient  # noqa: E402
from common.system_config import get_system_config  # noqa: E402


CREDENTIALS_PATH = Path(
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json")
)
SHEET_ID = os.environ.get(
    "SHEET_ID", "1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc"
)

SCRAPER_V3_SCRIPT = _PROJECT_ROOT / "Bots" / "enricher" / "scraper_v3.py"
LOG_DIR = _CONTROLLER_DIR / "logs"
LOCK_FILE = _CONTROLLER_DIR / "scraper_v3_lock.json"
BACKLOG_STATE_FILE = _CONTROLLER_DIR / "scraper_v3_backlog_state.json"


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


def _get_scraper_v3_settings() -> dict:
    config = get_system_config(include_live=False) or {}
    enrichment = config.get("enrichment") if isinstance(config.get("enrichment"), dict) else {}
    scraper_v3 = enrichment.get("scraper_v3") if isinstance(enrichment.get("scraper_v3"), dict) else {}
    watcher = scraper_v3.get("watcher") if isinstance(scraper_v3.get("watcher"), dict) else {}
    return {
        "alpha_beta_trigger_threshold": max(
            1,
            _env_int(
                "MIN_ALPHA_BETA_BACKLOG_TO_RUN",
                watcher.get("alpha_beta_trigger_threshold"),
                30,
            ),
        ),
        "max_wait_before_forced_run_minutes": max(
            1,
            _env_int(
                "SCRAPER_V3_MAX_WAIT_MINUTES",
                watcher.get("max_wait_before_forced_run_minutes"),
                60,
            ),
        ),
        "watcher_interval_minutes": max(
            1,
            _env_int(
                "WATCHER_INTERVAL_MINUTES",
                watcher.get("watcher_interval_minutes"),
                10,
            ),
        ),
        "stale_lock_timeout_minutes": max(
            1,
            _env_int(
                "SCRAPER_V3_STALE_LOCK_TIMEOUT_MINUTES",
                watcher.get("stale_lock_timeout_minutes"),
                30,
            ),
        ),
    }


def _tab_family(tab_name: str) -> Optional[str]:
    key = str(tab_name or "").strip().lower()
    if key in {"alpha", "alpha_high"}:
        return "alpha"
    if key in {"beta", "beta_high"}:
        return "beta"
    if key in {"gamma", "gamma_high"}:
        return "gamma"
    return None


def _log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [ScraperV3 Watcher] {msg}")


def _pid_is_alive(pid: int) -> bool:
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
    except OSError:
        return False


def _build_log_path() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOG_DIR / f"scraper_v3_{stamp}.log"


def _parse_iso_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _read_lock() -> dict | None:
    if not LOCK_FILE.exists():
        return None
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        with contextlib.suppress(OSError):
            LOCK_FILE.unlink()
        return None


def is_locked() -> bool:
    settings = _get_scraper_v3_settings()
    data = _read_lock()
    if not data:
        return False
    pid = data.get("pid")
    locked_at = _parse_iso_datetime(data.get("locked_at"))
    if locked_at is not None:
        age_minutes = (datetime.now() - locked_at).total_seconds() / 60.0
        if age_minutes >= settings["stale_lock_timeout_minutes"]:
            _log(
                f"⚠️ Clearing stale scraper_v3 lock ({age_minutes:.1f}m old, "
                f"threshold {settings['stale_lock_timeout_minutes']}m)."
            )
            release_lock()
            return False
    if not isinstance(pid, int) or not _pid_is_alive(pid):
        release_lock()
        return False
    return True


def acquire_lock(pid: int, reason: str) -> bool:
    if is_locked():
        return False
    payload = {
        "pid": pid,
        "reason": reason,
        "locked_at": datetime.now().isoformat(),
    }
    try:
        with open(LOCK_FILE, "x", encoding="utf-8") as f:
            json.dump(payload, f)
        return True
    except FileExistsError:
        return False


def update_lock_pid(pid: int, reason: str):
    payload = {
        "pid": pid,
        "reason": reason,
        "locked_at": datetime.now().isoformat(),
    }
    LOCK_FILE.write_text(json.dumps(payload), encoding="utf-8")


def release_lock():
    with contextlib.suppress(OSError):
        LOCK_FILE.unlink()


def _read_backlog_state() -> dict | None:
    if not BACKLOG_STATE_FILE.exists():
        return None
    try:
        data = json.loads(BACKLOG_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        with contextlib.suppress(OSError):
            BACKLOG_STATE_FILE.unlink()
        return None


def _get_backlog_since() -> datetime | None:
    data = _read_backlog_state()
    if not data:
        return None
    return _parse_iso_datetime(data.get("first_seen_at"))


def _set_backlog_since(now: datetime | None = None):
    timestamp = (now or datetime.now()).isoformat()
    BACKLOG_STATE_FILE.write_text(
        json.dumps({"first_seen_at": timestamp}),
        encoding="utf-8",
    )


def _clear_backlog_since():
    with contextlib.suppress(OSError):
        BACKLOG_STATE_FILE.unlink()


async def _fetch_ready_rows_async() -> list[dict]:
    async with aiohttp.ClientSession() as session:
        return await GoogleClient.fetch_source_urls(session)


def count_ready_rows() -> dict:
    try:
        rows = asyncio.run(_fetch_ready_rows_async())
    except Exception as exc:
        _log(f"❌ Failed to fetch source rows: {exc}")
        return {
            "alpha": 0,
            "alpha_high": 0,
            "beta": 0,
            "beta_high": 0,
            "gamma": 0,
            "gamma_high": 0,
            "alpha_family": 0,
            "beta_family": 0,
            "gamma_family": 0,
            "total": 0,
        }

    counts = {
        "alpha": 0,
        "alpha_high": 0,
        "beta": 0,
        "beta_high": 0,
        "gamma": 0,
        "gamma_high": 0,
        "alpha_family": 0,
        "beta_family": 0,
        "gamma_family": 0,
        "total": 0,
    }
    for row in rows:
        tab = str(row.get("tab") or "").strip().lower()
        if tab in counts:
            counts[tab] += 1
        family = _tab_family(tab)
        if family:
            counts[f"{family}_family"] += 1
        counts["total"] += 1
    return counts


def run_scraper_v3(total_ready: int) -> int:
    if not SCRAPER_V3_SCRIPT.exists():
        _log(f"❌ scraper_v3 not found: {SCRAPER_V3_SCRIPT}")
        return 0

    venv_python = _PROJECT_ROOT / ".venv" / "bin" / "python"
    python_exe = str(venv_python) if venv_python.exists() else sys.executable
    batch_size = max(1, total_ready)
    cmd = [python_exe, str(SCRAPER_V3_SCRIPT), "--sheet", str(batch_size)]
    env = os.environ.copy()
    bots_path = str(_PROJECT_ROOT / "Bots")
    existing_pythonpath = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = (
        f"{bots_path}:{existing_pythonpath}" if existing_pythonpath else bots_path
    )

    log_path = _build_log_path()
    _log(f"🚀 Starting scraper_v3 (detached): {' '.join(cmd)}")
    _log(f"📝 Scraper V3 log: {log_path}")

    log_handle = open(log_path, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=log_handle,
            stderr=log_handle,
            cwd=str(_PROJECT_ROOT),
            env=env,
        )
    finally:
        log_handle.close()

    _log(f"✅ scraper_v3 started with PID {proc.pid}")
    return proc.pid


def watcher_tick() -> bool:
    settings = _get_scraper_v3_settings()
    counts = count_ready_rows()
    alpha = counts["alpha_family"]
    beta = counts["beta_family"]
    gamma = counts["gamma_family"]
    total = counts["total"]
    alpha_beta = alpha + beta

    _log(
        f"🔍 Ready rows — Alpha family: {alpha}, Beta family: {beta}, Gamma family: {gamma}, "
        f"Alpha+Beta: {alpha_beta}, Total: {total}"
    )

    if is_locked():
        _log("🔒 Lock held (scraper_v3 running). Skipping this tick.")
        return False

    if alpha_beta >= settings["alpha_beta_trigger_threshold"]:
        _clear_backlog_since()
        if not acquire_lock(os.getpid(), "launching_scraper_v3"):
            _log("🔒 Could not acquire launch lock.")
            return False
        try:
            child_pid = run_scraper_v3(total)
            if not child_pid:
                release_lock()
                return False
            update_lock_pid(child_pid, "scraper_v3_sheet_batch")
            _log(
                f"✅ Triggered scraper_v3 — threshold met "
                f"(Alpha+Beta {alpha_beta} >= {settings['alpha_beta_trigger_threshold']}, "
                f"Gamma bonus {gamma}, total batch {total})."
            )
            return True
        except Exception as exc:
            release_lock()
            _log(f"❌ Failed to launch scraper_v3: {exc}")
            return False

    if total > 0:
        now = datetime.now()
        backlog_since = _get_backlog_since()
        if backlog_since is None:
            _set_backlog_since(now)
            _log(
                "⏳ Backlog detected below Alpha+Beta threshold. "
                "Waiting for threshold or timeout."
            )
            return False

        waited_minutes = (now - backlog_since).total_seconds() / 60.0
        if waited_minutes >= settings["max_wait_before_forced_run_minutes"]:
            _clear_backlog_since()
            if not acquire_lock(os.getpid(), "launching_scraper_v3_timeout"):
                _log("🔒 Could not acquire launch lock.")
                return False
            try:
                child_pid = run_scraper_v3(total)
                if not child_pid:
                    release_lock()
                    return False
                update_lock_pid(child_pid, "scraper_v3_sheet_batch")
                _log(
                    f"✅ Triggered scraper_v3 — timeout fallback after "
                    f"{waited_minutes:.1f}m with backlog total {total} "
                    f"(Alpha+Beta {alpha_beta}, Gamma {gamma})."
                )
                return True
            except Exception as exc:
                release_lock()
                _log(f"❌ Failed to launch scraper_v3: {exc}")
                return False

        remaining = max(0, int(settings["max_wait_before_forced_run_minutes"] - waited_minutes))
        _log(
            f"⏳ Waiting — Alpha+Beta {alpha_beta}/{settings['alpha_beta_trigger_threshold']}; "
            f"forced run in {remaining}m if backlog remains."
        )
        return False

    _clear_backlog_since()
    _log("✅ No source backlog ready for scraper_v3.")
    return False


def run_watcher_loop(interval_minutes: Optional[int] = None):
    if interval_minutes is None:
        interval_minutes = _get_scraper_v3_settings()["watcher_interval_minutes"]
    _log(f"🚀 Starting scraper_v3 watcher loop (interval: {interval_minutes}m)")
    try:
        while True:
            watcher_tick()
            time.sleep(interval_minutes * 60)
    except KeyboardInterrupt:
        _log("⏹️ Watcher stopped by user.")


def print_status():
    settings = _get_scraper_v3_settings()
    counts = count_ready_rows()
    lock = _read_lock()
    print("")
    print("=== Scraper V3 Watcher Status ===")
    print(f"Locked: {'YES' if is_locked() else 'NO'}")
    if lock and is_locked():
        print(f"PID: {lock.get('pid')}")
        print(f"Reason: {lock.get('reason')}")
        print(f"Locked at: {lock.get('locked_at')}")
    print(
        "Ready rows: "
        f"Alpha={counts['alpha']} + {counts['alpha_high']}h "
        f"Beta={counts['beta']} + {counts['beta_high']}h "
        f"Gamma={counts['gamma']} + {counts['gamma_high']}h "
        f"Total={counts['total']}"
    )
    print(f"Threshold (Alpha+Beta families): {settings['alpha_beta_trigger_threshold']}")
    print(f"Max wait: {settings['max_wait_before_forced_run_minutes']} minutes")
    print(f"Stale lock timeout: {settings['stale_lock_timeout_minutes']} minutes")
    backlog_since = _get_backlog_since()
    if backlog_since:
        waited_minutes = (datetime.now() - backlog_since).total_seconds() / 60.0
        print(f"Backlog timer: active for {waited_minutes:.1f} minutes")
    print("")


def parse_args():
    parser = argparse.ArgumentParser(description="Watcher for scraper_v3 source backlog")
    parser.add_argument("--once", action="store_true", help="Run a single watcher tick")
    parser.add_argument("--status", action="store_true", help="Show watcher lock/backlog status")
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Loop interval in minutes (defaults to shared scraper_v3 watcher config)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.status:
        print_status()
        sys.exit(0)

    if args.once:
        try:
            watcher_tick()
            sys.exit(0)
        except Exception as exc:
            _log(f"❌ Watcher tick failed: {exc}")
            sys.exit(1)

    run_watcher_loop(interval_minutes=args.interval)
