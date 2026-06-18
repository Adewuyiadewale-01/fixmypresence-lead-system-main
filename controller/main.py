#!/usr/bin/env python3
"""Master Controller - Single entry point for the lead generation pipeline.

This controller orchestrates the scraper in a controlled, resumable way:
1. Reads progress state from scraper
2. Decides next action (start/resume/next-category/done)
3. Runs scraper ONCE
4. EXITS unless a retryable delivery timeout triggers cooldown-based auto-retry
5. ONLY auto-restarts for category transitions and delivery timeout recovery

Usage:
    python3 controller/main.py              # Start controller (parallel mode)
    python3 controller/main.py --headless   # Run scraper in headless mode
    python3 controller/main.py --single     # Use single-threaded scraper
"""

from __future__ import annotations

import argparse
import sys
import time
import threading
from datetime import datetime, timedelta

from status import get_next_action, print_status_summary, get_status_dict
from scraper_runner import DELIVERY_FAILED_EXIT_CODE, run_scraper, check_scraper_ready
from category_handler import prompt_category_advance

try:
    from Bots.maps_scraper.config import (
        advance_location_tree_source,
        CATEGORY_ADVANCE_TIMEOUT_MINUTES,
        DELIVERY_RETRY_COOLDOWN_MINUTES,
    )
except Exception:
    advance_location_tree_source = None
    CATEGORY_ADVANCE_TIMEOUT_MINUTES = 30
    DELIVERY_RETRY_COOLDOWN_MINUTES = 15


def _wait_for_delivery_retry(cooldown_minutes: int, attempt: int) -> None:
    retry_at = datetime.now() + timedelta(minutes=cooldown_minutes)
    print(
        f"\n⏳ Delivery timeout cooldown: waiting {cooldown_minutes} minute(s) "
        f"before auto-retry #{attempt}."
    )
    print(f"   Next retry at: {retry_at.strftime('%Y-%m-%d %H:%M:%S')}")
    time.sleep(max(1, cooldown_minutes) * 60)


