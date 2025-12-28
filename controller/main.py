#!/usr/bin/env python3
"""Master Controller - Single entry point for the lead generation pipeline.

This controller orchestrates the scraper in a controlled, resumable way:
1. Reads progress state from scraper
2. Decides next action (start/resume/next-category/done)
3. Runs scraper until a boundary
4. Loops back to check progress again
5. Handles category transitions with 30-min auto-advance

Usage:
    python3 controller/main.py              # Start controller
    python3 controller/main.py --headless   # Run scraper in headless mode
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from status import get_next_action, print_status_summary, get_status_dict
from scraper_runner import run_scraper, check_scraper_ready
from category_handler import prompt_category_advance


def main(headless: bool = False):
    """Main controller loop."""
    
    print("\n" + "🎛️ " * 20)
    print("     MASTER CONTROLLER")
    print("🎛️ " * 20)
    print(f"\nStarted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Check if scraper is available
    if not check_scraper_ready():
        print("❌ Cannot start: scraper not found")
        sys.exit(1)
    
    # Error tracking to prevent infinite loops
    MAX_CONSECUTIVE_ERRORS = 3
    consecutive_errors = 0
    
    # Main loop
    loop_count = 0
    while True:
        loop_count += 1
        print(f"\n{'─' * 60}")
        print(f"🔄 Controller Loop #{loop_count}")
        print(f"{'─' * 60}")
        
        # Step 1: Print status summary
        print_status_summary()
        
        # Step 2: Determine next action
        action = get_next_action()
        print(f"📋 Action: {action}")
        
        # Step 3: Execute action
        if action == "done":
            print("\n🎉 All categories complete! Pipeline finished.")
            print(f"   Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            break
        
        elif action == "start":
            print("\n▶️ Starting fresh scraper run...")
            exit_code, msg = run_scraper(headless=headless)
            print(f"\n📝 Scraper finished: {msg}")
            
            # Track errors
            if exit_code != 0 and exit_code != -1:  # -1 is user interrupt
                consecutive_errors += 1
                print(f"⚠️ Error count: {consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print(f"\n❌ Too many consecutive errors. Stopping.")
                    break
            else:
                consecutive_errors = 0  # Reset on success
        
        elif action == "resume":
            print("\n▶️ Resuming scraper from saved position...")
            exit_code, msg = run_scraper(headless=headless)
            print(f"\n📝 Scraper finished: {msg}")
            
            # Track errors
            if exit_code != 0 and exit_code != -1:
                consecutive_errors += 1
                print(f"⚠️ Error count: {consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print(f"\n❌ Too many consecutive errors. Stopping.")
                    break
            else:
                consecutive_errors = 0
        
        elif action == "next_category":
            consecutive_errors = 0  # Reset errors on category transition
            # Category complete - prompt for advancement
            status = get_status_dict()
            cat = status.get("progress", {}).get("category", {})
            total_cats = cat.get("total", 5)
            current_cat = cat.get("index", 0) + 1
            
            print(f"\n📂 Category {current_cat}/{total_cats} is complete!")
            
            if current_cat >= total_cats:
                print("\n🎉 All categories complete! Pipeline finished.")
                break
            
            # Prompt with 30-min timeout
            if prompt_category_advance(timeout_minutes=30):
                print(f"\n➡️ Advancing to category {current_cat + 1}...")
                exit_code, msg = run_scraper(next_category=True, headless=headless)
                print(f"\n📝 Scraper finished: {msg}")
            else:
                print("\n⏹️ Stopping as requested. Run controller again to resume.")
                break
        
        else:
            print(f"⚠️ Unknown action: {action}")
            break
        
        # Small delay before next loop iteration
        time.sleep(1)
    
    print(f"\n🎛️ Controller ended at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Master Controller for the lead generation pipeline"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the scraper in headless mode"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        main(headless=args.headless)
    except KeyboardInterrupt:
        print("\n\n⏸️ Controller interrupted. Run again to resume from saved progress.")
        sys.exit(0)
