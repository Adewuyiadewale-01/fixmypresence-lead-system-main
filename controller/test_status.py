from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_DIR = REPO_ROOT / "controller"
for path in (str(CONTROLLER_DIR), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import status as controller_status  # noqa: E402


class ControllerLocationTreeStatusTests(unittest.TestCase):
    def test_completed_categories_advance_unfinished_location_file(self):
        with patch.object(
            controller_status,
            "get_progress_status",
            return_value={"has_progress": True, "status": "all_complete"},
        ), patch.object(
            controller_status,
            "get_location_tree_status",
            return_value={
                "enabled": True,
                "current_file": "netherlands/amsterdam/LOCATIONS.js",
                "current_file_completed": False,
            },
        ):
            self.assertEqual(controller_status.get_next_action(), "next_location_file")

    def test_completed_tree_stops(self):
        with patch.object(
            controller_status,
            "get_progress_status",
            return_value={"has_progress": True, "status": "all_complete"},
        ), patch.object(
            controller_status,
            "get_location_tree_status",
            return_value={
                "enabled": True,
                "current_file": "netherlands/amsterdam/LOCATIONS.js",
                "current_file_completed": True,
            },
        ):
            self.assertEqual(controller_status.get_next_action(), "done")


if __name__ == "__main__":
    unittest.main()