def _execute_controller_once(headless: bool, parallel: bool, approve_next_category: bool) -> bool:
    """Run one controller decision cycle.

    Returns True when the scraper hit a retryable delivery timeout and the
    controller should cool down before re-running.
    """
    print_status_summary()

    try:
        action = get_next_action()
        print(f"📋 Action: {action}\n")
    except KeyboardInterrupt:
        print("\n\n🛑 Controller stopped by user (Ctrl+C). Exiting...")
        sys.exit(0)

    exit_code = None
    msg = None

    if action == "done":
        print("🎉 All categories complete! Pipeline finished.")
        print(f"   Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    elif action == "start":
        print("▶️ Starting fresh scraper run...")
        exit_code, msg = run_scraper(headless=headless, parallel=parallel)

    elif action == "resume":
        print("▶️ Resuming scraper from saved position...")
        exit_code, msg = run_scraper(headless=headless, parallel=parallel)

    elif action == "next_category":
        status = get_status_dict()
        cat = status.get("progress", {}).get("category", {})
        total_cats = cat.get("total", 5)
        current_cat = cat.get("index", 0) + 1

        print(f"📂 Category {current_cat}/{total_cats} is complete!")

        if current_cat >= total_cats:
            print("\n🎉 All categories complete! Pipeline finished.")
        else:
            if approve_next_category:
                print(f"\n✅ Approval received. Advancing to category {current_cat + 1}...")
                exit_code, msg = run_scraper(next_category=True, headless=headless, parallel=parallel)
            elif sys.stdin.isatty():
                try:
                    if prompt_category_advance(timeout_minutes=CATEGORY_ADVANCE_TIMEOUT_MINUTES):
                        print(f"\n➡️ Advancing to category {current_cat + 1}...")
                        exit_code, msg = run_scraper(next_category=True, headless=headless, parallel=parallel)
                    else:
                        print("\n⏹️ Stopping as requested.")
                except KeyboardInterrupt:
                    print("\n\n🛑 Controller stopped by user during prompt.")
            else:
                print("\n⏸️ Category progression is waiting for explicit approval.")
                print("   Re-run the controller with --approve-next-category to continue.")

    elif action == "next_location_file":
        if advance_location_tree_source is None:
            print("❌ Location-tree advancement is unavailable.")
        else:
            transition = advance_location_tree_source()
            if transition.get("advanced"):
                print(
                    "\n➡️ Location file complete. "
                    f"Loaded {transition.get('current_file')} "
                    f"({transition.get('locations_loaded', 0)} locations)."
                )
                exit_code, msg = run_scraper(
                    headless=headless,
                    parallel=parallel,
                )
            elif transition.get("exhausted"):
                print("\n🎉 Every discovered location file is complete. Pipeline finished.")
            else:
                print(f"\n⚠️ Could not advance location files: {transition.get('reason', 'unknown reason')}")

    else:
        print(f"⚠️ Unknown action: {action}")

    if exit_code == -1:
        print("\n🛑 User interrupted scraping.")
    elif msg:
        print(f"\n📝 {msg}")

    return exit_code == DELIVERY_FAILED_EXIT_CODE


def main(headless: bool = False, parallel: bool = True, approve_next_category: bool = False):
    """Main controller entry point."""

    mode_str = "PARALLEL (6 workers)" if parallel else "SINGLE-THREADED"
    print("\n" + "🎛️ " * 20)
    print(f"     MASTER CONTROLLER - {mode_str}")
    print("🎛️ " * 20)
    print(f"\nStarted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    if not check_scraper_ready():
        print("❌ Cannot start: scraper not found")
        sys.exit(1)

    delivery_retry_attempt = 0
    while True:
        should_retry_delivery = _execute_controller_once(
            headless=headless,
            parallel=parallel,
            approve_next_category=approve_next_category,
        )
        if not should_retry_delivery:
            break

        delivery_retry_attempt += 1
        _wait_for_delivery_retry(DELIVERY_RETRY_COOLDOWN_MINUTES, delivery_retry_attempt)

    print(f"\n{'─' * 60}")
    print(f"🎛️ Controller ended at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("   Run 'python3 controller/main.py' to resume or check status.")
    print(f"{'─' * 60}\n")


# Watcher thread state
_watcher_stop_event = threading.Event()

def _run_watcher_in_thread(name: str, tick_func, interval_minutes: int):
    """Run a specific watcher in a background thread."""
    try:
        print(f"🔄 {name} started in background thread")
        
        while not _watcher_stop_event.is_set():
            try:
                tick_func()
            except Exception as e:
                print(f"[{name}] Error: {e}")
            
            # Sleep in small intervals to check stop event
            # Use smaller chunks for responsiveness
            sleep_total = interval_minutes * 60
            for _ in range(sleep_total):
                if _watcher_stop_event.is_set():
                    break
                time.sleep(1)
        
        print(f"🛑 {name} stopped")
    except Exception as e:
        print(f"❌ Could not run {name}: {e}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Master Controller for the lead generation pipeline"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the scraper in headless mode"
    )
    parser.add_argument(
        "--single",
        action="store_true",
        help="Use single-threaded scraper instead of parallel"
    )
    parser.add_argument(
        "--with-watcher",
        action="store_true",
        help="Run enrichment, ultra-validation, and scraper_v3 watchers in background"
    )
    parser.add_argument(
        "--approve-next-category",
        action="store_true",
        help="Explicitly approve advancing to the next category in non-interactive mode"
    )
    parser.add_argument(
        "--watcher-interval",
        type=int,
        default=10,
        help="Watcher interval in minutes (default: 10)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    
    watcher_threads = []
    
    try:
        # Start watchers in background if requested
        if args.with_watcher:
            _watcher_stop_event.clear()
            
            # 1. Enrichment Watcher
            try:
                from enrichment_watcher import watcher_tick as enrich_tick
                t1 = threading.Thread(
                    target=_run_watcher_in_thread,
                    args=("Enrichment Watcher", enrich_tick, args.watcher_interval),
                    daemon=True
                )
                t1.start()
                watcher_threads.append(t1)
            except ImportError:
                print("⚠️ Could not import enrichment_watcher")

            # 2. Ultra Validated Watcher
            try:
                from ultra_validated_watcher import watcher_tick as ultra_tick
                t2 = threading.Thread(
                    target=_run_watcher_in_thread,
                    args=("Ultra Validated Watcher", ultra_tick, args.watcher_interval),
                    daemon=True
                )
                t2.start()
                watcher_threads.append(t2)
            except ImportError:
                print("⚠️ Could not import ultra_validated_watcher")

            # 3. Scraper V3 Watcher
            try:
                from scraper_v3_watcher import watcher_tick as scraper_v3_tick
                t3 = threading.Thread(
                    target=_run_watcher_in_thread,
                    args=("Scraper V3 Watcher", scraper_v3_tick, args.watcher_interval),
                    daemon=True
                )
                t3.start()
                watcher_threads.append(t3)
            except ImportError:
                print("⚠️ Could not import scraper_v3_watcher")
        
        main(
            headless=args.headless,
            parallel=not args.single,
            approve_next_category=args.approve_next_category,
        )
        
    except KeyboardInterrupt:
        print("\n\n⏸️ Controller interrupted. Run again to resume from saved progress.")
    finally:
        # Signal watchers to stop
        if watcher_threads:
            print("🛑 Signaling watchers to stop...")
            _watcher_stop_event.set()
            for t in watcher_threads:
                if t.is_alive():
                    t.join(timeout=2)
        
        sys.exit(0)
