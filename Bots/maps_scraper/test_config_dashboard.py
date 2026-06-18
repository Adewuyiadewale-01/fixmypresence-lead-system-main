from __future__ import annotations

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


class DashboardConfigTests(unittest.TestCase):
    def test_local_tree_source_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            overrides_path = Path(tmp) / "config_overrides.json"
            with patch.object(maps_config, "CONFIG_OVERRIDES_FILE", overrides_path):
                saved = maps_config.save_dashboard_config(
                    {
                        "runtime": {},
                        "guardrails": {},
                        "locations": ["Existing location"],
                        "location_source": {
                            "mode": "local_tree",
                            "tree_root": "Bots/address-bot/out/locations",
                            "terminal_filename": "LOCATIONS.js",
                            "country_order": ["netherlands", "united-kingdom"],
                            "initial_file": "netherlands/north-holland/amsterdam/LOCATIONS.js",
                        },
                        "keyword_categories": [],
                    }
                )

        source = saved["location_source"]
        self.assertEqual(source["mode"], "local_tree")
        self.assertEqual(source["terminal_filename"], "LOCATIONS.js")
        self.assertEqual(source["country_order"], ["netherlands", "united-kingdom"])
        self.assertEqual(
            source["initial_file"],
            "netherlands/north-holland/amsterdam/LOCATIONS.js",
        )

    def test_delivery_retry_cooldown_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            overrides_path = Path(tmp) / "config_overrides.json"
            with patch.object(maps_config, "CONFIG_OVERRIDES_FILE", overrides_path):
                saved = maps_config.save_dashboard_config(
                    {
                        "runtime": {
                            "num_workers": 4,
                            "batch_size": 40,
                            "website_status_workers": 8,
                            "captcha_backoff_s": 45,
                            "inter_request_delay_min": 6,
                            "inter_request_delay_max": 10,
                            "worker_stagger_delay_min": 3,
                            "worker_stagger_delay_max": 8,
                            "category_advance_timeout_minutes": 15,
                            "delivery_retry_cooldown_minutes": 12,
                            "sidebar_adaptive_max_wait_ms": 1300,
                            "maps_telemetry_enabled": True,
                            "maps_batch_card_read_enabled": True,
                            "maps_adaptive_sidebar_wait_enabled": True,
                            "maps_early_dedupe_enabled": True,
                            "maps_early_dedupe_request_timeout_ms": 1500,
                            "maps_early_dedupe_reservation_ttl_seconds": 1800,
                            "maps_adaptive_worker_scaling_enabled": False,
                            "maps_max_workers": 7,
                            "maps_min_workers": 2,
                            "maps_worker_ramp_step": 1,
                            "maps_worker_ramp_check_interval_seconds": 300,
                            "maps_worker_scale_down_check_interval_seconds": 120,
                            "maps_worker_pressure_confirmation_checks": 2,
                            "maps_worker_cpu_ceiling_percent": 88,
                            "maps_worker_memory_pressure_ceiling_percent": 85,
                            "maps_min_website_found_rate_for_ramp": 75,
                            "maps_max_error_rate_for_ramp": 1,
                        },
                        "guardrails": {},
                        "locations": ["Mushin"],
                        "keyword_categories": [["one"], ["two"], ["three"], ["four"]],
                    }
                )

                loaded = maps_config.get_dashboard_config()

        self.assertEqual(saved["runtime"]["delivery_retry_cooldown_minutes"], 12)
        self.assertEqual(loaded["runtime"]["delivery_retry_cooldown_minutes"], 12)
        self.assertEqual(saved["runtime"]["sidebar_adaptive_max_wait_ms"], 1300)
        self.assertEqual(loaded["runtime"]["maps_max_workers"], 7)
        self.assertEqual(loaded["runtime"]["maps_min_workers"], 2)
        self.assertEqual(loaded["runtime"]["maps_worker_scale_down_check_interval_seconds"], 120)
        self.assertEqual(loaded["runtime"]["maps_worker_pressure_confirmation_checks"], 2)
        self.assertFalse(loaded["runtime"]["maps_adaptive_worker_scaling_enabled"])
        self.assertTrue(loaded["runtime"]["maps_early_dedupe_enabled"])
        self.assertEqual(loaded["runtime"]["maps_early_dedupe_request_timeout_ms"], 1500)
        self.assertEqual(loaded["runtime"]["maps_early_dedupe_reservation_ttl_seconds"], 1800)


if __name__ == "__main__":
    unittest.main()
