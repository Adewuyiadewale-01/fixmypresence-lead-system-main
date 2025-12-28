"""Progress status checker for the Google Maps scraper with category support.

This module provides utilities to check the current progress state,
intended for use by external controllers to decide whether to start
a fresh run, continue, or advance to the next category.

Usage:
    # As a CLI tool:
    python progress_status.py          # Print status
    python progress_status.py --json   # Output as JSON (for controllers)
    
    # As a module:
    from progress_status import get_progress_status, needs_next_category
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import ALL_KEYWORD_CATEGORIES, LOCATIONS, PROGRESS_FILE, TOTAL_CATEGORIES


def get_progress_status() -> dict:
    """Get the current progress status."""
    result = {
        "has_progress": False,
        "is_complete": False,
        "status": "fresh",
        "progress": None,
        "summary": "No saved progress. Ready for fresh run.",
        "checked_at": datetime.now().isoformat(),
    }
    
    if not PROGRESS_FILE.exists():
        return result
    
    try:
        with open(PROGRESS_FILE, "r") as f:
            progress = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        result["summary"] = f"Progress file exists but is corrupted: {e}"
        result["status"] = "error"
        return result
    
    result["has_progress"] = True
    result["progress"] = progress
    
    cat = progress.get("category", {})
    overall = progress.get("overall", {})
    current_loc = progress.get("current_location", {})
    
    # Check if ALL categories complete
    if cat.get("categories_completed", 0) >= TOTAL_CATEGORIES:
        result["is_complete"] = True
        result["status"] = "all_complete"
        result["summary"] = f"All {TOTAL_CATEGORIES} categories completed. Total leads: {overall.get('total_leads_collected', 0)}"
    elif cat.get("is_complete"):
        result["status"] = "category_complete"
        result["summary"] = (
            f"Category {cat.get('index', 0)+1}/{TOTAL_CATEGORIES} complete. "
            f"Use --next-category to advance."
        )
    else:
        result["status"] = "in_progress"
        result["summary"] = (
            f"Category {cat.get('index', 0)+1}/{TOTAL_CATEGORIES}: "
            f"{overall.get('completed_searches', 0)}/{overall.get('total_searches', '?')} searches "
            f"({overall.get('progress_pct', 0):.1f}%). "
            f"Current: '{current_loc.get('keyword_name', '?')}' in '{current_loc.get('name', '?')}'"
        )
    
    return result


def has_pending_progress() -> bool:
    """Check if there's pending (incomplete) progress to resume."""
    status = get_progress_status()
    return status["has_progress"] and status["status"] == "in_progress"


def should_continue() -> bool:
    """Check if should continue current category."""
    return has_pending_progress()


def should_start_fresh() -> bool:
    """Check if a fresh start is needed."""
    status = get_progress_status()
    return not status["has_progress"] or status["is_complete"]


def needs_next_category() -> bool:
    """Check if current category is complete and ready for --next-category."""
    status = get_progress_status()
    return status["status"] == "category_complete"


def is_all_complete() -> bool:
    """Check if all categories have been completed."""
    status = get_progress_status()
    return status["is_complete"]


def get_category_status() -> dict:
    """Get detailed category status."""
    status = get_progress_status()
    
    if not status["progress"]:
        return {
            "current_index": 0,
            "total": TOTAL_CATEGORIES,
            "categories_completed": 0,
            "progress_pct": 0.0,
            "is_current_complete": False,
            "all_complete": False,
            "needs_next_category": False,
        }
    
    cat = status["progress"].get("category", {})
    return {
        "current_index": cat.get("index", 0),
        "total": TOTAL_CATEGORIES,
        "categories_completed": cat.get("categories_completed", 0),
        "progress_pct": cat.get("progress_pct", 0.0),
        "keywords_in_category": cat.get("keywords_in_category", 0),
        "is_current_complete": cat.get("is_complete", False),
        "all_complete": cat.get("categories_completed", 0) >= TOTAL_CATEGORIES,
        "needs_next_category": needs_next_category(),
    }


def get_current_location() -> Optional[dict]:
    """Get info about the current location being processed."""
    status = get_progress_status()
    if not status["progress"]:
        return None
    return status["progress"].get("current_location")


