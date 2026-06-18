"""
Tests for the REST-phase fix that prevents the scheduler from killing
the enrichment pipeline children when it pauses scraping.

Covers:
  - ProcessManager.stop_scraper_only() sends SIGINT to the controller PID
    and does NOT invoke _stop_detached_pipeline_children() or _stop_watchers().
  - stop_scraper_only() escalates to SIGKILL if the controller ignores SIGINT.
  - stop_scraper_only() is a no-op when no controller PID is registered.
  - Scheduler._cycle_loop() calls pm.stop_scraper_only() (not pm.stop())
    when transitioning RUNNING -> RESTING.
"""

from __future__ import annotations

import signal
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.process import ProcessManager  # noqa: E402
from dashboard.scheduler import CyclePhase, Scheduler  # noqa: E402


class TestStopScraperOnly(unittest.TestCase):
    """ProcessManager.stop_scraper_only() must only touch the controller."""

    def setUp(self):
        self.pm = ProcessManager()
        # Spy on the methods that MUST NOT be called.
        self.pm._stop_detached_pipeline_children = MagicMock(
            side_effect=AssertionError(
                "stop_scraper_only must not stop detached pipeline children"
            )
        )
        self.pm._stop_watchers = MagicMock(
            side_effect=AssertionError(
                "stop_scraper_only must not stop watchers"
            )
        )
        self.pm._cleanup = MagicMock()
        self.pm._clear_pid_file = MagicMock()
        self.pm._check_stale_pid = MagicMock()

    def test_no_pid_is_noop(self):
        """When no controller PID is registered, return success and touch nothing."""
        with patch.object(self.pm, "get_pid", return_value=None):
            result = self.pm.stop_scraper_only(timeout=1)

        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "none")
        self.pm._stop_detached_pipeline_children.assert_not_called()
        self.pm._stop_watchers.assert_not_called()

    def test_dead_pid_is_noop(self):
        """Stale PID that no longer maps to a live process: clean exit."""
        with patch.object(self.pm, "get_pid", return_value=12345), \
             patch.object(self.pm, "_is_pid_alive", return_value=False):
            result = self.pm.stop_scraper_only(timeout=1)

        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "none")
        self.pm._stop_detached_pipeline_children.assert_not_called()
        self.pm._stop_watchers.assert_not_called()

    def test_graceful_sigint(self):
        """Controller exits after SIGINT: success, no pipeline-child kill."""
        pid = 54321
        alive_states = iter([True, False])  # alive at start, dead after signal
        with patch.object(self.pm, "get_pid", return_value=pid), \
             patch.object(self.pm, "_is_pid_alive", side_effect=lambda p: next(alive_states)), \
             patch("dashboard.process.os.kill") as mock_kill:
            result = self.pm.stop_scraper_only(timeout=2)

        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "sigint")
        mock_kill.assert_called_once_with(pid, signal.SIGINT)
        self.pm._stop_detached_pipeline_children.assert_not_called()
        self.pm._stop_watchers.assert_not_called()
        self.pm._clear_pid_file.assert_called()

    def test_escalate_to_sigkill(self):
        """Controller ignores SIGINT: escalates to SIGKILL, still no pipeline kill."""
        pid = 9999
        with patch.object(self.pm, "get_pid", return_value=pid), \
             patch.object(self.pm, "_is_pid_alive", return_value=True), \
             patch("dashboard.process.os.kill") as mock_kill, \
             patch("dashboard.process.time.sleep"):
            result = self.pm.stop_scraper_only(timeout=1)

        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "sigkill")

        signals_sent = [call.args[1] for call in mock_kill.call_args_list]
        self.assertIn(signal.SIGINT, signals_sent)
        self.assertIn(signal.SIGKILL, signals_sent)

        self.pm._stop_detached_pipeline_children.assert_not_called()
        self.pm._stop_watchers.assert_not_called()

    def test_sigint_process_gone_midway(self):
        """Process vanishes between get_pid() and SIGINT: graceful early exit."""
        pid = 7777
        with patch.object(self.pm, "get_pid", return_value=pid), \
             patch.object(self.pm, "_is_pid_alive", return_value=True), \
             patch("dashboard.process.os.kill", side_effect=ProcessLookupError):
            result = self.pm.stop_scraper_only(timeout=1)

        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "sigint")
        self.pm._stop_detached_pipeline_children.assert_not_called()
        self.pm._stop_watchers.assert_not_called()


