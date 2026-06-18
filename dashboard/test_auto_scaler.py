"""
Tests for dashboard/auto_scaler.py.

Covers:
  - worker arithmetic
  - immediate live brake logic
  - completed-run planning logic
  - check_and_apply() live brake + completed-run apply paths
  - manual change reset
  - status output shape
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

DASHBOARD_DIR = Path(__file__).parent
PROJECT_ROOT = DASHBOARD_DIR.parent
BOTS_DIR = PROJECT_ROOT / "Bots"
for p in (str(DASHBOARD_DIR), str(BOTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import auto_scaler as asc  # noqa: E402


def _utc(minutes_ago: float = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def _iso(minutes_ago: float = 0) -> str:
    return _utc(minutes_ago).isoformat()


def _health_file(tmp_dir: Path, **kwargs) -> Path:
    defaults = {
        "started_at": _iso(minutes_ago=5),
        "ended_at": None,
        "current_phase": "healthy",
        "run_searches": 50,
        "run_batches": 2,
        "run_leads": 100,
        "run_website_found": 40,
        "run_website_total": 50,
        "captcha_count": 0,
        "error_count": 0,
        "compute_utilization_pct": 40.0,
        "cpu_percent": 32.0,
        "memory_pressure_pct": 48.0,
    }
    defaults.update(kwargs)
    path = tmp_dir / "scraper_health.json"
    path.write_text(json.dumps(defaults), encoding="utf-8")
    return path


class AutoScalerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.state_path = self.tmp_path / "auto_scale_state.json"
        self.state_patch = patch.object(asc, "AUTO_SCALE_STATE_FILE", self.state_path)
        self.state_patch.start()

    def tearDown(self):
        self.state_patch.stop()
        self.tmp.cleanup()

    def _patch_health(self, health_path: Path):
        mock_cfg = MagicMock()
        mock_cfg.SCRAPER_HEALTH_FILE = str(health_path)
        return patch.dict("sys.modules", {"maps_scraper.config": mock_cfg})

    def _patch_config(self, workers=4):
        mock_cfg_mod = MagicMock()
        mock_cfg_mod.get_dashboard_config.return_value = {"runtime": {"num_workers": workers}}
        mock_cfg_mod.save_dashboard_config = MagicMock()
        return patch.dict("sys.modules", {"maps_scraper.config": mock_cfg_mod}), mock_cfg_mod

    def _patch_config_and_health(self, health_path: Path, workers=4):
        mock_cfg_mod = MagicMock()
        mock_cfg_mod.SCRAPER_HEALTH_FILE = str(health_path)
        mock_cfg_mod.get_dashboard_config.return_value = {"runtime": {"num_workers": workers}}
        mock_cfg_mod.save_dashboard_config = MagicMock()
        return patch.dict("sys.modules", {"maps_scraper.config": mock_cfg_mod}), mock_cfg_mod


class TestComputeNewWorkers(unittest.TestCase):
    def test_increase_20_percent(self):
        self.assertEqual(asc._compute_new_workers(4, "increase"), 5)

    def test_decrease_20_percent(self):
        self.assertEqual(asc._compute_new_workers(4, "decrease"), 3)

    def test_increase_always_moves_at_least_one(self):
        self.assertEqual(asc._compute_new_workers(1, "increase"), 2)

    def test_decrease_always_moves_at_least_one(self):
        self.assertEqual(asc._compute_new_workers(2, "decrease"), 1)

    def test_boundaries(self):
        self.assertEqual(asc._compute_new_workers(asc.MAX_WORKERS, "increase"), asc.MAX_WORKERS)
        self.assertEqual(asc._compute_new_workers(asc.MIN_WORKERS, "decrease"), asc.MIN_WORKERS)


class TestLiveBrakeAction(AutoScalerBase):
    def test_none_for_healthy_live_run(self):
        health = _health_file(self.tmp_path)
        with self._patch_health(health):
            self.assertEqual(asc._compute_action(), "none")

    def test_decrease_on_memory_pressure(self):
        health = _health_file(self.tmp_path, memory_pressure_pct=94.0)
        with self._patch_health(health):
            self.assertEqual(asc._compute_action(), "decrease")

    def test_decrease_on_captcha(self):
        health = _health_file(self.tmp_path, captcha_count=1)
        with self._patch_health(health):
            self.assertEqual(asc._compute_action(), "decrease")

    def test_load_alone_does_not_force_decrease(self):
        health = _health_file(self.tmp_path, compute_utilization_pct=196.0)
        with self._patch_health(health):
            self.assertEqual(asc._compute_action(), "none")

    def test_decrease_on_cpu_saturation(self):
        health = _health_file(self.tmp_path, cpu_percent=96.0)
        with self._patch_health(health):
            self.assertEqual(asc._compute_action(), "decrease")


class TestCompletedRunRecommendation(unittest.TestCase):
    def test_increase_for_completed_run_with_headroom(self):
        summary = {
            "runtime_minutes": 65.0,
            "sample_count": 8,
            "run_searches": 200,
            "run_batches": 4,
            "run_leads": 260,
            "leads_per_100": 130.0,
            "website_rate": 82.0,
            "captcha_count": 0,
            "error_count": 1,
            "error_rate": 0.5,
            "compute_p75": 158.0,
            "cpu_p75": 49.0,
            "memory_pressure_p75": 52.0,
        }
        self.assertEqual(asc._recommend_run_action(summary), ("increase", "completed_run_headroom"))

    def test_decrease_for_completed_run_with_low_website_rate(self):
        summary = {
            "runtime_minutes": 70.0,
            "sample_count": 8,
            "run_searches": 180,
            "run_batches": 4,
            "run_leads": 160,
            "leads_per_100": 88.9,
            "website_rate": 50.0,
            "captcha_count": 0,
            "error_count": 0,
            "error_rate": 0.0,
            "compute_p75": 170.0,
            "cpu_p75": 42.0,
            "memory_pressure_p75": 50.0,
        }
        self.assertEqual(asc._recommend_run_action(summary), ("decrease", "website_rate_low"))

    def test_none_when_run_too_short(self):
        summary = {
            "runtime_minutes": 12.0,
            "sample_count": 6,
            "run_searches": 100,
            "run_batches": 2,
            "run_leads": 100,
            "leads_per_100": 100.0,
            "website_rate": 80.0,
            "captcha_count": 0,
            "error_count": 0,
            "error_rate": 0.0,
            "compute_p75": 190.0,
            "cpu_p75": 30.0,
            "memory_pressure_p75": 35.0,
        }
        self.assertEqual(asc._recommend_run_action(summary), ("none", "run_too_short"))


class TestCheckAndApply(AutoScalerBase):
    def test_live_brake_applies_immediately(self):
        health = _health_file(self.tmp_path, memory_pressure_pct=94.0)
        cfg_patch, mock_mod = self._patch_config_and_health(health, workers=4)
        with cfg_patch:
            result = asc.check_and_apply()

        self.assertTrue(result["applied"])
        self.assertEqual(result["action"], "decrease")
        self.assertEqual(result["new_workers"], 3)
        saved_payload = mock_mod.save_dashboard_config.call_args[0][0]
        self.assertEqual(saved_payload["runtime"]["num_workers"], 3)

    def test_confirmed_ram_emergency_triggers_cooldown_stop(self):
        health = _health_file(self.tmp_path, memory_pressure_pct=95.0)
        cfg_patch, mock_mod = self._patch_config_and_health(health, workers=4)
        mock_scheduler = MagicMock()
        mock_scheduler.is_enabled.return_value = False
        mock_pm = MagicMock()
        mock_pm.stop_scraper_only.return_value = {"success": True, "method": "sigint"}

        with cfg_patch, \
             patch.object(asc, "_get_scheduler", return_value=mock_scheduler), \
             patch.object(asc, "_get_process_manager", return_value=mock_pm):
            first = asc.check_and_apply()
            second = asc.check_and_apply()

        self.assertTrue(first["applied"])
        self.assertEqual(first["action"], "decrease")
        self.assertEqual(second["source"], "emergency_cooldown")
        self.assertEqual(second["reason"], "ram_critical_confirmed")
        mock_pm.stop_scraper_only.assert_called_once()
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["last_emergency_run_started_at"], json.loads(health.read_text())["started_at"])
        self.assertIsNotNone(state["emergency_cooldown"])

    def test_extreme_ram_emergency_triggers_immediately(self):
        health = _health_file(self.tmp_path, memory_pressure_pct=97.0)
        cfg_patch, _ = self._patch_config_and_health(health, workers=4)
        mock_scheduler = MagicMock()
        mock_scheduler.is_enabled.return_value = False
        mock_pm = MagicMock()
        mock_pm.stop_scraper_only.return_value = {"success": True, "method": "sigint"}

        with cfg_patch, \
             patch.object(asc, "_get_scheduler", return_value=mock_scheduler), \
             patch.object(asc, "_get_process_manager", return_value=mock_pm):
            result = asc.check_and_apply()

        self.assertEqual(result["source"], "emergency_cooldown")
        self.assertEqual(result["reason"], "ram_extreme_single_hit")
        mock_pm.stop_scraper_only.assert_called_once()

    def test_completed_run_plan_applies_after_run_finishes(self):
        run_started_at = _iso(minutes_ago=45)
        running_health = _health_file(
            self.tmp_path,
            started_at=run_started_at,
            ended_at=None,
            run_searches=240,
            run_batches=5,
            run_leads=300,
            run_website_found=200,
            run_website_total=240,
            compute_utilization_pct=55.0,
            cpu_percent=44.0,
            memory_pressure_pct=50.0,
        )
        cfg_patch, mock_mod = self._patch_config_and_health(running_health, workers=4)

        with cfg_patch:
            for _ in range(5):
                result = asc.check_and_apply()
                self.assertFalse(result["applied"])

            _health_file(
                self.tmp_path,
                started_at=run_started_at,
                ended_at=_iso(minutes_ago=0),
                run_searches=240,
                run_batches=5,
                run_leads=300,
                run_website_found=200,
                run_website_total=240,
                compute_utilization_pct=55.0,
                cpu_percent=44.0,
                memory_pressure_pct=50.0,
            )
            result = asc.check_and_apply()

        self.assertTrue(result["applied"])
        self.assertEqual(result["action"], "increase")
        self.assertEqual(result["new_workers"], 5)
        saved_payload = mock_mod.save_dashboard_config.call_args[0][0]
        self.assertEqual(saved_payload["runtime"]["num_workers"], 5)

    def test_no_apply_when_not_enough_data(self):
        run_started_at = _iso(minutes_ago=50)
        running_health = _health_file(
            self.tmp_path,
            started_at=run_started_at,
            run_searches=10,
            run_batches=0,
        )
        cfg_patch, _ = self._patch_config_and_health(running_health, workers=4)
        with cfg_patch:
            for _ in range(5):
                asc.check_and_apply()
            _health_file(
                self.tmp_path,
                started_at=run_started_at,
                ended_at=_iso(minutes_ago=0),
                run_searches=10,
                run_batches=0,
            )
            result = asc.check_and_apply()
        self.assertFalse(result["applied"])

    def test_manual_mode_restarts_after_emergency_cooldown(self):
        run_started_at = _iso(minutes_ago=50)
        _health_file(
            self.tmp_path,
            started_at=run_started_at,
            ended_at=_iso(minutes_ago=40),
            run_searches=240,
            run_batches=5,
            run_leads=300,
            run_website_found=200,
            run_website_total=240,
            compute_utilization_pct=55.0,
            cpu_percent=44.0,
            memory_pressure_pct=50.0,
        )
        self.state_path.write_text(
            json.dumps(
                {
                    "emergency_cooldown": {
                        "run_started_at": run_started_at,
                        "reason": "ram_critical_confirmed",
                        "triggered_at": _iso(minutes_ago=35),
                        "restart_at": _iso(minutes_ago=20),
                        "cooldown_minutes": 15,
                        "scheduler_managed": False,
                    }
                }
            ),
            encoding="utf-8",
        )
        with self._patch_health(self.tmp_path / "scraper_health.json"), \
             patch.object(asc, "_get_scheduler", return_value=MagicMock(is_enabled=MagicMock(return_value=False))), \
             patch.object(asc, "_get_process_manager") as mock_get_pm:
            mock_pm = MagicMock()
            mock_pm.is_controller_running.return_value = False
            mock_pm.start.return_value = {"success": True, "pid": 123}
            mock_get_pm.return_value = mock_pm
            result = asc.check_and_apply()

        self.assertEqual(result["reason"], "restarted_after_emergency_cooldown")
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIsNone(state.get("emergency_cooldown"))


class TestManualChange(AutoScalerBase):
    def test_record_manual_change_clears_pending_plan(self):
        self.state_path.write_text(
            json.dumps(
                {
                    "pending_recommendation": {
                        "action": "increase",
                        "source": "completed_run",
                    },
                    "emergency_cooldown": {
                        "reason": "ram_critical_confirmed",
                    },
                }
            ),
            encoding="utf-8",
        )
        asc.record_manual_change()
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIsNotNone(state["last_manual_change_at"])
        self.assertIsNone(state["pending_recommendation"])
        self.assertIsNone(state["emergency_cooldown"])


class TestGetAutoScaleStatus(AutoScalerBase):
    def test_collecting_run_status_note(self):
        health = _health_file(self.tmp_path, started_at=_iso(minutes_ago=20), ended_at=None)
        with self._patch_health(health):
            status = asc.get_auto_scale_status()
        self.assertTrue(status["enabled"])
        self.assertTrue(status["collecting_run"])
        self.assertEqual(status["action"], "none")
        self.assertIn("observing the current run", status["note"])

    def test_pending_completed_run_status(self):
        self.state_path.write_text(
            json.dumps(
                {
                    "pending_recommendation": {
                        "action": "increase",
                        "source": "completed_run",
                        "runtime_minutes": 62.0,
                    }
                }
            ),
            encoding="utf-8",
        )
        status = asc.get_auto_scale_status()
        self.assertEqual(status["action"], "increase")
        self.assertEqual(status["source"], "completed_run")
        self.assertIn("last completed run recommends increase workers ~20%", status["note"])


if __name__ == "__main__":
    unittest.main()