def get_resume_cursor() -> Optional[dict]:
    """Get the exact resume cursor."""
    status = get_progress_status()
    if not status["progress"]:
        return None
    if status["is_complete"]:
        return None
    return status["progress"].get("resume_cursor")


def get_locations_usage() -> dict:
    """Get locations usage info (within current category)."""
    status = get_progress_status()
    
    if not status["progress"]:
        return {
            "total": len(LOCATIONS),
            "used": 0,
            "used_pct": 0.0,
            "completed": 0,
            "completed_pct": 0.0,
            "all_used": False,
            "all_completed": False,
        }
    
    locations = status["progress"].get("locations", {})
    return {
        "total": locations.get("total", len(LOCATIONS)),
        "used": locations.get("used", 0),
        "used_pct": locations.get("used_pct", 0.0),
        "completed": locations.get("completed", 0),
        "completed_pct": locations.get("completed_pct", 0.0),
        "all_used": locations.get("all_used", False),
        "all_completed": locations.get("all_completed", False),
    }


def print_status(as_json: bool = False):
    """Print the current status to stdout."""
    status = get_progress_status()
    
    if as_json:
        print(json.dumps(status, indent=2))
        return
    
    print("\n" + "=" * 65)
    print("📊 SCRAPER PROGRESS STATUS")
    print("=" * 65)
    print(f"Status: {status['status'].upper()}")
    print(f"Summary: {status['summary']}")
    
    if status["progress"]:
        p = status["progress"]
        cat = p.get("category", {})
        current_loc = p.get("current_location", {})
        resume = p.get("resume_cursor", {})
        overall = p.get("overall", {})
        locations = p.get("locations", {})
        
        # Category info
        print(f"\n📂 CATEGORY:")
        print(f"   Current:   {cat.get('index', 0)+1}/{cat.get('total', '?')}")
        print(f"   Completed: {cat.get('categories_completed', 0)}/{cat.get('total', '?')} ({cat.get('progress_pct', 0):.1f}%)")
        print(f"   Keywords:  {cat.get('keywords_in_category', '?')}")
        if cat.get('is_complete'):
            print(f"   ✅ CATEGORY COMPLETE - use --next-category to advance")
        
        # Current position
        if not cat.get('is_complete'):
            print(f"\n📍 CURRENT POSITION:")
            print(f"   Location: [{current_loc.get('index', 0)+1}/{locations.get('total', '?')}] {current_loc.get('name', '?')}")
            print(f"   Keyword:  [{current_loc.get('keyword_index', 0)+1}/{current_loc.get('keywords_total', '?')}] {current_loc.get('keyword_name', '?')}")
            print(f"   Progress: {current_loc.get('progress_pct', 0):.1f}%")
        
        # Resume cursor
        print(f"\n🎯 RESUME CURSOR:")
        if resume.get("category_complete"):
            print(f"   ➡️ Ready for --next-category")
        elif resume.get("is_retry"):
            print(f"   🔄 RETRY: '{resume.get('keyword_name')}' in '{resume.get('location_name')}'")
        else:
            print(f"   ▶️ NEXT: '{resume.get('keyword_name')}' in '{resume.get('location_name')}'")
        
        # Locations
        print(f"\n📊 LOCATIONS (in category):")
        print(f"   Used:      {locations.get('used', 0)}/{locations.get('total', '?')} ({locations.get('used_pct', 0):.1f}%)")
        print(f"   Completed: {locations.get('completed', 0)}/{locations.get('total', '?')} ({locations.get('completed_pct', 0):.1f}%)")
        
        # Overall
        print(f"\n📈 PROGRESS (in category):")
        print(f"   Searches: {overall.get('completed_searches', 0)}/{overall.get('total_searches', '?')} ({overall.get('progress_pct', 0):.1f}%)")
        print(f"   Leads:    {overall.get('total_leads_collected', 0)}")
        print(f"   Batches:  {overall.get('batch_number', 0)}")
        print(f"   Saved:    {p.get('last_saved', 'unknown')}")
    
    print("=" * 65 + "\n")


if __name__ == "__main__":
    as_json = "--json" in sys.argv
    print_status(as_json=as_json)