class TestSchedulerRestTransition(unittest.TestCase):
    """Scheduler must call stop_scraper_only() when entering REST phase."""

    def _make_scheduler(self) -> Scheduler:
        sched = Scheduler()
        sched._enabled = True
        sched._phase = CyclePhase.RUNNING
        sched._run_minutes = 4
        sched._rest_minutes = 3
        # Force transition time into the past so the loop fires immediately.
        sched._next_transition = datetime.now() - timedelta(seconds=1)
        return sched

    def _run_loop_once(self, sched: Scheduler):
        """Run exactly one iteration of _cycle_loop by stopping it right after."""
        original_sleep = __import__("time").sleep

        def _fast_sleep(_seconds):
            # After the first iteration's body runs, set the stop event so the
            # loop exits on its next guard check.
            sched._stop_event.set()

        with patch("dashboard.scheduler.time.sleep", side_effect=_fast_sleep):
            sched._cycle_loop()

    def test_rest_transition_calls_stop_scraper_only(self):
        """RUNNING -> RESTING must invoke stop_scraper_only, not stop."""
        sched = self._make_scheduler()
        mock_pm = MagicMock()
        mock_pm.is_controller_running.return_value = True

        with patch("dashboard.scheduler.get_process_manager", return_value=mock_pm):
            self._run_loop_once(sched)

        mock_pm.stop_scraper_only.assert_called_once()
        mock_pm.stop.assert_not_called()
        self.assertEqual(sched._phase, CyclePhase.RESTING)

    def test_rest_transition_sets_next_run_window(self):
        """After the REST transition, next_transition is pushed rest_minutes out."""
        sched = self._make_scheduler()
        mock_pm = MagicMock()
        mock_pm.is_controller_running.return_value = True

        before = datetime.now()
        with patch("dashboard.scheduler.get_process_manager", return_value=mock_pm):
            self._run_loop_once(sched)
        after = datetime.now()

        self.assertIsNotNone(sched._next_transition)
        expected_min = before + timedelta(minutes=sched._rest_minutes)
        expected_max = after + timedelta(minutes=sched._rest_minutes)
        self.assertGreaterEqual(sched._next_transition, expected_min)
        self.assertLessEqual(sched._next_transition, expected_max)

    def test_resting_to_running_uses_start_with_approval_flag(self):
        """RESTING -> RUNNING must restart the controller with the approval flag."""
        sched = Scheduler()
        sched._enabled = True
        sched._phase = CyclePhase.RESTING
        sched._run_minutes = 4
        sched._rest_minutes = 3
        sched._next_transition = datetime.now() - timedelta(seconds=1)

        mock_pm = MagicMock()
        mock_pm.is_controller_running.return_value = False

        with patch("dashboard.scheduler.get_process_manager", return_value=mock_pm):
            self._run_loop_once(sched)

        mock_pm.start.assert_called_once()
        # stop_scraper_only must not be called when leaving REST
        mock_pm.stop_scraper_only.assert_not_called()
        mock_pm.stop.assert_not_called()
        self.assertEqual(sched._phase, CyclePhase.RUNNING)

    def test_request_emergency_cooldown_enters_resting_and_stops_scraper(self):
        sched = Scheduler()
        sched._enabled = True
        sched._phase = CyclePhase.RUNNING

        mock_pm = MagicMock()
        mock_pm.stop_scraper_only.return_value = {"success": True, "method": "sigint"}

        with patch("dashboard.scheduler.get_process_manager", return_value=mock_pm):
            result = sched.request_emergency_cooldown(15, reason="ram_critical_confirmed")

        self.assertTrue(result["success"])
        self.assertTrue(result["scheduled"])
        self.assertEqual(sched._phase, CyclePhase.RESTING)
        self.assertEqual(sched._cooldown_reason, "ram_critical_confirmed")
        mock_pm.stop_scraper_only.assert_called_once()
        self.assertIsNotNone(sched._next_transition)


if __name__ == "__main__":
    unittest.main()
