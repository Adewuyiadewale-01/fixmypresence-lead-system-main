"""
Tests for dashboard/auto_scaler.py

Covers:
  - _compute_new_workers() arithmetic
  - _compute_action() health-signal logic
  - check_and_apply() guard conditions and apply path
  - record_manual_change() clock reset
  - get_auto_scale_status() output shape
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup — mirror what auto_scaler.py itself does
# ---------------------------------------------------------------------------
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


# ===========================================================================
# 1. Worker arithmetic
# ===========================================================================

class TestComputeNewWorkers(unittest.TestCase):

    def test_increase_20_percent(self):
        # 4 * 1.20 = 4.8 → rounds to 5
        self.assertEqual(asc._compute_new_workers(4, "increase"), 5)

    def test_decrease_20_percent(self):
        # 4 * 0.80 = 3.2 → rounds to 3
        self.assertEqual(asc._compute_new_workers(4, "decrease"), 3)

    def test_increase_always_moves_at_least_one(self):
        # 1 * 1.20 = 1.2 → rounds to 1, but minimum +1 applies → 2
        self.assertEqual(asc._compute_new_workers(1, "increase"), 2)

    def test_decrease_always_moves_at_least_one(self):
        # 2 * 0.80 = 1.6 → rounds to 2, but minimum -1 applies → 1
        self.assertEqual(asc._compute_new_workers(2, "decrease"), 1)

    def test_increase_capped_at_max(self):
        self.assertEqual(asc._compute_new_workers(asc.MAX_WORKERS, "increase"), asc.MAX_WORKERS)

    def test_decrease_capped_at_min(self):
        self.assertEqual(asc._compute_new_workers(asc.MIN_WORKERS, "decrease"), asc.MIN_WORKERS)

    def test_no_action_returns_current(self):
        self.assertEqual(asc._compute_new_workers(6, "none"), 6)

    def test_larger_counts_scale_proportionally(self):
        # 10 * 1.20 = 12
        self.assertEqual(asc._compute_new_workers(10, "increase"), 12)
        # 10 * 0.80 = 8
        self.assertEqual(asc._compute_new_workers(10, "decrease"), 8)


# ===========================================================================
# 2. Health-signal → action
# ===========================================================================

def _health_file(tmp_dir: Path, **kwargs) -> Path:
    """Write a minimal health JSON and return its path."""
    defaults = {
        "run_searches": 50,
        "run_batches": 2,
        "run_leads": 100,
        "run_website_found": 40,
        "run_website_total": 50,
        "captcha_count": 0,
        "error_count": 0,
    }
    defaults.update(kwargs)
    p = tmp_dir / "scraper_health.json"
    p.write_text(json.dumps(defaults), encoding="utf-8")
    return p


class TestComputeAction(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _patch(self, health_path):
        """Patch SCRAPER_HEALTH_FILE import inside _compute_action."""
        mock_cfg = MagicMock()
        mock_cfg.SCRAPER_HEALTH_FILE = str(health_path)
        return patch.dict("sys.modules", {"maps_scraper.config": mock_cfg})

    def test_increase_when_high_yield_and_high_website_rate(self):
        p = _health_file(
            self.tmp_path,
            run_leads=120,        # 120/50*100 = 240 >= 220 HIGH_YIELD
            run_website_found=40, # 40/50 = 80% >= 75% HIGH_WEBSITE
        )
        with self._patch(p):
            self.assertEqual(asc._compute_action(), "increase")

    def test_decrease_when_captcha_detected(self):
        p = _health_file(self.tmp_path, captcha_count=1)
        with self._patch(p):
            self.assertEqual(asc._compute_action(), "decrease")

    def test_decrease_when_low_website_rate(self):
        p = _health_file(
            self.tmp_path,
            run_website_found=25,  # 25/50 = 50% < 55% LOW_WEBSITE
        )
        with self._patch(p):
            self.assertEqual(asc._compute_action(), "decrease")

    def test_decrease_when_low_yield(self):
        p = _health_file(
            self.tmp_path,
            run_leads=50,          # 50/50*100 = 100 < 120 LOW_YIELD
            run_website_found=40,  # 80% website — healthy, but yield is low
        )
        with self._patch(p):
            self.assertEqual(asc._compute_action(), "decrease")

    def test_none_when_error_spike(self):
        p = _health_file(self.tmp_path, error_count=5)
        with self._patch(p):
            self.assertEqual(asc._compute_action(), "none")

    def test_none_when_not_enough_data(self):
        p = _health_file(self.tmp_path, run_searches=10, run_batches=0)
        with self._patch(p):
            self.assertEqual(asc._compute_action(), "none")

    def test_none_when_in_optimal_range(self):
        # website_rate = 64% (between 55-75%), yield = 160 (between 120-220)
        p = _health_file(
            self.tmp_path,
            run_leads=80,          # 80/50*100 = 160
            run_website_found=32,  # 32/50 = 64%
        )
        with self._patch(p):
            self.assertEqual(asc._compute_action(), "none")

    def test_none_when_health_file_missing(self):
        mock_cfg = MagicMock()
        mock_cfg.SCRAPER_HEALTH_FILE = str(self.tmp_path / "nonexistent.json")
        with patch.dict("sys.modules", {"maps_scraper.config": mock_cfg}):
            self.assertEqual(asc._compute_action(), "none")

    def test_none_when_import_fails(self):
        with patch.dict("sys.modules", {"maps_scraper.config": None}):
            # ImportError path
            self.assertEqual(asc._compute_action(), "none")


# ===========================================================================
# 3. check_and_apply() guard conditions
# ===========================================================================

class TestCheckAndApply(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "auto_scale_state.json"
        # Redirect state file to temp dir
        self.patcher_state = patch.object(asc, "AUTO_SCALE_STATE_FILE", self.state_path)
        self.patcher_state.start()

    def tearDown(self):
        self.patcher_state.stop()
        self.tmp.cleanup()

    def _mock_action(self, action):
        return patch.object(asc, "_compute_action", return_value=action)

    def _mock_config(self, workers=4):
        mock_cfg_mod = MagicMock()
        mock_cfg_mod.get_dashboard_config.return_value = {"runtime": {"num_workers": workers}}
        mock_cfg_mod.save_dashboard_config = MagicMock()
        return patch.dict("sys.modules", {"maps_scraper.config": mock_cfg_mod}), mock_cfg_mod

    # --- no actionable recommendation ---
    def test_no_action_returns_no_apply(self):
        with self._mock_action("none"):
            result = asc.check_and_apply()
        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "no_actionable_recommendation")

    # --- recommendation just appeared: start tracking clock ---
    def test_new_recommendation_starts_clock(self):
        with self._mock_action("increase"):
            result = asc.check_and_apply()
        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "recommendation_changed")
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["recommendation"], "increase")
        self.assertIsNotNone(state["recommendation_first_seen_at"])

    # --- manual change too recent ---
    def test_manual_too_recent_blocks(self):
        self.state_path.write_text(json.dumps({
            "last_manual_change_at": _iso(minutes_ago=1),  # 1 min ago, within STABLE_MINUTES=5
        }))
        with self._mock_action("increase"):
            result = asc.check_and_apply()
        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "manual_too_recent")
        self.assertGreater(result["minutes_left"], 0)

    # --- manual change old enough to not block ---
    def test_manual_old_enough_does_not_block(self):
        self.state_path.write_text(json.dumps({
            "last_manual_change_at": _iso(minutes_ago=10),  # older than STABLE_MINUTES=5
            "recommendation": "increase",
            "recommendation_first_seen_at": _iso(minutes_ago=10),
        }))
        cfg_patch, mock_mod = self._mock_config(workers=4)
        with self._mock_action("increase"), cfg_patch:
            result = asc.check_and_apply()
        self.assertTrue(result["applied"])

    # --- signal not stable long enough ---
    def test_not_stable_enough_waits(self):
        self.state_path.write_text(json.dumps({
            "recommendation": "increase",
            "recommendation_first_seen_at": _iso(minutes_ago=2),  # only 2 min, need 5
        }))
        with self._mock_action("increase"):
            result = asc.check_and_apply()
        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "waiting_for_stability")
        self.assertGreater(result["minutes_left"], 0)

    # --- recommendation changed mid-wait resets clock ---
    def test_recommendation_change_resets_clock(self):
        old_time = _iso(minutes_ago=3)
        self.state_path.write_text(json.dumps({
            "recommendation": "increase",
            "recommendation_first_seen_at": old_time,
        }))
        with self._mock_action("decrease"):  # flipped to decrease
            result = asc.check_and_apply()
        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "recommendation_changed")
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["recommendation"], "decrease")
        # Clock should be newer than old_time
        self.assertGreater(state["recommendation_first_seen_at"], old_time)

    # --- already at boundary ---
    def test_already_at_max_workers_no_apply(self):
        self.state_path.write_text(json.dumps({
            "recommendation": "increase",
            "recommendation_first_seen_at": _iso(minutes_ago=10),
        }))
        cfg_patch, _ = self._mock_config(workers=asc.MAX_WORKERS)
        with self._mock_action("increase"), cfg_patch:
            result = asc.check_and_apply()
        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "already_at_boundary")

    def test_already_at_min_workers_no_apply(self):
        self.state_path.write_text(json.dumps({
            "recommendation": "decrease",
            "recommendation_first_seen_at": _iso(minutes_ago=10),
        }))
        cfg_patch, _ = self._mock_config(workers=asc.MIN_WORKERS)
        with self._mock_action("decrease"), cfg_patch:
            result = asc.check_and_apply()
        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "already_at_boundary")

    # --- auto-applied too recently ---
    def test_auto_applied_too_recently_blocks(self):
        self.state_path.write_text(json.dumps({
            "recommendation": "increase",
            "recommendation_first_seen_at": _iso(minutes_ago=10),
            "last_auto_apply_at": _iso(minutes_ago=2),  # 2 min ago, within 5 min
        }))
        cfg_patch, _ = self._mock_config(workers=4)
        with self._mock_action("increase"), cfg_patch:
            result = asc.check_and_apply()
        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "auto_applied_too_recently")

    # --- successful apply ---
    def test_successful_increase_apply(self):
        self.state_path.write_text(json.dumps({
            "recommendation": "increase",
            "recommendation_first_seen_at": _iso(minutes_ago=10),
        }))
        cfg_patch, mock_mod = self._mock_config(workers=4)
        with self._mock_action("increase"), cfg_patch:
            result = asc.check_and_apply()

        self.assertTrue(result["applied"])
        self.assertEqual(result["action"], "increase")
        self.assertEqual(result["from_workers"], 4)
        self.assertEqual(result["new_workers"], 5)  # 4 * 1.20 = 4.8 → 5

        # save_dashboard_config called with new worker count
        saved_payload = mock_mod.save_dashboard_config.call_args[0][0]
        self.assertEqual(saved_payload["runtime"]["num_workers"], 5)

        # State file updated
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["last_auto_applied_workers"], 5)
        self.assertEqual(state["last_auto_from_workers"], 4)
        self.assertEqual(state["last_auto_action"], "increase")
        self.assertIsNotNone(state["last_auto_apply_at"])

    def test_successful_decrease_apply(self):
        self.state_path.write_text(json.dumps({
            "recommendation": "decrease",
            "recommendation_first_seen_at": _iso(minutes_ago=10),
        }))
        cfg_patch, mock_mod = self._mock_config(workers=8)
        with self._mock_action("decrease"), cfg_patch:
            result = asc.check_and_apply()

        self.assertTrue(result["applied"])
        self.assertEqual(result["new_workers"], 6)  # 8 * 0.80 = 6.4 → 6

    def test_state_file_missing_starts_fresh(self):
        """No state file → treated as empty state, starts clock on first call."""
        with self._mock_action("increase"):
            result = asc.check_and_apply()
        self.assertFalse(result["applied"])
        # Either recommendation_changed or started_tracking — either way, not applied
        self.assertIn(result["reason"], ("recommendation_changed", "started_tracking"))


# ===========================================================================
# 4. record_manual_change()
# ===========================================================================

class TestRecordManualChange(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "auto_scale_state.json"
        self.patcher = patch.object(asc, "AUTO_SCALE_STATE_FILE", self.state_path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_records_timestamp_and_resets_recommendation(self):
        self.state_path.write_text(json.dumps({
            "recommendation": "increase",
            "recommendation_first_seen_at": _iso(minutes_ago=3),
        }))
        asc.record_manual_change()
        state = json.loads(self.state_path.read_text())
        self.assertIsNotNone(state["last_manual_change_at"])
        self.assertIsNone(state["recommendation"])
        self.assertIsNone(state["recommendation_first_seen_at"])

    def test_manual_change_then_check_is_blocked(self):
        asc.record_manual_change()
        with patch.object(asc, "_compute_action", return_value="increase"):
            result = asc.check_and_apply()
        self.assertEqual(result["reason"], "manual_too_recent")


# ===========================================================================
# 5. get_auto_scale_status()
# ===========================================================================

class TestGetAutoScaleStatus(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "auto_scale_state.json"
        self.patcher = patch.object(asc, "AUTO_SCALE_STATE_FILE", self.state_path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_empty_state_returns_safe_defaults(self):
        status = asc.get_auto_scale_status()
        self.assertTrue(status["enabled"])
        self.assertEqual(status["action"], "none")
        self.assertIsNone(status["stable_minutes"])
        self.assertIsNone(status["minutes_left"])
        self.assertFalse(status["manual_blocking"])

    def test_tracking_in_progress_shows_countdown(self):
        self.state_path.write_text(json.dumps({
            "recommendation": "increase",
            "recommendation_first_seen_at": _iso(minutes_ago=2),
        }))
        status = asc.get_auto_scale_status()
        self.assertEqual(status["action"], "increase")
        self.assertAlmostEqual(status["stable_minutes"], 2.0, delta=0.2)
        self.assertAlmostEqual(status["minutes_left"], asc.STABLE_MINUTES - 2.0, delta=0.2)

    def test_manual_blocking_true_within_window(self):
        self.state_path.write_text(json.dumps({
            "last_manual_change_at": _iso(minutes_ago=1),
        }))
        status = asc.get_auto_scale_status()
        self.assertTrue(status["manual_blocking"])

    def test_manual_blocking_false_after_window(self):
        self.state_path.write_text(json.dumps({
            "last_manual_change_at": _iso(minutes_ago=asc.STABLE_MINUTES + 1),
        }))
        status = asc.get_auto_scale_status()
        self.assertFalse(status["manual_blocking"])

    def test_last_auto_apply_fields_propagated(self):
        self.state_path.write_text(json.dumps({
            "last_auto_apply_at": _iso(minutes_ago=10),
            "last_auto_applied_workers": 5,
            "last_auto_from_workers": 4,
            "last_auto_action": "increase",
        }))
        status = asc.get_auto_scale_status()
        self.assertEqual(status["last_auto_applied_workers"], 5)
        self.assertEqual(status["last_auto_from_workers"], 4)
        self.assertEqual(status["last_auto_action"], "increase")


if __name__ == "__main__":
    unittest.main()
