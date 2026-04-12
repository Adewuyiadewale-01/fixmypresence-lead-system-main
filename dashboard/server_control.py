"""Dedicated launcher/shutdown helpers for the dashboard server."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from . import settings


def _pid_file() -> Path:
    return settings.DASHBOARD_SERVER_PID_FILE


def _log_file() -> Path:
    return settings.DASHBOARD_SERVER_LOG


def _python_executable() -> str:
    venv_python = settings.PROJECT_ROOT / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def _is_pid_alive(pid: int | None) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid() -> Optional[int]:
    pid_path = _pid_file()
    if not pid_path.exists():
        return None
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except Exception:
        return None
    if _is_pid_alive(pid):
        return pid
    with contextlib.suppress(Exception):
        pid_path.unlink()
    return None


def _write_pid(pid: int):
    _pid_file().write_text(str(pid), encoding="utf-8")


def _clear_pid():
    with contextlib.suppress(Exception):
        _pid_file().unlink()


def _listening_pid() -> Optional[int]:
    try:
        completed = subprocess.run(
            [
                "lsof",
                "-nP",
                f"-iTCP:{settings.DASHBOARD_PORT}",
                "-sTCP:LISTEN",
                "-t",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return None
    raw = (completed.stdout or "").strip().splitlines()
    for line in raw:
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if _is_pid_alive(pid):
            return pid
    return None


def read_dashboard_pid() -> Optional[int]:
    return _read_pid() or _listening_pid()


def build_dashboard_cmd(reload: bool | None = None) -> list[str]:
    use_reload = settings.DASHBOARD_RELOAD if reload is None else bool(reload)
    cmd = [
        _python_executable(),
        "-m",
        "uvicorn",
        "dashboard.api:app",
        "--host",
        settings.DASHBOARD_HOST,
        "--port",
        str(settings.DASHBOARD_PORT),
    ]
    if use_reload:
        cmd.append("--reload")
    return cmd


def start_dashboard(reload: bool | None = None) -> Dict[str, Any]:
    existing_pid = read_dashboard_pid()
    if existing_pid:
        _write_pid(existing_pid)
        return {
            "success": False,
            "message": f"Dashboard already running (PID {existing_pid})",
            "pid": existing_pid,
        }

    log_path = _log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = build_dashboard_cmd(reload=reload)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    log_handle = open(log_path, "a", encoding="utf-8")
    try:
        log_handle.write(f"\n{'=' * 72}\n")
        log_handle.write(f"[{datetime.now().isoformat()}] Starting dashboard: {' '.join(cmd)}\n")
        log_handle.write(f"{'=' * 72}\n")
        log_handle.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(settings.PROJECT_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
    finally:
        log_handle.close()

    _write_pid(proc.pid)
    time.sleep(1.0)
    return_code = proc.poll()
    if return_code is not None:
        _clear_pid()
        return {
            "success": False,
            "message": f"Dashboard exited immediately (code {return_code}). Check {log_path}.",
            "pid": None,
        }

    return {
        "success": True,
        "message": f"Dashboard started on http://{settings.DASHBOARD_HOST}:{settings.DASHBOARD_PORT} (PID {proc.pid})",
        "pid": proc.pid,
    }


def stop_dashboard(force: bool = False, timeout: int = 15, delay_seconds: float = 0.0) -> Dict[str, Any]:
    if delay_seconds > 0:
        time.sleep(delay_seconds)

    pid = read_dashboard_pid()
    if not pid:
        _clear_pid()
        return {"success": True, "message": "Dashboard already stopped", "pid": None}

    signal_to_send = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.kill(pid, signal_to_send)
    except ProcessLookupError:
        _clear_pid()
        return {"success": True, "message": "Dashboard already stopped", "pid": pid}

    start_wait = time.time()
    while time.time() - start_wait < timeout:
        if not _is_pid_alive(pid):
            _clear_pid()
            return {
                "success": True,
                "message": f"Dashboard stopped (PID {pid})",
                "pid": pid,
            }
        time.sleep(0.25)

    if not force:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        time.sleep(0.5)

    alive = _is_pid_alive(pid)
    if not alive:
        _clear_pid()
        return {
            "success": True,
            "message": f"Dashboard killed after timeout (PID {pid})",
            "pid": pid,
        }

    return {
        "success": False,
        "message": f"Dashboard process {pid} did not stop cleanly",
        "pid": pid,
    }


def restart_dashboard(reload: bool | None = None, delay_seconds: float = 0.0) -> Dict[str, Any]:
    stop_result = stop_dashboard(force=True, timeout=5, delay_seconds=delay_seconds)
    start_result = start_dashboard(reload=reload)
    if not start_result.get("success"):
        return {
            "success": False,
            "message": f"Dashboard stop result: {stop_result.get('message')}. Start failed: {start_result.get('message')}",
            "pid": start_result.get("pid"),
        }
    return {
        "success": True,
        "message": f"Dashboard restarted. {start_result.get('message')}",
        "pid": start_result.get("pid"),
    }


def dashboard_status() -> Dict[str, Any]:
    pid = read_dashboard_pid()
    return {
        "running": bool(pid),
        "pid": pid,
        "host": settings.DASHBOARD_HOST,
        "port": settings.DASHBOARD_PORT,
        "url": f"http://{settings.DASHBOARD_HOST}:{settings.DASHBOARD_PORT}",
        "log_file": str(_log_file()),
        "pid_file": str(_pid_file()),
    }
