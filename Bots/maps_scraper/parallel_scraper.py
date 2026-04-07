"""Parallel orchestrator for Google Maps lead scraping with multiple browser instances.

Uses multiprocessing to avoid Playwright sync/async conflicts.
"""

from __future__ import annotations

import multiprocessing as mp
import json
import os
import random
import sys
import time
import queue
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Set, Tuple, Optional, Dict
from multiprocessing import Process, Queue

from config import (
    ALL_KEYWORD_CATEGORIES,
    BATCH_SIZE,
    BATCH_CRITICAL_ERROR_RATE,
    BATCH_NORMAL_WEBSITE_RATE,
    BATCH_NORMAL_YIELD,
    BATCH_WARNING_ERROR_RATE,
    BATCH_WARNING_WEBSITE_RATE,
    BATCH_WARNING_YIELD,
    CATEGORY_STATE_FILE,
    INTER_REQUEST_DELAY_MIN,
    INTER_REQUEST_DELAY_MAX,
    KEYWORDS,
    MASTER_OUTPUT,
    NUM_WORKERS,
    OUTPUT_DIR,
    PENDING_BATCH_FILE,
    RUN_LOCK_FILE,
    TASKS_PROGRESS_FILE,
    TOTAL_CATEGORIES,
    WORKER_JOIN_TIMEOUT_S,
    WORKER_KILL_JOIN_TIMEOUT_S,
    WORKER_TERMINATE_JOIN_TIMEOUT_S,
    WORKER_STAGGER_DELAY_MIN,
    WORKER_STAGGER_DELAY_MAX,
    PLAYWRIGHT_USER_DATA,
    SCRAPER_CALLOUTS_FILE,
    SCRAPER_HEALTH_FILE,
    SCRAPER_STATS_FILE,
    get_locations,
    sync_location_source_now,
    WEBSITE_STATUS_WORKERS,
)
from utils import normalize_domain


CALLOUTS_RETAIN_COUNT = 200
CALLOUT_SIGNAL_NORMAL = "normal"
CALLOUT_SIGNAL_WARNING = "warning"
CALLOUT_SIGNAL_CRITICAL = "critical"
PHASE_HEALTHY = "healthy"
PHASE_WARNING = "warning"
PHASE_CONFIRMING = "confirming"
PHASE_PAUSED_CRITICAL = "paused_critical"


# ============================================================================
# CATEGORY STATE MANAGEMENT
# ============================================================================


