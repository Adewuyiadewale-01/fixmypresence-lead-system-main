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
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, Dict, List, Tuple

from config import (
    ALL_KEYWORD_CATEGORIES,
    LOCATIONS,
    TASKS_PROGRESS_FILE,
    MASTER_OUTPUT,
    TOTAL_CATEGORIES,
    KEYWORD_CATEGORY_1,
    KEYWORD_CATEGORY_2,
    KEYWORD_CATEGORY_3,
    KEYWORD_CATEGORY_4,
)

# Map category index to keywords
CATEGORY_KEYWORDS = {
    0: KEYWORD_CATEGORY_1,
    1: KEYWORD_CATEGORY_2,
    2: KEYWORD_CATEGORY_3,
    3: KEYWORD_CATEGORY_4,
}


def _parse_completed_tasks() -> Tuple[Set[str], Dict[str, List[str]], str]:
    """
    Parse completed_tasks.jsonl and return:
    - Set of completed task keys ("keyword|location")
    - Dict mapping location -> list of completed keywords for that location
    - Last saved timestamp
    """
    completed_keys: Set[str] = set()
    location_keywords: Dict[str, List[str]] = defaultdict(list)
    last_timestamp = "unknown"
    
    if not TASKS_PROGRESS_FILE.exists():
        return completed_keys, dict(location_keywords), last_timestamp
    
    try:
        with open(TASKS_PROGRESS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    keyword = data.get("keyword", "")
                    location = data.get("location", "")
                    timestamp = data.get("timestamp", "")
                    
                    if keyword and location:
                        key = f"{keyword}|{location}"
                        completed_keys.add(key)
                        location_keywords[location].append(keyword)
                        if timestamp:
                            last_timestamp = timestamp
                except json.JSONDecodeError:
                    continue
    except IOError:
        pass
    
    return completed_keys, dict(location_keywords), last_timestamp


def _detect_active_category(completed_keys: Set[str]) -> int:
    """
    Detect which category is currently being processed based on completed tasks.
    Returns category index (0-4).
    """
    if not completed_keys:
        return 0
    
    # Check which category's keywords appear in completed tasks
    for cat_idx, keywords in CATEGORY_KEYWORDS.items():
        for key in completed_keys:
            keyword = key.split("|")[0]
            if keyword in keywords:
                return cat_idx
    
    return 0


def _count_leads_in_master() -> int:
    """Count total leads in the master CSV file."""
    if not MASTER_OUTPUT.exists():
        return 0
    
    try:
        with open(MASTER_OUTPUT, "r", encoding="utf-8") as f:
            # Subtract 1 for header row
            return max(0, sum(1 for _ in f) - 1)
    except IOError:
        return 0


def _count_batch_files() -> int:
    """Count the number of batch CSV files."""
    output_dir = MASTER_OUTPUT.parent
    if not output_dir.exists():
        return 0
    
    return len(list(output_dir.glob("leads_batch_*.csv")))


def _find_next_task(completed_keys: Set[str], category_keywords: List[str]) -> Tuple[Optional[str], Optional[str], int, int]:
    """
    Find the next task to execute.
    Returns: (next_keyword, next_location, location_index, keyword_index)
    """
    for loc_idx, location in enumerate(LOCATIONS):
        for kw_idx, keyword in enumerate(category_keywords):
            key = f"{keyword}|{location}"
            if key not in completed_keys:
                return keyword, location, loc_idx, kw_idx
    
    return None, None, -1, -1


def get_progress_status() -> dict:
    """Get the current progress status with complete statistics."""
    
    # Parse completed tasks
    completed_keys, location_keywords, last_timestamp = _parse_completed_tasks()
    completed_count = len(completed_keys)
    
    # Base result for fresh state
    if completed_count == 0:
        return {
            "has_progress": False,
            "is_complete": False,
            "status": "fresh",
            "progress": None,
            "summary": "No saved progress. Ready for fresh run.",
            "checked_at": datetime.now().isoformat(),
        }
    
    # Detect active category
    category_index = _detect_active_category(completed_keys)
    category_keywords = CATEGORY_KEYWORDS.get(category_index, KEYWORD_CATEGORY_1)
    keywords_in_category = len(category_keywords)
    
    # Calculate totals for this category
    total_locations = len(LOCATIONS)
    total_tasks = total_locations * keywords_in_category
    
    # Calculate per-location completion
    locations_fully_completed = 0
    locations_partially_completed = 0
    locations_used = 0
    
    for location in LOCATIONS:
        completed_for_loc = len(location_keywords.get(location, []))
        if completed_for_loc > 0:
            locations_used += 1
            if completed_for_loc >= keywords_in_category:
                locations_fully_completed += 1
            else:
                locations_partially_completed += 1
    
    # Find next task
    next_keyword, next_location, next_loc_idx, next_kw_idx = _find_next_task(
        completed_keys, category_keywords
    )
    
    # Check completion status
    category_complete = (next_keyword is None)
    all_complete = category_complete and (category_index >= TOTAL_CATEGORIES - 1)
    
    # Calculate progress percentage
    progress_pct = (completed_count / total_tasks * 100) if total_tasks > 0 else 0
    locations_completed_pct = (locations_fully_completed / total_locations * 100) if total_locations > 0 else 0
    locations_used_pct = (locations_used / total_locations * 100) if total_locations > 0 else 0
    categories_completed_pct = (category_index / TOTAL_CATEGORIES * 100) if category_complete else ((category_index) / TOTAL_CATEGORIES * 100)
    
    # Count leads and batches
    total_leads = _count_leads_in_master()
    batch_count = _count_batch_files()
    
    # Determine status
    if all_complete:
        status = "all_complete"
        summary = f"All {TOTAL_CATEGORIES} categories complete! {completed_count} searches, {total_leads} leads collected."
    elif category_complete:
        status = "category_complete"
        summary = f"Category {category_index + 1}/{TOTAL_CATEGORIES} complete. Ready for --next-category."
    else:
        status = "in_progress"
        summary = f"Category {category_index + 1}: {completed_count}/{total_tasks} searches ({progress_pct:.1f}%). {locations_fully_completed}/{total_locations} locations complete."
    
    # Current location info (the one being worked on)
    current_loc_name = next_location if next_location else LOCATIONS[-1] if LOCATIONS else "?"
    current_loc_idx = next_loc_idx if next_loc_idx >= 0 else (total_locations - 1)
    current_kw_idx = next_kw_idx if next_kw_idx >= 0 else (keywords_in_category - 1)
    current_kw_name = next_keyword if next_keyword else category_keywords[-1] if category_keywords else "?"
    
    # Build result
    result = {
        "has_progress": True,
        "is_complete": all_complete,
        "status": status,
        "summary": summary,
        "checked_at": datetime.now().isoformat(),
        "progress": {
            "category": {
                "index": category_index,
                "total": TOTAL_CATEGORIES,
                "keywords_in_category": keywords_in_category,
                "keyword_names": list(category_keywords),
                "is_complete": category_complete,
                "categories_completed": category_index if not category_complete else category_index + 1,
                "progress_pct": categories_completed_pct,
            },
            "current_location": {
                "index": current_loc_idx,
                "name": current_loc_name,
                "keyword_index": current_kw_idx,
                "keyword_name": current_kw_name,
                "keywords_total": keywords_in_category,
                "progress_pct": progress_pct,
            },
            "resume_cursor": {
                "keyword_name": next_keyword,
                "location_name": next_location,
                "location_index": next_loc_idx,
                "keyword_index": next_kw_idx,
                "category_complete": category_complete,
                "is_retry": False,
            },
            "locations": {
                "total": total_locations,
                "used": locations_used,
                "used_pct": locations_used_pct,
                "completed": locations_fully_completed,
                "completed_pct": locations_completed_pct,
                "partial": locations_partially_completed,
                "all_used": locations_used >= total_locations,
                "all_completed": locations_fully_completed >= total_locations,
            },
            "overall": {
                "completed_searches": completed_count,
                "total_searches": total_tasks,
                "progress_pct": progress_pct,
                "total_leads_collected": total_leads,
                "batch_number": batch_count,
            },
            "last_saved": last_timestamp,
        },
    }
    
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
        print(f"   Current:   {cat.get('index', 0)+1}/{cat.get('total', TOTAL_CATEGORIES)}")
        print(f"   Completed: {cat.get('categories_completed', 0)}/{cat.get('total', TOTAL_CATEGORIES)} ({cat.get('progress_pct', 0):.1f}%)")
        print(f"   Keywords:  {cat.get('keywords_in_category', 0)} ({', '.join(cat.get('keyword_names', [])[:3])}{'...' if len(cat.get('keyword_names', [])) > 3 else ''})")
        if cat.get('is_complete'):
            print(f"   ✅ CATEGORY COMPLETE - use --next-category to advance")
        
        # Current position
        if not cat.get('is_complete'):
            print(f"\n📍 CURRENT POSITION:")
            print(f"   Location: [{current_loc.get('index', 0)+1}/{locations.get('total', len(LOCATIONS))}] {current_loc.get('name', '?')}")
            print(f"   Keyword:  [{current_loc.get('keyword_index', 0)+1}/{current_loc.get('keywords_total', 0)}] {current_loc.get('keyword_name', '?')}")
            print(f"   Progress: {overall.get('progress_pct', 0):.1f}%")
        
        # Resume cursor
        print(f"\n🎯 RESUME CURSOR:")
        if resume.get("category_complete"):
            print(f"   ➡️ Ready for --next-category")
        elif resume.get("is_retry"):
            print(f"   🔄 RETRY: '{resume.get('keyword_name')}' in '{resume.get('location_name')}'")
        elif resume.get("keyword_name") and resume.get("location_name"):
            print(f"   ▶️ NEXT: '{resume.get('keyword_name')}' in '{resume.get('location_name')}'")
        else:
            print(f"   ✅ All tasks completed for this category")
        
        # Locations
        print(f"\n📊 LOCATIONS (in category):")
        print(f"   Used:      {locations.get('used', 0)}/{locations.get('total', len(LOCATIONS))} ({locations.get('used_pct', 0):.1f}%)")
        print(f"   Completed: {locations.get('completed', 0)}/{locations.get('total', len(LOCATIONS))} ({locations.get('completed_pct', 0):.1f}%)")
        if locations.get('partial', 0) > 0:
            print(f"   Partial:   {locations.get('partial', 0)} (some keywords done)")
        
        # Overall
        print(f"\n📈 PROGRESS (in category):")
        print(f"   Searches: {overall.get('completed_searches', 0)}/{overall.get('total_searches', 0)} ({overall.get('progress_pct', 0):.1f}%)")
        print(f"   Leads:    {overall.get('total_leads_collected', 0)}")
        print(f"   Batches:  {overall.get('batch_number', 0)}")
        print(f"   Saved:    {p.get('last_saved', 'unknown')}")
    
    print("=" * 65 + "\n")


if __name__ == "__main__":
    as_json = "--json" in sys.argv
    print_status(as_json=as_json)
