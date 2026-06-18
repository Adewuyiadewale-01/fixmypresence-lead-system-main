"""Status reader for the master controller.

Reads scraper progress and provides a clean interface for decision-making.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add Bots path to import from maps_scraper
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "Bots" / "maps_scraper"))

from progress_status import (
    get_progress_status,
    get_category_status,
    get_current_location,
    get_resume_cursor,
    get_locations_usage,
    needs_next_category,
    is_all_complete,
)
from config import get_location_tree_status


def get_next_action() -> str:
    """
    Determine the next action based on progress state.
    
    Returns:
        "start"         - No progress, start fresh
        "resume"        - In progress, resume scraper
        "next_category" - Category complete, need to advance
        "next_location_file" - All categories complete; load the next file
        "done"          - All categories and location files complete
    """
    status = get_progress_status()
    
    if not status["has_progress"]:
        return "start"
    
    if status["status"] == "all_complete":
        tree_status = get_location_tree_status()
        if (
            tree_status.get("enabled")
            and tree_status.get("current_file")
            and not tree_status.get("current_file_completed")
        ):
            return "next_location_file"
        return "done"
    
    if status["status"] == "category_complete":
        return "next_category"
    
    if status["status"] == "in_progress":
        return "resume"
    
    # Default to start if unknown state
    return "start"


def print_status_summary():
    """Print a compact status summary for the controller."""
    status = get_progress_status()
    
    print("\n" + "=" * 60)
    print("🎛️  MASTER CONTROLLER - STATUS")
    print("=" * 60)
    
    if not status["has_progress"]:
        print("📭 No saved progress. Ready for fresh start.")
        print("=" * 60 + "\n")
        return
    
    progress = status["progress"]
    cat = progress.get("category", {})
    current_loc = progress.get("current_location", {})
    resume = progress.get("resume_cursor", {})
    overall = progress.get("overall", {})
    locations = progress.get("locations", {})
    
    # Category
    print(f"\n📂 Category: {cat.get('index', 0)+1}/{cat.get('total', '?')}", end="")
    if cat.get("is_complete"):
        print(" ✅ COMPLETE")
    else:
        print(f" ({cat.get('progress_pct', 0):.1f}% of all categories)")
    
    # Current position (if not category complete)
    if not cat.get("is_complete"):
        print(f"📍 Location: [{current_loc.get('index', 0)+1}/{locations.get('total', '?')}] {current_loc.get('name', '?')}")
        print(f"🔑 Keyword:  [{current_loc.get('keyword_index', 0)+1}/{current_loc.get('keywords_total', '?')}] {current_loc.get('keyword_name', '?')}")
    
    # Resume cursor
    print(f"\n🎯 NEXT: ", end="")
    if resume.get("category_complete"):
        print("Ready for --next-category")
    elif resume.get("is_retry"):
        print(f"🔄 RETRY '{resume.get('keyword_name')}' in '{resume.get('location_name')}'")
    else:
        print(f"'{resume.get('keyword_name')}' in '{resume.get('location_name')}'")
    
    # Stats
    print(f"\n📊 Searches: {overall.get('completed_searches', 0)}/{overall.get('total_searches', '?')}")
    print(f"📦 Leads: {overall.get('total_leads_collected', 0)} | Batches: {overall.get('batch_number', 0)}")
    print(f"🕐 Last saved: {progress.get('last_saved', 'unknown')}")
    
    print("=" * 60 + "\n")


def get_status_dict() -> dict:
    """Get the raw status dict for programmatic access."""
    return get_progress_status()


if __name__ == "__main__":
    print_status_summary()
    action = get_next_action()
    print(f"Next action: {action}")