def _atomic_write_json(path: Path, data: dict | list):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _append_jsonl(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _tail_valid_jsonl(path: Path, limit: int) -> List[dict]:
    if not path.exists():
        return []
    items: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    items.append(data)
    except OSError:
        return []
    return items[-limit:]

def _load_category_state() -> int:
    """Load the current category index from state file. Returns 0 if not found."""
    import json
    if not CATEGORY_STATE_FILE.exists():
        return 0
    try:
        with open(CATEGORY_STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('category_index', 0)
    except (json.JSONDecodeError, IOError):
        return 0


def _save_category_state(category_index: int):
    """Save the current category index to state file."""
    data = {'category_index': category_index}
    _atomic_write_json(CATEGORY_STATE_FILE, data)


def _advance_to_next_category() -> int:
    """Advance to the next category and clear completed tasks for the old category."""
    current = _load_category_state()
    new_index = current + 1
    
    if new_index >= TOTAL_CATEGORIES:
        print(f"⚠️ Already at last category ({TOTAL_CATEGORIES}). Cannot advance further.")
        return current
    
    # Clear completed tasks for the previous category
    if TASKS_PROGRESS_FILE.exists():
        print(f"🧹 Clearing completed tasks from Category {current + 1}...")
        TASKS_PROGRESS_FILE.unlink()
    
    # Save new category state
    _save_category_state(new_index)
    print(f"➡️ Advanced to Category {new_index + 1}/{TOTAL_CATEGORIES}")
    
    return new_index


def _get_current_keywords() -> list:
    """Get the keywords for the current category."""
    category_index = _load_category_state()
    if category_index < 0 or category_index >= len(ALL_KEYWORD_CATEGORIES):
        category_index = 0
    return ALL_KEYWORD_CATEGORIES[category_index]


@dataclass
class WorkerResult:
    """Result from a single worker's search operation."""
    worker_id: int
    keyword: str
    location: str
    leads: List[dict] = field(default_factory=list)  # Dicts for multiprocessing serialization
    error: Optional[str] = None
    captcha_detected: bool = False


def worker_process(
    worker_id: int,
    task_queue: Queue,
    result_queue: Queue,
    stop_event,
    headless: bool,
    delay_min: int,
    delay_max: int,
    profile_path: str
):
    """
    Worker process that runs its own browser instance.
    Each worker has its own Playwright context to avoid sync/async conflicts.
    """
    # Import inside worker to avoid multiprocessing issues
    from google_maps_scraper import CaptchaDetected, GoogleMapsScraper
    import shutil
    
    # Ensure profile directory is clean of stale locks
    profile_dir = Path(profile_path)
    if profile_dir.exists():
        lock_file = profile_dir / "SingletonLock"
        if lock_file.exists():
            try:
                lock_file.unlink()
                print(f"   [W{worker_id + 1}] 🧹 Removed stale lock file")
            except Exception as e:
                print(f"   [W{worker_id + 1}] ⚠️ Could not remove lock file: {e}")

    print(f"   [W{worker_id + 1}] Starting browser with profile: {profile_path}...")
    scraper = GoogleMapsScraper(headless=headless, user_data_dir=profile_path)
    scraper.start()
    print(f"   [W{worker_id + 1}] ✅ Browser ready")

    def cooperative_sleep(delay_seconds: float):
        remaining = max(0.0, delay_seconds)
        step = 0.25
        while remaining > 0 and not stop_event.is_set():
            sleep_for = min(step, remaining)
            time.sleep(sleep_for)
            remaining -= sleep_for
    
    try:
        while not stop_event.is_set():
            try:
                # Get task from queue (non-blocking with timeout)
                task = task_queue.get(timeout=1)
                
                if task is None:  # Poison pill to stop worker
                    break
                
                keyword, location = task
                result = WorkerResult(worker_id=worker_id, keyword=keyword, location=location)
                
                try:
                    print(f"   [W{worker_id + 1}] Searching '{keyword}' in '{location}'...")
                    leads = scraper.search(keyword, location)
                    
                    # Convert Lead objects to dicts for serialization
                    result.leads = [
                        {
                            'name': l.name,
                            'website': l.website,
                            'location': l.location,
                            'keyword': l.keyword,
                            'place_url': l.place_url,
                        }
                        for l in leads
                    ]
                    
                    # Randomized delay between requests
                    delay = random.uniform(delay_min, delay_max)
                    print(f"   [W{worker_id + 1}] Found {len(leads)} leads. Waiting {delay:.1f}s...")
                    cooperative_sleep(delay)
                    
                except CaptchaDetected:
                    print(f"   [W{worker_id + 1}] ⚠️ CAPTCHA detected!")
                    result.captcha_detected = True
                    result.error = "CAPTCHA detected"
                    
                except Exception as e:
                    print(f"   [W{worker_id + 1}] ❌ Error: {e}")
                    result.error = str(e)
                
                result_queue.put(result)
                
            except queue.Empty:
                # Check if we should continue waiting
                continue
                
    finally:
        print(f"   [W{worker_id + 1}] Stopping browser...")
        scraper.stop()


class ParallelOrchestrator:
    """Coordinates multiple browser processes for parallel scraping."""
    
    def __init__(self, headless: bool = False, num_workers: int = NUM_WORKERS, continuous: bool = False):
        self.headless = headless
        self.num_workers = num_workers
        self.continuous = continuous  # Skip batch prompts for non-interactive runs
        self.workers: List[Process] = []
        self.task_queue: Optional[Queue] = None
        self.result_queue: Optional[Queue] = None
        self.stop_event = None
        self._workers_stopped = False
        self._run_lock_acquired = False
        
        # Deduplication (main process only)
        self._seen_domains: Set[str] = set()
        self._seen_names: Set[str] = set()
        
        # Task Tracking
        self._completed_tasks_set: Set[str] = set()
        self._load_completed_tasks()
        
        # Results aggregation
        self.batch_number = self._load_last_batch_number()
        self.pending_batch_path = PENDING_BATCH_FILE
        self.run_lock_path = RUN_LOCK_FILE
        self.health_path = SCRAPER_HEALTH_FILE
        self.callouts_path = SCRAPER_CALLOUTS_FILE
        self.stats_path = SCRAPER_STATS_FILE
        self._health_started_at: Optional[str] = None
        self._health_ended_at: Optional[str] = None
        self._health_last_activity: Optional[str] = None
        self._health_phase = PHASE_HEALTHY
        self._captcha_count = 0
        self._error_count = 0
        self._run_searches = 0
        self._run_leads = 0
        self._run_batches = 0
        self._run_website_found = 0
        self._run_website_total = 0
        self._current_batch_number = 0
        self._pending_critical_signals: Dict[str, Dict[str, Any]] = {}
        self._last_callout_severity: Optional[str] = None
        self._all_time_stats = self._load_all_time_stats()
        
        # Load existing leads to prevent duplicates across runs
        self._load_existing_leads()

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_batch_observation() -> Dict[str, int]:
        return {
            "searches": 0,
            "captchas": 0,
            "errors": 0,
            "unique_leads": 0,
        }

    def _default_all_time_stats(self) -> Dict[str, Optional[int]]:
        return {
            "total_searches": None,
            "total_leads": None,
            "total_batches": None,
            "total_website_found": None,
            "total_website_total": None,
            "total_captchas": None,
            "total_errors": None,
        }

    def _derive_existing_all_time_stats(self) -> Dict[str, Optional[int]]:
        stats = self._default_all_time_stats()

        if TASKS_PROGRESS_FILE.exists():
            try:
                with open(TASKS_PROGRESS_FILE, "r", encoding="utf-8") as f:
                    stats["total_searches"] = sum(1 for line in f if line.strip())
            except OSError:
                stats["total_searches"] = None
        else:
            stats["total_searches"] = 0

        if MASTER_OUTPUT.exists():
            try:
                import csv

                total_rows = 0
                website_found = 0
                with open(MASTER_OUTPUT, "r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        total_rows += 1
                        if (row.get("website") or "").strip():
                            website_found += 1
                stats["total_leads"] = total_rows
                stats["total_website_total"] = total_rows
                stats["total_website_found"] = website_found
            except OSError:
                stats["total_leads"] = None
                stats["total_website_total"] = None
                stats["total_website_found"] = None
        else:
            stats["total_leads"] = 0
            stats["total_website_total"] = 0
            stats["total_website_found"] = 0

        if OUTPUT_DIR.exists():
            try:
                stats["total_batches"] = len(list(OUTPUT_DIR.glob("leads_batch_*.csv")))
            except OSError:
                stats["total_batches"] = None
        else:
            stats["total_batches"] = 0

        return stats

    def _load_all_time_stats(self) -> Dict[str, Optional[int]]:
        stats = self._derive_existing_all_time_stats()
        if self.stats_path.exists():
            try:
                payload = json.loads(self.stats_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict):
                for key in stats:
                    if key in payload:
                        stats[key] = payload.get(key)
        return stats

    def _write_all_time_stats(self):
        try:
            _atomic_write_json(self.stats_path, self._all_time_stats)
        except Exception as e:
            print(f"⚠️ Could not write scraper stats: {e}")

    def _increment_all_time_stat(self, key: str, amount: int):
        current = self._all_time_stats.get(key)
        if current is None:
            current = 0
        self._all_time_stats[key] = current + amount

    def _trim_callouts_file(self):
        items = _tail_valid_jsonl(self.callouts_path, CALLOUTS_RETAIN_COUNT)
        if not items:
            if self.callouts_path.exists():
                try:
                    self.callouts_path.unlink()
                except OSError:
                    pass
            return
        try:
            self.callouts_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.callouts_path, "w", encoding="utf-8") as f:
                for item in items:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"⚠️ Could not trim scraper callouts: {e}")

    def _append_callout(
        self,
        severity: str,
        signal_type: str,
        batch_number: int,
        message: str,
        details: Dict[str, Any],
        action: str,
        confirmed: bool = False,
        window: str = "batch",
    ):
        payload = {
            "timestamp": self._utc_now_iso(),
            "severity": severity,
            "signal_type": signal_type,
            "window": window,
            "batch_number": batch_number,
            "message": message,
            "details": details,
            "action": action,
            "confirmed": confirmed,
        }
        try:
            _append_jsonl(self.callouts_path, payload)
            self._last_callout_severity = severity
        except OSError as e:
            print(f"⚠️ Could not write scraper callout: {e}")

    def _set_phase(self, phase: str):
        self._health_phase = phase
        self._mark_activity()

    def _write_health(self):
        payload = {
            "pid": os.getpid(),
            "started_at": self._health_started_at,
            "ended_at": self._health_ended_at,
            "last_activity": self._health_last_activity,
            "current_phase": self._health_phase,
            "captcha_count": self._captcha_count,
            "error_count": self._error_count,
            "run_searches": self._run_searches,
            "run_leads": self._run_leads,
            "run_batches": self._run_batches,
            "run_website_found": self._run_website_found,
            "run_website_total": self._run_website_total,
            "current_batch_number": self._current_batch_number,
        }
        try:
            _atomic_write_json(self.health_path, payload)
        except Exception as e:
            print(f"⚠️ Could not write scraper health: {e}")

    def _mark_activity(self):
        self._health_last_activity = self._utc_now_iso()
        self._write_health()
        self._write_all_time_stats()

    def _start_health_tracking(self):
        self._trim_callouts_file()
        self._health_started_at = self._utc_now_iso()
        self._health_ended_at = None
        self._health_last_activity = self._health_started_at
        self._health_phase = PHASE_HEALTHY
        self._captcha_count = 0
        self._error_count = 0
        self._run_searches = 0
        self._run_leads = 0
        self._run_batches = 0
        self._run_website_found = 0
        self._run_website_total = 0
        self._current_batch_number = self.batch_number
        self._pending_critical_signals = {}
        self._last_callout_severity = None
        self._write_health()
        self._write_all_time_stats()

    def _finish_health_tracking(self):
        if not self._health_started_at:
            return
        self._health_ended_at = self._utc_now_iso()
        self._health_last_activity = self._health_ended_at
        self._write_health()

    def _load_last_batch_number(self) -> int:
        """Resume batch numbering from existing output files."""
        highest = 0
        for path in OUTPUT_DIR.glob("leads_batch_*.csv"):
            try:
                highest = max(highest, int(path.stem.split("_")[-1]))
            except (ValueError, IndexError):
                continue
        if highest:
            print(f"📦 Resuming batch numbering from {highest}.")
        return highest

    def _acquire_run_lock(self):
        """Ensure only one parallel scraper run mutates shared state at a time."""
        if self._run_lock_acquired:
            return
        lock_payload = {"pid": os.getpid(), "started_at": time.time()}
        self.run_lock_path.parent.mkdir(parents=True, exist_ok=True)

        if self.run_lock_path.exists():
            try:
                existing = json.loads(self.run_lock_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
            pid = int(existing.get("pid") or 0)
            if pid and _pid_is_alive(pid):
                raise RuntimeError(
                    f"Another parallel scraper run is already active (pid={pid}). "
                    f"Lock file: {self.run_lock_path}"
                )
            print("⚠️ Found stale run lock; replacing it.")
            try:
                self.run_lock_path.unlink()
            except FileNotFoundError:
                pass

        try:
            with open(self.run_lock_path, "x", encoding="utf-8") as f:
                json.dump(lock_payload, f)
            self._run_lock_acquired = True
        except FileExistsError:
            raise RuntimeError(f"Could not acquire run lock: {self.run_lock_path}")

    def _release_run_lock(self):
        if not self._run_lock_acquired:
            return
        try:
            if self.run_lock_path.exists():
                self.run_lock_path.unlink()
        finally:
            self._run_lock_acquired = False
        
    def _load_pending_batch(self) -> List[dict]:
        """Load buffered leads left over from a previous clean stop."""
        if not self.pending_batch_path.exists():
            return []
        try:
            with open(self.pending_batch_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                print(f"📦 Restored {len(data)} pending buffered leads from previous stop.")
                return [item for item in data if isinstance(item, dict)]
        except Exception as e:
            print(f"⚠️ Could not load pending batch: {e}")
        return []

    def _save_pending_batch(self, batch: List[dict]):
        """Persist buffered leads so a manual stop doesn't lose them."""
        try:
            if batch:
                _atomic_write_json(self.pending_batch_path, batch)
            elif self.pending_batch_path.exists():
                self.pending_batch_path.unlink()
        except Exception as e:
            print(f"⚠️ Could not save pending batch: {e}")

    def _register_buffered_lead(self, lead_dict: dict):
        """Ensure dedupe state includes a lead already buffered in memory/on disk."""
        website = lead_dict.get('website')
        if website:
            domain = normalize_domain(website)
            if domain:
                self._seen_domains.add(domain)
        name_key = self._build_name_key(lead_dict.get('name', ''), lead_dict.get('location', ''))
        if name_key:
            self._seen_names.add(name_key)

    def _register_pending_batch(self, batch: List[dict]):
        for lead_dict in batch:
            self._register_buffered_lead(lead_dict)

    def _load_existing_leads(self):
        """Load existing leads from master CSV to initialize deduplication."""
        master_path = MASTER_OUTPUT
        if not master_path.exists():
            print("📋 No existing master file. Starting fresh deduplication.")
            return
        
        import csv
        try:
            with open(master_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Add domain to seen set
                    website = row.get('website', '')
                    if website:
                        domain = normalize_domain(website)
                        if domain:
                            self._seen_domains.add(domain)
                    
                    # Add name to seen set
                    name = row.get('name', '')
                    if name:
                        name_key = self._build_name_key(name, row.get('location', ''))
                        self._seen_names.add(name_key)
            
            print(f"📋 Loaded {len(self._seen_domains)} existing domains and {len(self._seen_names)} names for deduplication.")
        except Exception as e:
            print(f"⚠️ Could not load existing leads: {e}")

    @staticmethod
    def _build_name_key(name: str, location: str) -> str:
        clean_name = (name or '').lower().strip()
        clean_location = (location or '').lower().strip()
        return f"{clean_name}|{clean_location}"
    
    def _cleanup_stale_browsers(self):
        """Clean stale profile locks and only kill orphaned profile-specific browsers."""
        import subprocess
        from pathlib import Path
        
        try:
            result = subprocess.run(
                ["ps", "ax", "-o", "pid=", "-o", "command="],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line or ".playwright-profile-worker-" not in line:
                    continue
                pid_text, _, command = line.partition(" ")
                try:
                    pid = int(pid_text)
                except ValueError:
                    continue
                if pid == os.getpid():
                    continue
                try:
                    os.kill(pid, 9)
                    print(f"   🔪 Killed stale browser process {pid}: {command[:80]}")
                except Exception:
                    pass
        except Exception:
            pass
        
        # Remove stale SingletonLock files from profile directories
        profile_dir = PLAYWRIGHT_USER_DATA.parent
        for lock_file in profile_dir.glob(".playwright-profile-worker-*/SingletonLock"):
            try:
                lock_file.unlink()
                print(f"   🔓 Removed stale lock: {lock_file.name}")
            except Exception:
                pass
        
        # Small delay to ensure processes are terminated
        time.sleep(1)
    
    def start_workers(self):
        """Initialize worker processes with staggered starts."""
        self._workers_stopped = False
        print(f"\n🚀 Starting {self.num_workers} parallel worker processes...")
        
        # Clean up any orphaned browsers from previous runs
        self._cleanup_stale_browsers()
        
        self.task_queue = mp.Queue()
        self.result_queue = mp.Queue()
        self.stop_event = mp.Event()
        
        for i in range(self.num_workers):
            if i > 0:
                # Randomized stagger delay for more human-like behavior
                stagger = random.uniform(WORKER_STAGGER_DELAY_MIN, WORKER_STAGGER_DELAY_MAX)
                print(f"   ⏳ Waiting {stagger:.1f}s before starting worker {i + 1}...")
                time.sleep(stagger)
            
            # Each worker gets its own profile directory to avoid race conditions
            worker_profile = str(PLAYWRIGHT_USER_DATA.parent / f".playwright-profile-worker-{i + 1}")
            
            p = Process(
                target=worker_process,
                args=(i, self.task_queue, self.result_queue, self.stop_event, self.headless, 
                      INTER_REQUEST_DELAY_MIN, INTER_REQUEST_DELAY_MAX, worker_profile)
            )
            p.start()
            self.workers.append(p)
        
        # Wait a bit for workers to initialize
        time.sleep(3)
        print(f"\n✅ All {self.num_workers} workers started.\n")
    
    def stop_workers(self):
        """Send poison pills and wait for workers to stop."""
        if self._workers_stopped:
            return
        self._workers_stopped = True

        print("\n🛑 Shutting down workers...")
        if self.stop_event:
            self.stop_event.set()
        
        # Send poison pills
        for _ in self.workers:
            if self.task_queue:
                self.task_queue.put(None)
        
        # Wait for workers to finish
        for i, p in enumerate(self.workers):
            p.join(timeout=WORKER_JOIN_TIMEOUT_S)
            if p.is_alive():
                print(f"   ⚠️ Worker {i + 1} didn't stop gracefully, terminating...")
                p.terminate()
                p.join(timeout=WORKER_TERMINATE_JOIN_TIMEOUT_S)
                if p.is_alive() and hasattr(p, "kill"):
                    print(f"   ⚠️ Worker {i + 1} still alive, killing...")
                    p.kill()
                    p.join(timeout=WORKER_KILL_JOIN_TIMEOUT_S)
                if p.is_alive():
                    print(f"   ❌ Worker {i + 1} could not be stopped cleanly")
                else:
                    print(f"   ✅ Worker {i + 1} terminated")
            else:
                print(f"   ✅ Worker {i + 1} stopped")
        
        self.workers.clear()

    def _seed_tasks(self, tasks: List[Tuple[str, str]], tasks_sent: int, tasks_completed: int) -> int:
        """Fill worker slots with the next available tasks."""
        if not self.task_queue:
            return tasks_sent
        in_flight = max(0, tasks_sent - tasks_completed)
        active_slots = max(0, self.num_workers - in_flight)
        to_send = min(active_slots, len(tasks) - tasks_sent)
        for _ in range(to_send):
            self.task_queue.put(tasks[tasks_sent])
            tasks_sent += 1
        return tasks_sent

    def _drain_completed_results(
        self,
        current_batch: List[dict],
        tasks_completed: int,
        batch_obs: Dict[str, int],
    ) -> Tuple[List[dict], int]:
        """Drain already-finished worker results after pausing workers."""
        while self.result_queue:
            try:
                result = self.result_queue.get_nowait()
            except queue.Empty:
                break

            tasks_completed += 1
            if result.captcha_detected:
                self._record_result_observation(batch_obs, captcha=True)
                continue
            if result.error and not result.captcha_detected:
                self._record_result_observation(batch_obs, errored=True)
                continue

            self._record_result_observation(batch_obs)
            unique_leads = []
            for lead_dict in result.leads:
                if self._dedupe_lead(lead_dict):
                    unique_leads.append(lead_dict)

            if unique_leads:
                current_batch.extend(unique_leads)
                self._record_unique_leads(batch_obs, unique_leads)

            self._save_pending_batch(current_batch)
            self._save_completed_task(result.keyword, result.location)
            self._mark_activity()

        return current_batch, tasks_completed
    
    def _dedupe_lead(self, lead_dict: dict) -> bool:
        """Check if lead is unique. Returns True if unique, False if duplicate."""
        # Check domain using the same robust normalization as single-threaded mode
        website = lead_dict.get('website')
        if website:
            domain = normalize_domain(website)
            if domain:
                if domain in self._seen_domains:
                    return False
                self._seen_domains.add(domain)
        
        # Check name
        name = lead_dict.get('name', '')
        name_key = self._build_name_key(name, lead_dict.get('location', ''))
        if name_key in self._seen_names:
            return False
        self._seen_names.add(name_key)
        
        return True
    
    def _load_completed_tasks(self):
        """Load previously completed search tasks."""
        if not TASKS_PROGRESS_FILE.exists():
            return
            
        import json
        try:
            with open(TASKS_PROGRESS_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        data = json.loads(line)
                        key = f"{data['keyword']}|{data['location']}"
                        self._completed_tasks_set.add(key)
                    except json.JSONDecodeError:
                        continue
            print(f"📋 Loaded {len(self._completed_tasks_set)} completed search tasks.")
        except Exception as e:
            print(f"⚠️ Could not load completed tasks: {e}")

    def _save_completed_task(self, keyword: str, location: str):
        """Save a task as completed."""
        from datetime import datetime
        
        TASKS_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        key = f"{keyword}|{location}"
        self._completed_tasks_set.add(key)
        
        data = {
            "keyword": keyword,
            "location": location,
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            with open(TASKS_PROGRESS_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(data) + "\n")
        except Exception as e:
            print(f"⚠️ Could not save completed task: {e}")

    def _record_result_observation(self, batch_obs: Dict[str, int], *, captcha: bool = False, errored: bool = False):
        self._run_searches += 1
        self._increment_all_time_stat("total_searches", 1)
        batch_obs["searches"] += 1
        if captcha:
            self._captcha_count += 1
            self._increment_all_time_stat("total_captchas", 1)
            batch_obs["captchas"] += 1
        if errored:
            self._error_count += 1
            self._increment_all_time_stat("total_errors", 1)
            batch_obs["errors"] += 1
        self._mark_activity()

    def _record_unique_leads(self, batch_obs: Dict[str, int], unique_leads: List[dict]):
        if not unique_leads:
            return
        lead_count = len(unique_leads)
        self._run_leads += lead_count
        batch_obs["unique_leads"] += lead_count

    def _classify_batch_signals(self, batch_obs: Dict[str, int], batch_metrics: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        searches = batch_obs.get("searches", 0)
        captchas = batch_obs.get("captchas", 0)
        errors = batch_obs.get("errors", 0)
        leads = batch_metrics.get("leads", 0) or 0
        website_total = batch_metrics.get("website_total", 0) or 0
        website_found = batch_metrics.get("website_found", 0) or 0

        signals: Dict[str, Dict[str, Any]] = {}
        error_rate = (errors / searches * 100.0) if searches > 0 else None
        website_rate = (website_found / website_total * 100.0) if website_total > 0 else None
        yield_rate = (leads / searches * 100.0) if searches > 0 else None

        if captchas >= 2:
            signals["captcha"] = {"severity": CALLOUT_SIGNAL_CRITICAL, "details": {"captchas": captchas, "searches": searches}}
        elif captchas == 1:
            signals["captcha"] = {"severity": CALLOUT_SIGNAL_WARNING, "details": {"captchas": captchas, "searches": searches}}

        if error_rate is not None:
            if error_rate > BATCH_CRITICAL_ERROR_RATE:
                signals["error_rate"] = {"severity": CALLOUT_SIGNAL_CRITICAL, "details": {"errors": errors, "searches": searches, "error_rate": round(error_rate, 1)}}
            elif error_rate >= BATCH_WARNING_ERROR_RATE:
                signals["error_rate"] = {"severity": CALLOUT_SIGNAL_WARNING, "details": {"errors": errors, "searches": searches, "error_rate": round(error_rate, 1)}}

        if website_rate is not None:
            if website_rate < BATCH_WARNING_WEBSITE_RATE:
                signals["website_rate"] = {"severity": CALLOUT_SIGNAL_CRITICAL, "details": {"website_found": website_found, "website_total": website_total, "website_rate": round(website_rate, 1)}}
            elif website_rate <= BATCH_NORMAL_WEBSITE_RATE:
                signals["website_rate"] = {"severity": CALLOUT_SIGNAL_WARNING, "details": {"website_found": website_found, "website_total": website_total, "website_rate": round(website_rate, 1)}}

        if yield_rate is not None:
            if yield_rate < BATCH_WARNING_YIELD:
                signals["yield"] = {"severity": CALLOUT_SIGNAL_CRITICAL, "details": {"leads": leads, "searches": searches, "yield_per_100": round(yield_rate, 1)}}
            elif yield_rate <= BATCH_NORMAL_YIELD:
                signals["yield"] = {"severity": CALLOUT_SIGNAL_WARNING, "details": {"leads": leads, "searches": searches, "yield_per_100": round(yield_rate, 1)}}

        return signals

    def _signal_message(self, signal_type: str, details: Dict[str, Any], batch_number: int, *, confirmed: bool = False, batch_specific: bool = False) -> str:
        prefix = f"Batch {batch_number}"
        if signal_type == "captcha":
            base = f"{prefix} saw {details.get('captchas', 0)} captcha event(s)"
        elif signal_type == "error_rate":
            base = f"{prefix} saw {details.get('error_rate', 0):.1f}% worker error rate"
        elif signal_type == "website_rate":
            base = f"{prefix} landed at {details.get('website_rate', 0):.1f}% website-found rate"
        elif signal_type == "yield":
            base = f"{prefix} yielded {details.get('yield_per_100', 0):.1f} leads per 100 searches"
        else:
            base = f"{prefix} triggered a scraper system signal"

        if confirmed:
            return f"{base}. Confirmed across consecutive batches; scraper auto-paused for review."
        if batch_specific:
            return f"{base}. The next batch recovered, so the signal looks batch-specific."
        return f"{base}. Watching the next batch before escalating."

    def _maybe_emit_normal_callout(self, batch_number: int, batch_obs: Dict[str, int], batch_metrics: Dict[str, Any]):
        if self._last_callout_severity == CALLOUT_SIGNAL_NORMAL and not self._pending_critical_signals:
            return
        searches = batch_obs.get("searches", 0)
        website_total = batch_metrics.get("website_total", 0) or 0
        website_found = batch_metrics.get("website_found", 0) or 0
        leads = batch_metrics.get("leads", 0) or 0
        yield_rate = round((leads / searches) * 100.0, 1) if searches > 0 else None
        website_rate = round((website_found / website_total) * 100.0, 1) if website_total > 0 else None
        message = f"Batch {batch_number} completed cleanly and the current run looks healthy."
        self._append_callout(
            CALLOUT_SIGNAL_NORMAL,
            "system",
            batch_number,
            message,
            {
                "searches": searches,
                "leads": leads,
                "yield_per_100": yield_rate,
                "website_rate": website_rate,
            },
            action="continue",
            confirmed=False,
        )

    def _evaluate_batch_signals(self, batch_obs: Dict[str, int], batch_metrics: Dict[str, Any]) -> bool:
        batch_number = batch_metrics.get("batch_number", self.batch_number)
        signals = self._classify_batch_signals(batch_obs, batch_metrics)
        criticals = {k: v for k, v in signals.items() if v["severity"] == CALLOUT_SIGNAL_CRITICAL}
        warnings = {k: v for k, v in signals.items() if v["severity"] == CALLOUT_SIGNAL_WARNING}
        auto_pause = False

        for signal_type, pending in list(self._pending_critical_signals.items()):
            if signal_type in criticals:
                details = dict(criticals[signal_type]["details"])
                details["previous_batch_number"] = pending.get("batch_number")
                self._append_callout(
                    CALLOUT_SIGNAL_CRITICAL,
                    signal_type,
                    batch_number,
                    self._signal_message(signal_type, criticals[signal_type]["details"], batch_number, confirmed=True),
                    details,
                    action="auto_paused",
                    confirmed=True,
                )
                auto_pause = True
            else:
                self._append_callout(
                    CALLOUT_SIGNAL_WARNING,
                    signal_type,
                    batch_number,
                    self._signal_message(signal_type, pending.get("details", {}), pending.get("batch_number", batch_number), batch_specific=True),
                    {
                        **pending.get("details", {}),
                        "resolved_by_batch": batch_number,
                    },
                    action="continue",
                    confirmed=False,
                )
            self._pending_critical_signals.pop(signal_type, None)

        for signal_type, payload in criticals.items():
            if auto_pause:
                continue
            self._pending_critical_signals[signal_type] = {
                "batch_number": batch_number,
                "details": payload["details"],
            }
            self._append_callout(
                CALLOUT_SIGNAL_CRITICAL,
                signal_type,
                batch_number,
                self._signal_message(signal_type, payload["details"], batch_number),
                payload["details"],
                action="confirming",
                confirmed=False,
            )

        for signal_type, payload in warnings.items():
            self._append_callout(
                CALLOUT_SIGNAL_WARNING,
                signal_type,
                batch_number,
                self._signal_message(signal_type, payload["details"], batch_number).replace("Watching the next batch before escalating.", "Continuing with a warning."),
                payload["details"],
                action="continue",
                confirmed=False,
            )

        if auto_pause:
            self._health_phase = PHASE_PAUSED_CRITICAL
        elif self._pending_critical_signals:
            self._health_phase = PHASE_CONFIRMING
        elif warnings:
            self._health_phase = PHASE_WARNING
        else:
            self._health_phase = PHASE_HEALTHY
            self._maybe_emit_normal_callout(batch_number, batch_obs, batch_metrics)

        self._mark_activity()
        return auto_pause

    def _generate_tasks(self) -> List[Tuple[str, str]]:
        """Generate keyword × location combinations, skipping completed ones."""
        # Use current category keywords instead of hardcoded KEYWORDS
        current_keywords = _get_current_keywords()
        category_index = _load_category_state()
        print(f"📂 Using Category {category_index + 1} keywords: {current_keywords}")
        
        current_locations = get_locations()
        all_tasks = []
        for location in current_locations:
            for keyword in current_keywords:
                all_tasks.append((keyword, location))
        
        # Filter out completed tasks
        needed_tasks = []
        for t in all_tasks:
            key = f"{t[0]}|{t[1]}"
            if key not in self._completed_tasks_set:
                needed_tasks.append(t)
        
        if len(needed_tasks) < len(all_tasks):
            print(f"⏩ Skipping {len(all_tasks) - len(needed_tasks)} already completed tasks.")
            
        return needed_tasks
    
    def _process_batch(self, batch: List[dict], master_path: Path) -> Tuple[bool, Optional[Dict[str, Any]], bool]:
        """Validate, upload, and persist a completed batch."""
        from utils import Lead, append_to_master, export_batch
        from webhook import send_batch_to_webhook
        from common.validator import get_website_statuses
        
        self.batch_number += 1

        statuses = get_website_statuses(
            [ld.get('website') for ld in batch],
            max_workers=WEBSITE_STATUS_WORKERS,
        )

        # Convert dicts to Lead objects with validation
        validated_leads = []
        for ld, status in zip(batch, statuses):
            lead = Lead(
                name=ld['name'],
                website=ld.get('website'),
                location=ld['location'],
                keyword=ld['keyword'],
                place_url=ld.get('place_url'),
                status=status
            )
            validated_leads.append(lead)

        delivery_result = send_batch_to_webhook(self.batch_number, validated_leads)
        if not delivery_result.get("ok"):
            if delivery_result.get("blocked") and delivery_result.get("reason") == "sheet_capacity_reached":
                sheet_status = delivery_result.get("sheet_status") or {}
                label = sheet_status.get("sheet_label") or "Delivery sheet"
                tab_name = sheet_status.get("tab_name") or "Leads"
                projected = sheet_status.get("projected_rows")
                stop_threshold = sheet_status.get("stop_row_threshold")
                self._append_callout(
                    CALLOUT_SIGNAL_CRITICAL,
                    "pipeline_sheet",
                    self.batch_number,
                    f"{label} / {tab_name} reached the configured stop threshold ({projected}/{stop_threshold}). Scraper paused until a new pipeline sheet is configured.",
                    dict(sheet_status),
                    action="auto_paused",
                    confirmed=True,
                    window="run",
                )
                self._health_phase = PHASE_PAUSED_CRITICAL
                self._mark_activity()
                self.batch_number -= 1
                return False, None, True

            self.batch_number -= 1
            return False, None, False

        # Export only after remote delivery succeeds so local state mirrors pipeline state.
        destination = OUTPUT_DIR / f"leads_batch_{self.batch_number:03d}.csv"
        export_batch(validated_leads, destination)
        append_to_master(master_path, validated_leads)
        website_found = sum(1 for lead in validated_leads if lead.website)
        website_total = len(validated_leads)
        self._run_batches += 1
        self._run_website_found += website_found
        self._run_website_total += website_total
        self._current_batch_number = self.batch_number
        self._increment_all_time_stat("total_leads", len(validated_leads))
        self._increment_all_time_stat("total_batches", 1)
        self._increment_all_time_stat("total_website_found", website_found)
        self._increment_all_time_stat("total_website_total", website_total)
        self._mark_activity()

        sheet_status = delivery_result.get("sheet_status") or {}
        if sheet_status.get("warning"):
            label = sheet_status.get("sheet_label") or "Delivery sheet"
            tab_name = sheet_status.get("tab_name") or "Leads"
            current_rows = sheet_status.get("current_rows")
            warning_threshold = sheet_status.get("warning_row_threshold")
            rows_remaining = sheet_status.get("rows_remaining_to_stop")
            self._append_callout(
                CALLOUT_SIGNAL_WARNING,
                "pipeline_sheet",
                self.batch_number,
                f"{label} / {tab_name} is approaching its row cap ({current_rows}/{warning_threshold}+). {rows_remaining} rows remain before the stop threshold.",
                dict(sheet_status),
                action="continue",
                confirmed=False,
                window="run",
            )

        stage2_status = delivery_result.get("stage2") or {}
        if stage2_status and not stage2_status.get("ok"):
            self._append_callout(
                CALLOUT_SIGNAL_WARNING,
                "pipeline_stage2",
                self.batch_number,
                f"Stage 2 sorter did not confirm successfully after batch delivery. {stage2_status.get('message') or 'Manual review may be needed.'}",
                dict(stage2_status),
                action="continue",
                confirmed=False,
                window="run",
            )
        
        # Summary
        print(f"\n--- Batch {self.batch_number} ({len(validated_leads)} leads) ---")
        for lead in validated_leads[:10]:  # Show first 10
            website = lead.website or "<no website>"
            print(f" • {lead.name}  |  {website}")
        if len(validated_leads) > 10:
            print(f" ... and {len(validated_leads) - 10} more")
        
        print(f"Saved batch {self.batch_number} → {destination}")
        print(f"Updated consolidated dataset → {master_path}")
        return True, {
            "batch_number": self.batch_number,
            "leads": len(validated_leads),
            "website_found": website_found,
            "website_total": website_total,
        }, False
    
    def run(self):
        """Main execution loop with parallel task distribution."""
        self._acquire_run_lock()
        if not self.workers:
            self.start_workers()
        tasks = self._generate_tasks()
        total_tasks = len(tasks)
        
        if total_tasks == 0:
            print("No tasks to execute. Check active locations and keywords in config.")
            return

        self._start_health_tracking()
        
        print(f"📋 {total_tasks} search tasks to execute across {self.num_workers} workers.\n")
        
        master_path = MASTER_OUTPUT
        # DO NOT DELETE master file - it's needed for persistent deduplication
        
        target_batch_size = BATCH_SIZE
        current_batch: List[dict] = self._load_pending_batch()
        self._register_pending_batch(current_batch)
        tasks_sent = 0
        tasks_completed = 0
        stop_requested = False
        workers_paused = False
        batch_obs = self._new_batch_observation()
        
        try:
            while len(current_batch) >= target_batch_size:
                batch_to_process = current_batch[:target_batch_size]
                current_batch = current_batch[target_batch_size:]
                success, batch_metrics, force_stop = self._process_batch(batch_to_process, master_path)
                if success:
                    if batch_metrics and batch_obs.get("searches", 0) > 0:
                        stop_requested = self._evaluate_batch_signals(batch_obs, batch_metrics) or stop_requested
                    batch_obs = self._new_batch_observation()
                    self._save_pending_batch(current_batch)
                else:
                    current_batch = batch_to_process + current_batch
                    self._save_pending_batch(current_batch)
                    if force_stop:
                        print("🛑 Delivery sheet cap reached. Stopping cleanly until a new sheet is configured.")
                    else:
                        print("❌ Could not deliver restored pending batch. Stopping to avoid data loss.")
                    return
                if stop_requested:
                    break

            # Send initial tasks
            tasks_sent = self._seed_tasks(tasks, tasks_sent, tasks_completed)
            
            print(f"=== Started processing {tasks_sent} tasks ===\n")
            
            # Process results and send more tasks
            while tasks_completed < total_tasks and not stop_requested:
                if len(current_batch) >= target_batch_size:
                    batch_to_process = current_batch[:target_batch_size]
                    remaining_batch = current_batch[target_batch_size:]
                    success, batch_metrics, force_stop = self._process_batch(batch_to_process, master_path)
                    if success:
                        if batch_metrics:
                            stop_requested = self._evaluate_batch_signals(batch_obs, batch_metrics) or stop_requested
                        batch_obs = self._new_batch_observation()
                        current_batch = remaining_batch
                        self._save_pending_batch(current_batch)
                    else:
                        current_batch = batch_to_process + remaining_batch
                        self._save_pending_batch(current_batch)
                        if force_stop:
                            print("🛑 Delivery sheet cap reached. Pausing the scraper until a new delivery sheet is configured.")
                        else:
                            print("❌ Delivery failed. Stopping to preserve buffered leads.")
                        stop_requested = True
                        break

                    if stop_requested:
                        print(f"\n🛑 Batch {self.batch_number} confirmed a critical scraper signal. Auto-pausing for review.")
                        break

                    if self.continuous:
                        print(f"\n[Continuous] Batch {self.batch_number} done. Auto-continuing...")
                    else:
                        # Quiesce workers before prompting so no extra scrape work happens in the background.
                        self.stop_workers()
                        current_batch, tasks_completed = self._drain_completed_results(
                            current_batch,
                            tasks_completed,
                            batch_obs,
                        )
                        self._save_pending_batch(current_batch)

                        print(f"\nBatch {self.batch_number} ready. Press Enter for next {target_batch_size} leads or 'q' to stop:", end=" ")
                        try:
                            response = input().strip().lower()
                            if response in {"q", "quit", "exit"}:
                                print("Stopping as requested.")
                                stop_requested = True
                                break
                        except EOFError:
                            print("\n[Non-interactive] Auto-continuing...")

                        workers_paused = True
                        continue

                if workers_paused and not stop_requested and tasks_sent < total_tasks:
                    self.start_workers()
                    tasks_sent = self._seed_tasks(tasks, tasks_sent, tasks_completed)
                    workers_paused = False

                try:
                    result = self.result_queue.get(timeout=180)  # 3 min timeout for slow networks
                    tasks_completed += 1
                    
                    if result.captcha_detected:
                        self._record_result_observation(batch_obs, captcha=True)
                        print(f"   [W{result.worker_id + 1}] CAPTCHA detected; recording for batch evaluation.")
                        continue
                    
                    if result.error and not result.captcha_detected:
                        self._record_result_observation(batch_obs, errored=True)
                        print(f"   [W{result.worker_id + 1}] Skipping due to error")
                    else:
                        self._record_result_observation(batch_obs)
                        # Process leads with deduplication
                        unique_leads = []
                        for lead_dict in result.leads:
                            if self._dedupe_lead(lead_dict):
                                unique_leads.append(lead_dict)

                        if unique_leads:
                            current_batch.extend(unique_leads)
                            self._record_unique_leads(batch_obs, unique_leads)

                        # Persist buffered leads before marking the task completed.
                        self._save_pending_batch(current_batch)
                        self._save_completed_task(result.keyword, result.location)
                        self._mark_activity()

                    # Send next task if available and we still have room in the active batch.
                    if tasks_sent < total_tasks and len(current_batch) < target_batch_size:
                        tasks_sent = self._seed_tasks(tasks, tasks_sent, tasks_completed)
                        
                except queue.Empty:
                    self._error_count += 1
                    self._increment_all_time_stat("total_errors", 1)
                    batch_obs["errors"] += 1
                    self._mark_activity()
                    print("⚠️ Timeout waiting for worker results. Checking worker status...")
                    # Check if workers are still alive
                    alive_workers = sum(1 for p in self.workers if p.is_alive())
                    if alive_workers == 0:
                        print("❌ All workers have stopped unexpectedly.")
                        break
            
            # Final batch
            if current_batch and not stop_requested:
                success, batch_metrics, force_stop = self._process_batch(current_batch, master_path)
                if success:
                    if batch_metrics:
                        stop_requested = self._evaluate_batch_signals(batch_obs, batch_metrics) or stop_requested
                    self._save_pending_batch([])
                else:
                    self._save_pending_batch(current_batch)
                    if force_stop:
                        print("🛑 Delivery sheet cap reached during final flush. Buffered leads were preserved for retry after sheet rotation.")
                    else:
                        print("❌ Final batch delivery failed. Buffered leads were preserved for retry.")
            else:
                self._save_pending_batch(current_batch)
                
        finally:
            self.stop_workers()
            self._finish_health_tracking()
            self._release_run_lock()


def run_parallel(headless: bool = False, next_category: bool = False, continuous: bool = False):
    """Entry point for parallel scraping mode.
    
    Args:
        headless: Run browsers in headless mode
        next_category: Advance to next keyword category before starting
        continuous: Skip batch prompts (for non-interactive runs like dashboard)
    """
    
    try:
        sync_location_source_now(respect_delay=True)
    except Exception as exc:
        print(f"⚠️ Could not refresh sheet-backed locations before run start: {exc}")

    # Handle --next-category flag
    if next_category:
        _advance_to_next_category()
    
    category_index = _load_category_state()
    current_keywords = _get_current_keywords()
    
    print("=" * 60)
    print("  PARALLEL MAPS SCRAPER")
    print(f"  Category: {category_index + 1}/{TOTAL_CATEGORIES} | Keywords: {len(current_keywords)}")
    print(f"  Workers: {NUM_WORKERS} | Delay: {INTER_REQUEST_DELAY_MIN}-{INTER_REQUEST_DELAY_MAX}s | Stagger: {WORKER_STAGGER_DELAY_MIN}-{WORKER_STAGGER_DELAY_MAX}s")
    if continuous:
        print("  Mode: CONTINUOUS (no batch prompts)")
    print("=" * 60)
    
    orchestrator = ParallelOrchestrator(headless=headless, continuous=continuous)
    
    try:
        orchestrator.run()
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user. Cleaning up...")
        orchestrator.stop_workers()
        orchestrator._release_run_lock()
    except Exception:
        orchestrator.stop_workers()
        orchestrator._release_run_lock()
        raise
    
    print("\n✅ Parallel scraping session complete.")


if __name__ == "__main__":
    headless_arg = "--headless" in sys.argv
    next_category_arg = "--next-category" in sys.argv
    continuous_arg = "--continuous" in sys.argv
    run_parallel(headless=headless_arg, next_category=next_category_arg, continuous=continuous_arg)
