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

import parallel_scraper as ps  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
