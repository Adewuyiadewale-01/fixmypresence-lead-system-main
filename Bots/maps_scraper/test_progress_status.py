from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
MAPS_DIR = REPO_ROOT / "Bots" / "maps_scraper"
BOTS_DIR = REPO_ROOT / "Bots"

for path in (str(MAPS_DIR), str(BOTS_DIR), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import config as maps_config  # noqa: E402
import progress_status as ps  # noqa: E402


class ProgressStatusDynamicConfigTests(unittest.TestCase):
    def test_category_complete_uses_current_override_locations_and_keywords(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            overrides_path = tmpdir / "config_overrides.json"
            tasks_path = tmpdir / "completed_tasks.jsonl"
            master_path = tmpdir / "leads_full.csv"
            category_path = tmpdir / "category_state.json"

            overrides_path.write_text(
                json.dumps(
                    {
                        "locations": ["Ikeja", "Yaba"],
                        "keyword_categories": [
                            [
                                "real estate firm",
                                "real estate company",
                            ],
                            ["family office"],
                            ["law firm"],
                            ["insurance firm"],
                        ],
                    }
                ),
                encoding="utf-8",
            )
            tasks_path.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "keyword": keyword,
                            "location": location,
                            "timestamp": "2026-04-13T12:00:00+00:00",
                        }
                    )
                    for location in ["Ikeja", "Yaba"]
                    for keyword in ["real estate firm", "real estate company"]
                )
                + "\n",
                encoding="utf-8",
            )
            master_path.write_text("name,website\nAcme,https://example.com\n", encoding="utf-8")

            with patch.object(maps_config, "CONFIG_OVERRIDES_FILE", overrides_path), \
                 patch.object(ps, "TASKS_PROGRESS_FILE", tasks_path), \
                 patch.object(ps, "CATEGORY_STATE_FILE", category_path), \
                 patch.object(ps, "MASTER_OUTPUT", master_path):
                status = ps.get_progress_status()

        self.assertEqual(status["status"], "category_complete")
        self.assertEqual(status["progress"]["category"]["total"], 4)
        self.assertEqual(status["progress"]["locations"]["total"], 2)
        self.assertEqual(status["progress"]["category"]["keyword_names"], ["real estate firm", "real estate company"])


if __name__ == "__main__":
    unittest.main()
