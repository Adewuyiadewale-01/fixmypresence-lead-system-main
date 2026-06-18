from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
MAPS_DIR = REPO_ROOT / "Bots" / "maps_scraper"
BOTS_DIR = REPO_ROOT / "Bots"

for path in (str(MAPS_DIR), str(BOTS_DIR), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


if "playwright.sync_api" not in sys.modules:
    playwright_mod = types.ModuleType("playwright")
    sync_api_mod = types.ModuleType("playwright.sync_api")
    sync_api_mod.Playwright = object
    sync_api_mod.TimeoutError = TimeoutError
    sync_api_mod.sync_playwright = lambda: None
    sys.modules.setdefault("playwright", playwright_mod)
    sys.modules["playwright.sync_api"] = sync_api_mod

import google_maps_scraper as gms  # noqa: E402


class GoogleMapsEfficiencyTests(unittest.TestCase):
    def test_search_telemetry_derives_quality_rates(self):
        telemetry = gms.SearchTelemetry(
            search_duration_ms=2000,
            cards_seen=4,
            leads_returned=2,
            leads_with_website=1,
            batch_read_complete=1,
            fallback_to_existing_extract_card=3,
            cards_completed_without_sidebar=2,
            sidebar_click_attempts=2,
            sidebar_click_successes=1,
            sidebar_extraction_failures=1,
            card_extract_duration_ms=400,
            sidebar_wait_duration_ms=300,
            identity_cards_submitted=4,
            cards_with_strong_place_key=3,
            early_dedupe_approved=1,
            early_duplicates_skipped=2,
            historical_duplicates_skipped=1,
            inflight_duplicates_skipped=1,
            coordinator_requests=1,
            coordinator_lookup_duration_ms=2,
            coordinator_max_lookup_duration_ms=2,
        ).as_dict()

        self.assertEqual(telemetry["website_found_rate"], 50.0)
        self.assertEqual(telemetry["avg_time_per_lead_ms"], 1000.0)
        self.assertEqual(telemetry["avg_time_per_website_found_lead_ms"], 2000.0)
        self.assertEqual(telemetry["batch_read_complete_rate"], 25.0)
        self.assertEqual(telemetry["fallback_rate"], 75.0)
        self.assertEqual(telemetry["sidebar_click_rate"], 50.0)
        self.assertEqual(telemetry["sidebar_success_rate"], 50.0)
        self.assertEqual(telemetry["sidebar_failure_rate"], 50.0)
        self.assertEqual(telemetry["no_sidebar_completion_rate"], 50.0)
        self.assertEqual(telemetry["strong_place_key_coverage"], 75.0)
        self.assertEqual(telemetry["early_duplicate_rate"], 66.7)
        self.assertEqual(telemetry["extraction_avoidance_rate"], 50.0)
        self.assertEqual(telemetry["average_coordinator_latency_ms"], 2.0)

    def test_batch_card_helper_requires_complete_visible_website_data(self):
        scraper = gms.GoogleMapsScraper(headless=True)
        complete = scraper._lead_from_batch_card(
            {
                "name": "ACME Advisory",
                "place_url": "https://www.google.com/maps/place/acme",
                "website": "https://acme.example",
            },
            "accountant",
            "Amsterdam",
        )
        missing_website = scraper._lead_from_batch_card(
            {
                "name": "ACME Advisory",
                "place_url": "https://www.google.com/maps/place/acme",
                "website": "",
            },
            "accountant",
            "Amsterdam",
        )

        self.assertIsNotNone(complete)
        self.assertEqual(complete.website, "https://acme.example")
        self.assertIsNone(missing_website)

    def test_early_duplicate_skip_never_calls_card_extractor(self):
        class FakeCards:
            def count(self):
                return 1

            def nth(self, index):
                return object()

        scraper = gms.GoogleMapsScraper(headless=True)
        scraper._page = object()
        gate = lambda candidates: {
            "decisions": [{
                "candidate_index": 0,
                "decision": "SKIP_HISTORICAL",
                "reservation_token": None,
            }],
            "lookup_duration_ms": 0.1,
        }

        with (
            patch.object(scraper, "_attempt_navigation", return_value=True),
            patch.object(scraper, "_check_captcha"),
            patch.object(scraper, "_cards_locator", return_value=FakeCards()),
            patch.object(
                scraper,
                "_read_visible_card_data",
                return_value=[{
                    "name": "ACME",
                    "place_url": "https://www.google.com/maps/place/ACME/data=!1sChIJABC!4m2",
                    "website": "",
                }],
            ),
            patch.object(scraper, "_scroll_results_panel"),
            patch.object(scraper, "_extract_card") as extract_card,
            patch.object(gms, "human_throttle"),
        ):
            leads = scraper.search("accountant", "Amsterdam", identity_gate=gate)

        self.assertEqual(leads, [])
        extract_card.assert_not_called()
        self.assertEqual(scraper.last_telemetry["historical_duplicates_skipped"], 1)
        self.assertEqual(scraper.last_telemetry["sidebar_fallbacks_avoided_estimate"], 1)


if __name__ == "__main__":
    unittest.main()
