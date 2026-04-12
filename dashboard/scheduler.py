"""Scheduler - Manages 4h Run / 2h Rest auto-cycle."""

import threading
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any

from . import settings
from .process import get_process_manager


class CyclePhase(Enum):
    DISABLED = "DISABLED"
    RUNNING = "RUNNING"
    RESTING = "RESTING"


class Scheduler:
    """
    Manages the automated Run/Rest cycle.
    
    Important: Manual stop() disables the scheduler to prevent unwanted restarts.
    """
    
    def __init__(self):
        self._enabled = False
        self._phase = CyclePhase.DISABLED
        self._run_hours = settings.DEFAULT_RUN_HOURS
        self._rest_hours = settings.DEFAULT_REST_HOURS
        self._next_transition: Optional[datetime] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
    
    # =========================================================================
    # CONFIGURATION
    # =========================================================================
    
    def configure(self, run_hours: int = None, rest_hours: int = None):
        """Update cycle durations."""
        with self._lock:
            if run_hours is not None:
                self._run_hours = max(1, run_hours)
            if rest_hours is not None:
                self._rest_hours = max(1, rest_hours)
    
    def get_config(self) -> Dict[str, int]:
        """Get current configuration."""
        return {
            "run_hours": self._run_hours,
            "rest_hours": self._rest_hours
        }
    
    # =========================================================================
    # CYCLE CONTROL
    # =========================================================================
    
    def start_cycle(self) -> Dict[str, Any]:
        """
        Enable scheduler and start the controller.
        Returns cycle start result.
        """
        with self._lock:
            if self._enabled:
                return {
                    "success": False,
                    "message": "Cycle already running"
                }
            
            # Start the controller
            pm = get_process_manager()
            result = pm.start()
            
            if not result["success"]:
                return result
            
            # Enable scheduler
            self._enabled = True
            self._phase = CyclePhase.RUNNING
            self._next_transition = datetime.now() + timedelta(hours=self._run_hours)
            
            # Start background thread
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._cycle_loop, daemon=True)
            self._thread.start()
            
            return {
                "success": True,
                "message": f"Cycle started. Will rest in {self._run_hours}h.",
                "next_transition": self._next_transition.isoformat()
            }
    
    def stop_cycle(self) -> Dict[str, Any]:
        """
        Disable scheduler and stop the controller.
        This is the "manual override" - cycle won't resume until explicitly re-enabled.
        """
        with self._lock:
            was_enabled = self._enabled
            self._enabled = False
            self._phase = CyclePhase.DISABLED
            self._next_transition = None
            self._stop_event.set()
        
        # Stop the controller
        pm = get_process_manager()
        stop_result = pm.stop()
        
        return {
            "success": True,
            "message": "Cycle disabled. Controller stopped." if was_enabled else "Controller stopped.",
            "stop_details": stop_result
        }
    
    def manual_stop(self) -> Dict[str, Any]:
        """
        Called when user explicitly stops the system.
        Disables the scheduler (manual override).
        """
        return self.stop_cycle()
    
    # =========================================================================
    # STATE
    # =========================================================================
    
    def get_state(self) -> Dict[str, Any]:
        """Get current scheduler state."""
        with self._lock:
            return {
                "enabled": self._enabled,
                "phase": self._phase.value,
                "next_transition": self._next_transition.isoformat() if self._next_transition else None,
                "config": self.get_config()
            }
    
    def is_enabled(self) -> bool:
        """Check if scheduler is enabled."""
        return self._enabled
    
    # =========================================================================
    # BACKGROUND LOOP
    # =========================================================================
    
    def _cycle_loop(self):
        """
        Background thread loop managing the Run/Rest cycle.
        Uses small sleep intervals for responsiveness.
        Also monitors controller process and restarts if it exits during RUN phase.
        """
        while not self._stop_event.is_set():
            with self._lock:
                if not self._enabled:
                    break
                
                now = datetime.now()
                pm = get_process_manager()
                
                # Check for time-based transitions
                if self._next_transition and now >= self._next_transition:
                    if self._phase == CyclePhase.RUNNING:
                        # Transition to REST
                        self._phase = CyclePhase.RESTING
                        self._next_transition = now + timedelta(hours=self._rest_hours)
                        print(f"[Scheduler] Entering REST phase for {self._rest_hours}h")
                        
                        # Stop controller
                        pm.stop()
                        
                    elif self._phase == CyclePhase.RESTING:
                        # Transition to RUN
                        self._phase = CyclePhase.RUNNING
                        self._next_transition = now + timedelta(hours=self._run_hours)
                        print(f"[Scheduler] Entering RUN phase for {self._run_hours}h")
                        
                        # Start controller
                        pm.start()
                
                # Auto-restart: If we're in RUN phase but controller has exited, restart it
                # This enables automatic category advancement
                elif self._phase == CyclePhase.RUNNING and not pm.is_controller_running():
                    print(f"[Scheduler] Controller exited during RUN phase. Restarting to continue...")
                    time.sleep(5)  # Brief delay before restart
                    pm.start()
            
            # Sleep in small increments for responsiveness
            for _ in range(10):  # 10 x 1s = 10s between checks
                if self._stop_event.is_set():
                    break
                time.sleep(1)
        
        print("[Scheduler] Cycle loop exited")


# Singleton instance
_scheduler: Optional[Scheduler] = None

def get_scheduler() -> Scheduler:
    """Get the singleton Scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler
