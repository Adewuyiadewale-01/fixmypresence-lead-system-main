"""Process Manager - Controls the Master Controller subprocess."""

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
    
    # =========================================================================
    # CORE OPERATIONS
    # =========================================================================
    
    def is_running(self) -> bool:
        """Check if controller is running."""
        # First, check our in-memory process
        if self._process and self._process.poll() is None:
            return True
        
        # Check PID file (for processes started before dashboard restart)
        pid = self._read_pid_file()
        if pid and self._is_pid_alive(pid):
            return True
        
        # If PID file exists but process is dead, clean it up
        self._check_stale_pid()
        return False
    
    def get_pid(self) -> Optional[int]:
        """Get current controller PID."""
        if self._process and self._process.poll() is None:
            return self._process.pid
        return self._read_pid_file()
    
    def start(self) -> Dict[str, Any]:
        """
        Start the controller.
        Returns: {"success": bool, "message": str, "pid": int?}
        """
        # Safety gate: prevent double-start
        if self.is_running():
            pid = self.get_pid()
            return {
                "success": False,
                "message": f"Controller already running (PID {pid})",
                "pid": pid
            }
        
        # Clear any stale PID
        self._check_stale_pid()
        
        # Build command from settings
        cmd = settings.get_controller_cmd()
        
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
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                cwd=str(settings.PROJECT_ROOT),
                start_new_session=True  # Detach from parent
            )
            self._start_time = datetime.now()
            self._write_pid_file(self._process.pid)
            
            return {
                "success": True,
                "message": f"Controller started (PID {self._process.pid})",
                "pid": self._process.pid
            }
        except Exception as e:
            if self._log_file:
                self._log_file.close()
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
        
        pid = self.get_pid()
        if not pid:
            self._check_stale_pid()
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
            return {
                "success": True,
                "message": "Controller stopped (exited during signal)",
                "method": "sigint"
            }
        
        # Step 2: Wait for graceful shutdown
        start_wait = time.time()
        while time.time() - start_wait < timeout:
            if not self._is_pid_alive(pid):
                self._clear_pid_file()
                self._cleanup()
                return {
                    "success": True,
                    "message": f"Controller stopped gracefully in {time.time() - start_wait:.1f}s",
                    "method": "sigint"
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
        
        return {
            "success": True,
            "message": f"Controller killed after {timeout}s timeout",
            "method": "sigkill"
        }
    
    def kill(self) -> Dict[str, Any]:
        """Immediate hard kill."""
        pid = self.get_pid()
        if not pid:
            self._check_stale_pid()
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
        
        return {
            "success": True,
            "message": "Controller killed immediately",
            "method": "sigkill"
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
        running = self.is_running()
        pid = self.get_pid() if running else None
        
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
    return _process_manager
