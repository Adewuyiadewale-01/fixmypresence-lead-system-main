from __future__ import annotations

import sys
import tempfile
import unittest
import queue as std_queue
import threading
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
MAPS_DIR = REPO_ROOT / "Bots" / "maps_scraper"
BOTS_DIR = REPO_ROOT / "Bots"

for path in (str(MAPS_DIR), str(BOTS_DIR), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import parallel_scraper as ps  # noqa: E402
import utils  # noqa: E402


class ParallelScraperHardeningTests(unittest.TestCase):
    def test_load_last_batch_number_resumes_from_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            (output_dir / "leads_batch_001.csv").write_text("a", encoding="utf-8")
            (output_dir / "leads_batch_007.csv").write_text("b", encoding="utf-8")

            with patch.object(ps, "OUTPUT_DIR", output_dir), patch.object(ps, "MASTER_OUTPUT", output_dir / "leads_full.csv"), patch.object(ps, "TASKS_PROGRESS_FILE", output_dir / "completed_tasks.jsonl"):
                orchestrator = ps.ParallelOrchestrator()

            self.assertEqual(orchestrator.batch_number, 7)

    def test_pending_batch_roundtrip_uses_atomic_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending_path = Path(tmp) / "pending_batch.json"
            with patch.object(ps, "OUTPUT_DIR", Path(tmp)), patch.object(ps, "PENDING_BATCH_FILE", pending_path), patch.object(ps, "MASTER_OUTPUT", Path(tmp) / "leads_full.csv"), patch.object(ps, "TASKS_PROGRESS_FILE", Path(tmp) / "completed_tasks.jsonl"):
                orchestrator = ps.ParallelOrchestrator()
                batch = [{"name": "ACME", "website": "acme.com", "location": "Amsterdam", "keyword": "accountant"}]
                orchestrator._save_pending_batch(batch)
                restored = orchestrator._load_pending_batch()

            self.assertEqual(restored, batch)

    def test_run_lock_blocks_second_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "parallel_scraper.lock"
            with patch.object(ps, "OUTPUT_DIR", Path(tmp)), patch.object(ps, "RUN_LOCK_FILE", lock_path), patch.object(ps, "MASTER_OUTPUT", Path(tmp) / "leads_full.csv"), patch.object(ps, "TASKS_PROGRESS_FILE", Path(tmp) / "completed_tasks.jsonl"):
                orchestrator_one = ps.ParallelOrchestrator()
                orchestrator_two = ps.ParallelOrchestrator()

                orchestrator_one._acquire_run_lock()
                with self.assertRaises(RuntimeError):
                    orchestrator_two._acquire_run_lock()
                orchestrator_one._release_run_lock()

            self.assertFalse(lock_path.exists())

    def test_name_key_uses_location_to_reduce_false_dedupes(self):
        key_one = ps.ParallelOrchestrator._build_name_key("Acme Advisory", "Amsterdam")
        key_two = ps.ParallelOrchestrator._build_name_key("Acme Advisory", "Rotterdam")
        self.assertNotEqual(key_one, key_two)

    def test_extract_place_key_uses_stable_google_place_identity(self):
        url_one = (
            "https://www.google.com/maps/place/MEXEM+Europa/data="
            "!4m7!3m6!1s0x47c60a718f3aaaab:0x7b6219da1c3980a9!"
            "8m2!3d52.3193126!4d4.8705842!16s%2Fg%2F11h4bsyd4s"
            "!19sChIJq6o6j3EKxkcRqYA5HNoZYns?authuser=0&hl=en&rclk=1"
        )
        url_two = (
            "https://www.google.com/maps/place/MEXEM+Europa/data="
            "!4m7!3m6!1s0x47c60a718f3aaaab:0x7b6219da1c3980a9!"
            "8m2!3d52.319313!4d4.870584!16s%2Fg%2F11h4bsyd4s"
            "!19sChIJq6o6j3EKxkcRqYA5HNoZYns?authuser=0&hl=en&rclk=1"
        )

        self.assertEqual(utils.extract_place_key(url_one), utils.extract_place_key(url_two))

    def test_strong_place_key_rejects_path_only_fallback(self):
        self.assertEqual(
            utils.extract_strong_place_key("https://www.google.com/maps/place/Acme?cid=123"),
            "cid:123",
        )
        self.assertEqual(
            utils.extract_strong_place_key("https://www.google.com/maps/place/Acme?ftid=0xABC"),
            "ftid:0xabc",
        )
        self.assertEqual(
            utils.extract_strong_place_key("https://www.google.com/maps/place/Acme/data=!3m1!1sChIJABC!4m2"),
            "1s:chijabc",
        )
        self.assertIsNone(utils.extract_strong_place_key("https://www.google.com/maps/place/Acme"))

    def test_early_dedupe_coordinator_reserves_atomically(self):
        coordinator = ps.EarlyDedupeCoordinator(
            std_queue.Queue(), {}, threading.Lock(), {"1s:historical"}, 1800
        )
        response = coordinator._reserve_batch({
            "request_id": "r1",
            "worker_id": 1,
            "candidates": [
                {"candidate_index": 0, "place_key": "1s:new"},
                {"candidate_index": 1, "place_key": "1s:new"},
                {"candidate_index": 2, "place_key": "1s:historical"},
                {"candidate_index": 3, "place_key": None},
            ],
        })

        self.assertEqual(
            [item["decision"] for item in response["decisions"]],
            ["EXTRACT_NEW", "SKIP_SAME_BATCH", "SKIP_HISTORICAL", "EXTRACT_UNIDENTIFIED"],
        )
        second = coordinator._reserve_batch({
            "request_id": "r2",
            "worker_id": 2,
            "candidates": [{"candidate_index": 0, "place_key": "1s:new"}],
        })
        self.assertEqual(second["decisions"][0]["decision"], "SKIP_IN_FLIGHT")

    def test_early_dedupe_commit_release_and_expiry(self):
        coordinator = ps.EarlyDedupeCoordinator(
            std_queue.Queue(), {}, threading.Lock(), set(), 60
        )
        first = coordinator._reserve_batch({
            "request_id": "r1",
            "worker_id": 1,
            "candidates": [{"candidate_index": 0, "place_key": "1s:commit"}],
        })
        coordinator._finalize({
            "reservation_token": first["decisions"][0]["reservation_token"],
            "success": True,
        })
        committed = coordinator._reserve_batch({
            "request_id": "r2",
            "worker_id": 2,
            "candidates": [{"candidate_index": 0, "place_key": "1s:commit"}],
        })
        self.assertEqual(committed["decisions"][0]["decision"], "SKIP_IN_FLIGHT")

        released = coordinator._reserve_batch({
            "request_id": "r3",
            "worker_id": 1,
            "candidates": [{"candidate_index": 0, "place_key": "1s:release"}],
        })
        coordinator._finalize({
            "reservation_token": released["decisions"][0]["reservation_token"],
            "success": False,
        })
        retried = coordinator._reserve_batch({
            "request_id": "r4",
            "worker_id": 2,
            "candidates": [{"candidate_index": 0, "place_key": "1s:release"}],
        })
        self.assertEqual(retried["decisions"][0]["decision"], "EXTRACT_NEW")

        coordinator.reservations["1s:release"].reserved_at = ps.time.monotonic() - 61
        self.assertEqual(coordinator._expire_reservations(), 1)
        self.assertNotIn("1s:release", coordinator.reservations)

    def test_early_dedupe_coordinator_thread_routes_worker_response(self):
        request_queue = std_queue.Queue()
        response_queue = std_queue.Queue()
        coordinator = ps.EarlyDedupeCoordinator(
            request_queue,
            {3: response_queue},
            threading.Lock(),
            set(),
            1800,
        )
        coordinator.start()
        try:
            request_queue.put({
                "type": "reserve",
                "request_id": "threaded",
                "worker_id": 3,
                "candidates": [{"candidate_index": 0, "place_key": "1s:threaded"}],
            })
            response = response_queue.get(timeout=1)
        finally:
            coordinator.stop()

        self.assertEqual(response["request_id"], "threaded")
        self.assertEqual(response["decisions"][0]["decision"], "EXTRACT_NEW")

    def test_place_key_dedupes_same_business_across_different_search_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ps, "OUTPUT_DIR", Path(tmp)), patch.object(ps, "MASTER_OUTPUT", Path(tmp) / "leads_full.csv"), patch.object(ps, "TASKS_PROGRESS_FILE", Path(tmp) / "completed_tasks.jsonl"):
                orchestrator = ps.ParallelOrchestrator()

            first = {
                "name": "WEBB Traders",
                "website": "http://www.webbtraders.nl/",
                "location": "Aalsmeerwegbuurt Oost, Amsterdam, Netherlands",
                "keyword": "proprietary trading",
                "place_url": "https://www.google.com/maps/place/WEBB+Traders/data=!4m7!3m6!1s0x47c609b0897498b1:0x53ccf6f93233263e!8m2!3d52.3752734!4d4.8917619!16s%2Fg%2F1td64hrq!19sChIJsZh0ibAJxkcRPiYzMvn2zFM?authuser=0&hl=en&rclk=1",
            }
            second = {
                "name": "WEBB Traders",
                "website": "",
                "location": "Afrikahaven, Amsterdam, Netherlands",
                "keyword": "proprietary trading",
                "place_url": "https://www.google.com/maps/place/WEBB+Traders/data=!4m7!3m6!1s0x47c609b0897498b1:0x53ccf6f93233263e!8m2!3d52.3752734!4d4.8917619!16s%2Fg%2F1td64hrq!19sChIJsZh0ibAJxkcRPiYzMvn2zFM?authuser=0&hl=en&rclk=1",
            }

            self.assertTrue(orchestrator._dedupe_lead(first))
            self.assertFalse(orchestrator._dedupe_lead(second))

    def test_telemetry_summary_handles_zero_and_rates(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ps, "OUTPUT_DIR", Path(tmp)), patch.object(ps, "MASTER_OUTPUT", Path(tmp) / "leads_full.csv"), patch.object(ps, "TASKS_PROGRESS_FILE", Path(tmp) / "completed_tasks.jsonl"):
                orchestrator = ps.ParallelOrchestrator()

        empty = orchestrator._telemetry_summary()
        self.assertIsNone(empty["website_found_rate"])
        self.assertIsNone(empty["fallback_rate"])

        orchestrator._record_search_telemetry({
            "search_duration_ms": 2000,
            "cards_seen": 4,
            "leads_returned": 2,
            "leads_with_website": 1,
            "batch_read_complete": 1,
            "fallback_to_existing_extract_card": 3,
            "cards_completed_without_sidebar": 2,
            "sidebar_click_attempts": 2,
            "sidebar_click_successes": 1,
            "sidebar_extraction_failures": 1,
            "card_extract_duration_ms": 400,
            "sidebar_wait_duration_ms": 300,
            "identity_cards_submitted": 4,
            "cards_with_strong_place_key": 3,
            "early_dedupe_approved": 1,
            "early_duplicates_skipped": 2,
            "historical_duplicates_skipped": 1,
            "inflight_duplicates_skipped": 1,
            "coordinator_requests": 1,
            "coordinator_lookup_duration_ms": 2,
            "coordinator_max_lookup_duration_ms": 2,
            "sidebar_fallbacks_avoided_estimate": 2,
        })

        summary = orchestrator._telemetry_summary()
        self.assertEqual(summary["website_found_rate"], 50.0)
        self.assertEqual(summary["avg_time_per_website_found_lead_ms"], 2000.0)
        self.assertEqual(summary["fallback_rate"], 75.0)
        self.assertEqual(summary["sidebar_success_rate"], 50.0)
        self.assertEqual(summary["strong_place_key_coverage"], 75.0)
        self.assertEqual(summary["early_duplicate_rate"], 66.7)
        self.assertEqual(summary["extraction_avoidance_rate"], 50.0)
        self.assertEqual(summary["average_coordinator_latency_ms"], 2.0)

    def test_adaptive_ramp_does_not_require_website_found_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ps, "OUTPUT_DIR", Path(tmp)), patch.object(ps, "MASTER_OUTPUT", Path(tmp) / "leads_full.csv"), patch.object(ps, "TASKS_PROGRESS_FILE", Path(tmp) / "completed_tasks.jsonl"):
                orchestrator = ps.ParallelOrchestrator(num_workers=2)

        orchestrator.task_queue = std_queue.Queue()
        orchestrator.result_queue = std_queue.Queue()
        orchestrator.stop_event = object()
        for _ in range(10):
            orchestrator._record_search_telemetry({
                "search_duration_ms": 1000,
                "cards_seen": 1,
                "leads_returned": 1,
                "leads_with_website": 0,
            })

        with (
            patch.object(ps, "MAPS_ADAPTIVE_WORKER_SCALING_ENABLED", True),
            patch.object(ps, "MAPS_MAX_WORKERS", 3),
            patch.object(ps, "MAPS_WORKER_RAMP_STEP", 1),
            patch.object(ps, "MAPS_WORKER_RAMP_CHECK_INTERVAL_SECONDS", 30),
            patch.object(ps, "MAPS_MAX_ERROR_RATE_FOR_RAMP", 1.0),
            patch.object(ps, "_host_compute_snapshot", return_value={"cpu_percent": 10.0, "memory_pressure_pct": 20.0}),
            patch.object(orchestrator, "_start_worker_process") as start_worker,
        ):
            action = orchestrator._maybe_adjust_workers(tasks_sent=5, total_tasks=20)

        self.assertEqual(action, "up")
        self.assertEqual(orchestrator.num_workers, 3)
        start_worker.assert_called_once()

    def test_adaptive_pressure_retires_worker_after_current_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ps, "OUTPUT_DIR", Path(tmp)), patch.object(ps, "MASTER_OUTPUT", Path(tmp) / "leads_full.csv"), patch.object(ps, "TASKS_PROGRESS_FILE", Path(tmp) / "completed_tasks.jsonl"):
                orchestrator = ps.ParallelOrchestrator(num_workers=4)

        orchestrator.task_queue = std_queue.Queue()
        orchestrator._pressure_started_at = ps.time.monotonic() - 121
        with (
            patch.object(ps, "MAPS_ADAPTIVE_WORKER_SCALING_ENABLED", True),
            patch.object(ps, "MAPS_MIN_WORKERS", 2),
            patch.object(ps, "MAPS_WORKER_PRESSURE_CONFIRMATION_CHECKS", 1),
            patch.object(ps, "MAPS_WORKER_SCALE_DOWN_CHECK_INTERVAL_SECONDS", 120),
            patch.object(ps, "_host_compute_snapshot", return_value={"cpu_percent": 99.0, "memory_pressure_pct": 20.0}),
        ):
            action = orchestrator._maybe_adjust_workers(tasks_sent=5, total_tasks=20)

        self.assertEqual(action, "down")
        self.assertEqual(orchestrator.num_workers, 3)
        self.assertEqual(orchestrator._retired_worker_count, 1)
        self.assertIsNone(orchestrator.task_queue.get_nowait())

    def test_adaptive_pressure_waits_for_sustained_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ps, "OUTPUT_DIR", Path(tmp)), patch.object(ps, "MASTER_OUTPUT", Path(tmp) / "leads_full.csv"), patch.object(ps, "TASKS_PROGRESS_FILE", Path(tmp) / "completed_tasks.jsonl"):
                orchestrator = ps.ParallelOrchestrator(num_workers=4)

        orchestrator.task_queue = std_queue.Queue()
        with (
            patch.object(ps, "MAPS_ADAPTIVE_WORKER_SCALING_ENABLED", True),
            patch.object(ps, "MAPS_MIN_WORKERS", 2),
            patch.object(ps, "MAPS_WORKER_PRESSURE_CONFIRMATION_CHECKS", 1),
            patch.object(ps, "MAPS_WORKER_SCALE_DOWN_CHECK_INTERVAL_SECONDS", 120),
            patch.object(ps, "_host_compute_snapshot", return_value={"cpu_percent": 99.0, "memory_pressure_pct": 20.0}),
        ):
            action = orchestrator._maybe_adjust_workers(tasks_sent=5, total_tasks=20)

        self.assertEqual(action, "none")
        self.assertEqual(orchestrator.num_workers, 4)
        self.assertEqual(orchestrator._retired_worker_count, 0)

    def test_adaptive_pressure_stops_gracefully_at_min_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ps, "OUTPUT_DIR", Path(tmp)), patch.object(ps, "MASTER_OUTPUT", Path(tmp) / "leads_full.csv"), patch.object(ps, "TASKS_PROGRESS_FILE", Path(tmp) / "completed_tasks.jsonl"):
                orchestrator = ps.ParallelOrchestrator(num_workers=2)

        orchestrator.task_queue = std_queue.Queue()
        orchestrator._pressure_started_at = ps.time.monotonic() - 121
        with (
            patch.object(ps, "MAPS_ADAPTIVE_WORKER_SCALING_ENABLED", True),
            patch.object(ps, "MAPS_MIN_WORKERS", 2),
            patch.object(ps, "MAPS_WORKER_PRESSURE_CONFIRMATION_CHECKS", 1),
            patch.object(ps, "MAPS_WORKER_SCALE_DOWN_CHECK_INTERVAL_SECONDS", 120),
            patch.object(ps, "_host_compute_snapshot", return_value={"cpu_percent": 99.0, "memory_pressure_pct": 20.0}),
        ):
            action = orchestrator._maybe_adjust_workers(tasks_sent=5, total_tasks=20)

        self.assertEqual(action, "stop")
        self.assertTrue(orchestrator._resource_pressure_stop_requested)
        self.assertEqual(orchestrator.num_workers, 2)


if __name__ == "__main__":
    unittest.main()
