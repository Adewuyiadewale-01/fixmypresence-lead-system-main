"""Process Manager - Controls the Master Controller subprocess."""

import json
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from . import settings


class ProcessManager:
    """Manages the controller subprocess lifecycle."""
    
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._start_time: Optional[datetime] = None
        self._log_file = None
        self._system_active = False
        self._supervisor_thread = None
        self._supervisor_stop_event = None
    
    # =========================================================================
    # PID FILE HANDLING
    # =========================================================================
    
    def _read_pid_file(self) -> Optional[int]:
        """Read PID from file. Returns None if file doesn't exist."""
        if settings.PID_FILE.exists():
            try:
                return int(settings.PID_FILE.read_text().strip())
            except (ValueError, FileNotFoundError):
                return None
        return None
    
    def _write_pid_file(self, pid: int):
        """Write PID to file."""
        settings.PID_FILE.write_text(str(pid))
    
    def _clear_pid_file(self):
        """Remove PID file."""
        if settings.PID_FILE.exists():
            settings.PID_FILE.unlink()
    
    def _is_pid_alive(self, pid: int) -> bool:
        """Check if a process with given PID is running."""
        try:
            os.kill(pid, 0)  # Signal 0 = check existence
            return True
        except (ProcessLookupError, PermissionError):
            return False
    
    def _check_stale_pid(self) -> bool:
        """
        Check for stale PID file and clean it up.
        Returns True if stale PID was found and cleared.
        """
        pid = self._read_pid_file()
        if pid and not self._is_pid_alive(pid):
            self._clear_pid_file()
            return True
        return False

    def _read_pid_file_for(self, path: Path) -> Optional[int]:
        if path.exists():
            try:
                return int(path.read_text().strip())
            except (ValueError, FileNotFoundError):
                return None
        return None

    def _write_pid_file_for(self, path: Path, pid: int):
        path.write_text(str(pid))

    def _clear_pid_file_for(self, path: Path):
        if path.exists():
            path.unlink()

    def _read_json_file(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_json_file(self, path: Path, payload: Dict[str, Any]):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _signal_pid_or_group(self, pid: int, sig: int):
        try:
            os.killpg(pid, sig)
            return
        except Exception:
            pass
        os.kill(pid, sig)

    def _read_recent_system_log(self, max_lines: int = 80) -> str:
        try:
            if not settings.SYSTEM_LOG.exists():
                return ""
            lines = settings.SYSTEM_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
            return "\n".join(lines[-max_lines:])
        except Exception:
            return ""

    def _describe_controller_immediate_exit(self, returncode: int) -> str:
        if returncode != 0:
            return f"Controller exited immediately (code {returncode}). Check logs/system.log."

        recent_log = self._read_recent_system_log()
        if "All categories complete! Pipeline finished." in recent_log:
            return "Controller completed immediately: all scraper categories are already finished."
        if "Ready for --next-category" in recent_log or "Action: next_category" in recent_log:
            return "Controller completed immediately: the scraper is already at the next-category boundary."
        if "Action: done" in recent_log:
            return "Controller completed immediately: the scraper run is already finished."
        return "Controller completed immediately."

    def _format_watcher_labels(self, watcher_pids: Dict[str, int]) -> str:
        labels = [spec["label"] for spec in settings.WATCHER_SPECS if spec["key"] in watcher_pids]
        return ", ".join(labels)

    def _controller_is_running(self) -> bool:
        if self._process and self._process.poll() is None:
            return True
        pid = self._read_pid_file()
        if pid and self._is_pid_alive(pid):
            return True
        self._check_stale_pid()
        return False

    def _watcher_pid(self, spec: Dict[str, Any]) -> Optional[int]:
        pid = self._read_pid_file_for(spec["pid_file"])
        if pid and self._is_pid_alive(pid):
            return pid
        if pid:
            self._clear_pid_file_for(spec["pid_file"])
        return None

    def _watchers_running(self) -> Dict[str, int]:
        running: Dict[str, int] = {}
        for spec in settings.WATCHER_SPECS:
            pid = self._watcher_pid(spec)
            if pid:
                running[spec["key"]] = pid
        return running

    def _stop_detached_pipeline_children(self, timeout: int, sigkill: bool = False) -> Dict[str, Any]:
        stopped: list[str] = []
        already_stopped: list[str] = []
        failed: list[str] = []

        controller_dir = settings.CONTROLLER_DIR
        signal_to_send = signal.SIGKILL if sigkill else signal.SIGINT

        targets = [
            {
                "label": "LinkedIn Enricher",
                "lock_path": controller_dir / "enrichment_lock.json",
                "run_state_path": controller_dir / "enrichment_run_state.json",
            },
            {
                "label": "Scraper V3",
                "lock_path": controller_dir / "scraper_v3_lock.json",
                "run_state_path": None,
            },
        ]

        for target in targets:
            lock_data = self._read_json_file(target["lock_path"])
            pid = lock_data.get("pid")
            label = target["label"]

            if not isinstance(pid, int):
                already_stopped.append(label)
                continue

            if not self._is_pid_alive(pid):
                already_stopped.append(label)
            else:
                try:
                    self._signal_pid_or_group(pid, signal_to_send)
                except ProcessLookupError:
                    already_stopped.append(label)
                else:
                    if not sigkill:
                        start_wait = time.time()
                        while time.time() - start_wait < timeout:
                            if not self._is_pid_alive(pid):
                                break
                            time.sleep(0.25)
                        if self._is_pid_alive(pid):
                            try:
                                self._signal_pid_or_group(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    if self._is_pid_alive(pid):
                        failed.append(label)
                    else:
                        stopped.append(label)

            self._clear_pid_file_for(target["lock_path"])

            run_state_path = target.get("run_state_path")
            if run_state_path:
                run_state = self._read_json_file(run_state_path)
                if run_state:
                    run_state["active"] = False
                    run_state["ended_at"] = run_state.get("ended_at") or datetime.now().isoformat()
                    run_state["last_error"] = run_state.get("last_error") or "Stopped by dashboard shutdown"
                    self._write_json_file(run_state_path, run_state)

        # Safety sweep for detached drain commands that may have stale/missing lock metadata.
        drain_patterns = [
            "Bots/enricher/enrich_linkedin.py --drain",
            "Bots/enricher/keyword_detector.py --drain",
            "Bots/enricher/score_and_classify.py --drain",
            "Bots/enricher/scraper_v3.py",
        ]
        for pattern in drain_patterns:
            try:
                subprocess.run(
                    ["pkill", "-f", pattern],
                    capture_output=True,
                    text=True,
                )
            except Exception:
                pass

        watcher_state_path = controller_dir / "enrichment_watcher_state.json"
        watcher_state = self._read_json_file(watcher_state_path)
        if watcher_state:
            watcher_state.update(
                {
                    "lock_held": False,
                    "trigger_state": "idle",
                    "active_pid": None,
                    "last_shutdown_at": datetime.now().isoformat(),
                }
            )
            self._write_json_file(watcher_state_path, watcher_state)

        return {
            "stopped": stopped,
            "already_stopped": already_stopped,
            "failed": failed,
        }

    def _start_watchers(self) -> Dict[str, Any]:
        started: list[str] = []
        failed: list[str] = []
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")

        for spec in settings.WATCHER_SPECS:
            if self._watcher_pid(spec):
                continue
            log_path = spec["log_file"]
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            log_handle = open(log_path, "a", encoding="utf-8")
            try:
                log_handle.write(f"\n{'='*60}\n")
                log_handle.write(f"[{datetime.now().isoformat()}] Starting watcher: {spec['label']}\n")
                log_handle.write(f"{'='*60}\n")
                log_handle.flush()
                proc = subprocess.Popen(
                    [settings.CONTROLLER_PYTHON, spec["script"]],
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    cwd=str(settings.PROJECT_ROOT),
                    start_new_session=True,
                    env=env,
                )
            finally:
                log_handle.close()
            time.sleep(0.25)
            if proc.poll() is not None:
                failed.append(spec["label"])
                self._clear_pid_file_for(spec["pid_file"])
                continue
            self._write_pid_file_for(spec["pid_file"], proc.pid)
            started.append(f"{spec['label']} (PID {proc.pid})")

        return {"started": started, "failed": failed}

    def _stop_watchers(self, timeout: int, sigkill: bool = False) -> Dict[str, Any]:
        stopped: list[str] = []
        already_stopped: list[str] = []

        for spec in settings.WATCHER_SPECS:
            pid = self._watcher_pid(spec)
            if not pid:
                already_stopped.append(spec["label"])
                self._clear_pid_file_for(spec["pid_file"])
                continue
            signal_to_send = signal.SIGKILL if sigkill else signal.SIGINT
            try:
                os.kill(pid, signal_to_send)
            except ProcessLookupError:
                self._clear_pid_file_for(spec["pid_file"])
                already_stopped.append(spec["label"])
                continue
            if not sigkill:
                start_wait = time.time()
                while time.time() - start_wait < timeout:
                    if not self._is_pid_alive(pid):
                        break
                    time.sleep(0.25)
                if self._is_pid_alive(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            self._clear_pid_file_for(spec["pid_file"])
            stopped.append(spec["label"])

        return {"stopped": stopped, "already_stopped": already_stopped}

    def _ensure_supervisor_thread(self):
        import threading

        if self._supervisor_thread and self._supervisor_thread.is_alive():
            return
        self._supervisor_stop_event = threading.Event()
        self._supervisor_thread = threading.Thread(
            target=self._watcher_supervisor_loop,
            daemon=True,
            name="dashboard-watcher-supervisor",
        )
        self._supervisor_thread.start()

    def _watcher_supervisor_loop(self):
        while self._supervisor_stop_event and not self._supervisor_stop_event.is_set():
            if not self._system_active:
                break
            # Keep watcher processes alive for the active system even after the
            # run-once controller exits naturally.
            self._start_watchers()
            for _ in range(10):
                if self._supervisor_stop_event and self._supervisor_stop_event.is_set():
                    break
                time.sleep(1)

    def _stop_supervisor_thread(self):
        if self._supervisor_stop_event:
            self._supervisor_stop_event.set()
        self._supervisor_thread = None
        self._supervisor_stop_event = None
    
    # =========================================================================
    # CORE OPERATIONS
    # =========================================================================
    
    def is_running(self) -> bool:
        """Check if any managed system process is running."""
        return self._controller_is_running() or bool(self._watchers_running())

    def is_controller_running(self) -> bool:
        """Check if the master controller process is running."""
        return self._controller_is_running()
    
    def get_pid(self) -> Optional[int]:
        """Get current controller PID."""
        if self._process and self._process.poll() is None:
            return self._process.pid
        return self._read_pid_file()
    
    def start(self, extra_flags: Optional[list[str]] = None) -> Dict[str, Any]:
        """
        Start the controller.
        Returns: {"success": bool, "message": str, "pid": int?}
        """
        # Safety gate: prevent double-start
        if self._controller_is_running():
            pid = self.get_pid()
            return {
                "success": False,
                "message": f"Controller already running (PID {pid})",
                "pid": pid
            }
        
        # Clear any stale PID
        self._check_stale_pid()
        self._system_active = True
        self._ensure_supervisor_thread()
        
        watcher_result = self._start_watchers()
        if watcher_result["failed"]:
            self._system_active = False
            self._stop_supervisor_thread()
            self._stop_watchers(timeout=2, sigkill=True)
            return {
                "success": False,
                "message": f"Failed to start watcher(s): {', '.join(watcher_result['failed'])}",
                "pid": None,
            }

        # Build command from settings
        cmd = settings.get_controller_cmd(extra_flags=extra_flags)
        
        # Open log file
        settings.SYSTEM_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(settings.SYSTEM_LOG, "a")
        
        # Write separator to log
        self._log_file.write(f"\n{'='*60}\n")
        self._log_file.write(f"[{datetime.now().isoformat()}] Starting: {' '.join(cmd)}\n")
        self._log_file.write(f"{'='*60}\n")
        self._log_file.flush()
        
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                cwd=str(settings.PROJECT_ROOT),
                start_new_session=True  # Detach from parent
            )
            self._start_time = datetime.now()
            self._write_pid_file(self._process.pid)

            # Give the child a brief chance to fail fast so the UI doesn't
            # report success when the controller exits immediately on import.
            time.sleep(0.75)
            returncode = self._process.poll()
            if returncode is not None:
                self._clear_pid_file()
                self._cleanup()
                if returncode == 0:
                    watcher_pids = self._watchers_running()
                    message = self._describe_controller_immediate_exit(returncode)
                    if watcher_pids:
                        message = f"{message} Watchers remain active: {self._format_watcher_labels(watcher_pids)}."
                    else:
                        self._system_active = False
                        self._stop_supervisor_thread()
                    return {
                        "success": True,
                        "message": message,
                        "pid": None,
                        "controller_completed": True,
                    }

                self._system_active = False
                self._stop_supervisor_thread()
                self._stop_watchers(timeout=2, sigkill=True)
                return {
                    "success": False,
                    "message": self._describe_controller_immediate_exit(returncode),
                    "pid": None,
                }
            
            watcher_note = ""
            if watcher_result["started"]:
                watcher_note = f" Watchers active: {', '.join(watcher_result['started'])}."
            return {
                "success": True,
                "message": f"Controller started (PID {self._process.pid}).{watcher_note}",
                "pid": self._process.pid
            }
        except Exception as e:
            if self._log_file:
                self._log_file.close()
            self._system_active = False
            self._stop_supervisor_thread()
            self._stop_watchers(timeout=2, sigkill=True)
            return {
                "success": False,
                "message": f"Failed to start: {e}",
                "pid": None
            }
    
    def stop(self, timeout: int = None) -> Dict[str, Any]:
        """
        Graceful stop with escalation to kill.
        Returns: {"success": bool, "message": str, "method": str}
        """
        timeout = timeout or settings.GRACEFUL_STOP_TIMEOUT
        self._system_active = False
        self._stop_supervisor_thread()
        child_result = self._stop_detached_pipeline_children(timeout=timeout)
        
        pid = self.get_pid()
        watchers_before = self._watchers_running()
        if not pid:
            self._check_stale_pid()
            watcher_result = self._stop_watchers(timeout=timeout)
            if watchers_before:
                return {
                    "success": True,
                    "message": "Controller not running. Watchers stopped.",
                    "method": "watchers_only",
                    "watchers": watcher_result,
                    "children": child_result,
                }
            return {
                "success": True,
                "message": "Controller not running",
                "method": "none"
            }
        
        # Check if actually alive
        if not self._is_pid_alive(pid):
            self._clear_pid_file()
            return {
                "success": True,
                "message": "Controller already stopped (cleared stale PID)",
                "method": "stale_cleanup"
            }
        
        # Step 1: Graceful SIGINT
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            self._clear_pid_file()
            watcher_result = self._stop_watchers(timeout=timeout)
            return {
                "success": True,
                "message": "Controller stopped (exited during signal)",
                "method": "sigint",
                "watchers": watcher_result,
                "children": child_result,
            }
        
        # Step 2: Wait for graceful shutdown
        start_wait = time.time()
        while time.time() - start_wait < timeout:
            if not self._is_pid_alive(pid):
                self._clear_pid_file()
                self._cleanup()
                watcher_result = self._stop_watchers(timeout=timeout)
                return {
                    "success": True,
                    "message": f"Controller stopped gracefully in {time.time() - start_wait:.1f}s",
                    "method": "sigint",
                    "watchers": watcher_result,
                    "children": child_result,
                }
            time.sleep(0.5)
        
        # Step 3: Escalate to SIGKILL
        try:
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.5)
        except ProcessLookupError:
            pass
        
        self._clear_pid_file()
        self._cleanup()
        watcher_result = self._stop_watchers(timeout=timeout, sigkill=True)
        
        return {
            "success": True,
            "message": f"Controller killed after {timeout}s timeout",
            "method": "sigkill",
            "watchers": watcher_result,
            "children": child_result,
        }
    
    def kill(self) -> Dict[str, Any]:
        """Immediate hard kill."""
        self._system_active = False
        self._stop_supervisor_thread()
        child_result = self._stop_detached_pipeline_children(timeout=1, sigkill=True)
        pid = self.get_pid()
        watchers_before = self._watchers_running()
        if not pid:
            self._check_stale_pid()
            watcher_result = self._stop_watchers(timeout=1, sigkill=True)
            if watchers_before:
                return {
                    "success": True,
                    "message": "Controller not running. Watchers killed.",
                    "method": "watchers_only",
                    "watchers": watcher_result,
                    "children": child_result,
                }
            return {
                "success": True,
                "message": "Controller not running",
                "method": "none"
            }
        
        if not self._is_pid_alive(pid):
            self._clear_pid_file()
            return {
                "success": True,
                "message": "Controller already stopped",
                "method": "stale_cleanup"
            }
        
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        
        self._clear_pid_file()
        self._cleanup()
        watcher_result = self._stop_watchers(timeout=1, sigkill=True)
        
        return {
            "success": True,
            "message": "Controller killed immediately",
            "method": "sigkill",
            "watchers": watcher_result,
            "children": child_result,
        }
    
    def _cleanup(self):
        """Clean up internal state and orphaned browsers."""
        if self._log_file:
            try:
                self._log_file.close()
            except:
                pass
            self._log_file = None
        self._process = None
        self._start_time = None
        
        # Kill any orphaned browser processes
        self._kill_orphaned_browsers()
    
    def _kill_orphaned_browsers(self):
        """Kill any Chromium/Playwright browser processes that may be left over."""
        import subprocess
        try:
            # Kill playwright-related chromium processes
            subprocess.run(
                ["pkill", "-9", "-f", ".playwright-profile-worker"],
                capture_output=True,
                text=True
            )
        except Exception:
            pass  # pkill may fail if no matching processes
    
    # =========================================================================
    # STATUS
    # =========================================================================
    
    def get_info(self) -> Dict[str, Any]:
        """Get process status info."""
        controller_running = self._controller_is_running()
        watcher_pids = self._watchers_running()
        running = controller_running or bool(watcher_pids)
        pid = self.get_pid() if controller_running else None
        
        uptime = None
        if running and self._start_time:
            delta = datetime.now() - self._start_time
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                uptime = f"{hours}h {minutes}m"
            else:
                uptime = f"{minutes}m {seconds}s"
        
        return {
            "running": running,
            "controller_running": controller_running,
            "watchers_running": watcher_pids,
            "pid": pid,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "uptime": uptime
        }


# Singleton instance
_process_manager: Optional[ProcessManager] = None

def get_process_manager() -> ProcessManager:
    """Get the singleton ProcessManager instance."""
    global _process_manager
    if _process_manager is None:
        _process_manager = ProcessManager()
        if _process_manager.is_running():
            _process_manager._system_active = True
            _process_manager._ensure_supervisor_thread()
    return _process_manager
