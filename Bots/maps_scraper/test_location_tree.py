from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
MAPS_DIR = REPO_ROOT / "Bots" / "maps_scraper"
for path in (str(MAPS_DIR), str(REPO_ROOT / "Bots"), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import config as maps_config  # noqa: E402
from location_tree import discover_location_files, parse_locations_file  # noqa: E402


def _write_locations(path: Path, locations: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"LOCATIONS = {json.dumps(locations, ensure_ascii=False, indent=2)}\n",
        encoding="utf-8",
    )


class LocationTreeTests(unittest.TestCase):
    def test_discovery_honors_country_order_and_arbitrary_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            uk_file = root / "united-kingdom" / "england" / "london" / "mayfair" / "LOCATIONS.js"
            nl_a = root / "netherlands" / "north-holland" / "amsterdam" / "LOCATIONS.js"
            nl_b = root / "netherlands" / "drenthe" / "aa-en-hunze" / "LOCATIONS.js"
            for path in (uk_file, nl_a, nl_b):
                _write_locations(path, [path.parent.name])

            files = discover_location_files(
                root,
                "LOCATIONS.js",
                ["netherlands", "united-kingdom"],
            )

        self.assertEqual(
            [path.relative_to(root).as_posix() for path in files],
            [
                "netherlands/drenthe/aa-en-hunze/LOCATIONS.js",
                "netherlands/north-holland/amsterdam/LOCATIONS.js",
                "united-kingdom/england/london/mayfair/LOCATIONS.js",
            ],
        )

    def test_parser_rejects_executable_javascript(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "LOCATIONS.js"
            path.write_text(
                'LOCATIONS = ["Mayfair, Westminster, United Kingdom"];\nalert("no");\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                parse_locations_file(path)

    def test_advance_loads_next_file_and_resets_only_search_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree_root = root / "locations"
            amsterdam = tree_root / "netherlands" / "north-holland" / "amsterdam" / "LOCATIONS.js"
            beemster = tree_root / "netherlands" / "north-holland" / "beemster" / "LOCATIONS.js"
            _write_locations(amsterdam, ["Old Amsterdam"])
            _write_locations(beemster, ["Middenbeemster", "Westbeemster"])

            overrides_path = root / "config_overrides.json"
            state_path = root / "output" / "location_tree_state.json"
            tasks_path = root / "output" / "completed_tasks.jsonl"
            category_path = root / "output" / "category_state.json"
            master_path = root / "output" / "leads_full.csv"
            tasks_path.parent.mkdir(parents=True, exist_ok=True)
            tasks_path.write_text('{"keyword":"one","location":"Old Amsterdam"}\n', encoding="utf-8")
            category_path.write_text('{"category_index":3}\n', encoding="utf-8")
            master_path.write_text("name,website\nAcme,https://example.com\n", encoding="utf-8")
            overrides_path.write_text(
                json.dumps(
                    {
                        "locations": ["Old Amsterdam"],
                        "location_source": {
                            "mode": "local_tree",
                            "tree_root": "locations",
                            "terminal_filename": "LOCATIONS.js",
                            "country_order": ["netherlands"],
                            "initial_file": "netherlands/north-holland/amsterdam/LOCATIONS.js",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(maps_config, "_PROJECT_ROOT", root), \
                 patch.object(maps_config, "CONFIG_OVERRIDES_FILE", overrides_path), \
                 patch.object(maps_config, "LOCATION_TREE_STATE_FILE", state_path), \
                 patch.object(maps_config, "_TASKS_PROGRESS_PATH", tasks_path), \
                 patch.object(maps_config, "TASKS_PROGRESS_FILE", tasks_path), \
                 patch.object(maps_config, "CATEGORY_STATE_FILE", category_path):
                result = maps_config.advance_location_tree_source()

            state = json.loads(state_path.read_text(encoding="utf-8"))
            saved = json.loads(overrides_path.read_text(encoding="utf-8"))
            tasks_exists = tasks_path.exists()
            category_state = json.loads(category_path.read_text(encoding="utf-8"))
            master_contents = master_path.read_text(encoding="utf-8")

        self.assertTrue(result["advanced"])
        self.assertEqual(
            result["current_file"],
            "netherlands/north-holland/beemster/LOCATIONS.js",
        )
        self.assertEqual(result["locations_loaded"], 2)
        self.assertFalse(tasks_exists)
        self.assertEqual(category_state, {"category_index": 0})
        self.assertEqual(
            master_contents,
            "name,website\nAcme,https://example.com\n",
        )
        self.assertIn(
            "netherlands/north-holland/amsterdam/LOCATIONS.js",
            state["completed_files"],
        )
        self.assertEqual(saved["location_resolved_locations"], ["Middenbeemster", "Westbeemster"])


if __name__ == "__main__":
    unittest.main()
