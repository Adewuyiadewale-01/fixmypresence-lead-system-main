from __future__ import annotations

import sys
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_DIR = REPO_ROOT / "controller"

for path in (str(CONTROLLER_DIR), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

spec = importlib.util.spec_from_file_location("controller_main_under_test", CONTROLLER_DIR / "main.py")
controller_main = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(controller_main)


class ControllerDeliveryRetryTests(unittest.TestCase):
    def test_execute_once_advances_location_tree_and_starts_new_file(self):
        transition = {
            "advanced": True,
            "current_file": "netherlands/north-holland/beemster/LOCATIONS.js",
            "locations_loaded": 10,
        }
        with patch.object(controller_main, "print_status_summary"), \
             patch.object(controller_main, "get_next_action", return_value="next_location_file"), \
             patch.object(controller_main, "advance_location_tree_source", return_value=transition), \
             patch.object(controller_main, "run_scraper", return_value=(0, "complete")) as run_mock:
            should_retry = controller_main._execute_controller_once(
                headless=False,
                parallel=True,
                approve_next_category=True,
            )

        self.assertFalse(should_retry)
        run_mock.assert_called_once_with(headless=False, parallel=True)

    def test_execute_once_marks_delivery_timeout_as_retryable(self):
        with patch.object(controller_main, "print_status_summary"), \
             patch.object(controller_main, "get_next_action", return_value="resume"), \
             patch.object(
                 controller_main,
                 "run_scraper",
                 return_value=(
                     controller_main.DELIVERY_FAILED_EXIT_CODE,
                     "Scraper paused after a delivery timeout. Pending leads were preserved for retry.",
                 ),
             ):
            should_retry = controller_main._execute_controller_once(
                headless=False,
                parallel=True,
                approve_next_category=False,
            )

        self.assertTrue(should_retry)

    def test_main_waits_then_retries_after_delivery_timeout(self):
        with patch.object(controller_main, "check_scraper_ready", return_value=True), \
             patch.object(controller_main, "_execute_controller_once", side_effect=[True, False]) as execute_mock, \
             patch.object(controller_main, "_wait_for_delivery_retry") as wait_mock:
            controller_main.main(headless=False, parallel=True, approve_next_category=False)

        self.assertEqual(execute_mock.call_count, 2)
        wait_mock.assert_called_once_with(controller_main.DELIVERY_RETRY_COOLDOWN_MINUTES, 1)


if __name__ == "__main__":
    unittest.main()
