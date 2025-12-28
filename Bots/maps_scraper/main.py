"""Batch-orchestrated Google Maps lead scraper with progress tracking and category support."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from config import (
    ALL_KEYWORD_CATEGORIES,
    BATCH_SIZE,
    LOCATIONS,
    MASTER_OUTPUT,
    OUTPUT_DIR,
    PROGRESS_FILE,
    TOTAL_CATEGORIES,
)
from google_maps_scraper import CaptchaDetected, GoogleMapsScraper
from utils import Lead, append_to_master, dedupe_leads, export_batch
from webhook import send_batch_to_webhook
from common.validator import get_website_status


# ============================================================================
# Progress Tracking
# ============================================================================

def load_progress() -> Optional[dict]:
    """Load progress from the JSON file if it exists."""
    if not PROGRESS_FILE.exists():
        return None
    try:
        with open(PROGRESS_FILE, "r") as f:
            progress = json.load(f)
        
        cat = progress.get("category", {})
        current_loc = progress.get("current_location", {})
        overall = progress.get("overall", {})
        
        print(f"\n📍 Found saved progress from: {progress.get('last_saved', 'unknown')}")
        print(f"   Category: {cat.get('index', 0)+1}/{cat.get('total', '?')} ({cat.get('progress_pct', 0):.1f}% of categories)")
        print(f"   Location: [{current_loc.get('index', 0)+1}/{progress.get('locations', {}).get('total', '?')}] {current_loc.get('name', '?')}")
        print(f"   Keyword:  [{current_loc.get('keyword_index', 0)+1}/{current_loc.get('keywords_total', '?')}] {current_loc.get('keyword_name', '?')}")
        print(f"   Leads collected: {overall.get('total_leads_collected', 0)}")
        
        if cat.get("is_complete"):
            print(f"\n✅ Category {cat.get('index', 0)+1} is COMPLETE. Use --next-category to advance.")
        
        return progress
    except (json.JSONDecodeError, KeyError) as e:
        print(f"⚠️ Could not parse progress file: {e}")
        return None


def save_progress(
    category_index: int,
    location_index: int,
    keyword_index: int,
    location: str,
    keyword: str,
    completed_searches: int,
    batch_number: int,
    total_leads: int,
    search_completed: bool = True,
    category_complete: bool = False,
):
    """Save current progress to the JSON file with category tracking."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    keywords = ALL_KEYWORD_CATEGORIES[category_index]
    total_locations = len(LOCATIONS)
    total_keywords = len(keywords)
    total_searches_in_category = total_locations * total_keywords
    
    # Category progress
    categories_completed = category_index if not category_complete else category_index + 1
    category_progress_pct = (categories_completed / TOTAL_CATEGORIES) * 100 if TOTAL_CATEGORIES > 0 else 0
    
    # Location progress within current category
    location_progress_pct = ((keyword_index + 1) / total_keywords) * 100 if total_keywords > 0 else 0
    overall_category_pct = (completed_searches / total_searches_in_category) * 100 if total_searches_in_category > 0 else 0
    
    # Locations stats
    locations_completed = location_index
    locations_completed_pct = (locations_completed / total_locations) * 100 if total_locations > 0 else 0
    locations_used = location_index + 1
    locations_used_pct = (locations_used / total_locations) * 100 if total_locations > 0 else 0
    all_locations_used = locations_used >= total_locations
    
    # Is current location complete?
    current_location_complete = (keyword_index + 1) >= total_keywords and search_completed
    
    # Calculate resume cursor
    if category_complete:
        # Category done, next run should use --next-category
        resume_cat_idx = category_index
        resume_loc_idx = 0
        resume_kw_idx = 0
        is_retry = False
    elif search_completed:
        resume_cat_idx = category_index
        resume_kw_idx = keyword_index + 1
        resume_loc_idx = location_index
        if resume_kw_idx >= total_keywords:
            resume_kw_idx = 0
            resume_loc_idx += 1
        is_retry = False
    else:
        resume_cat_idx = category_index
        resume_kw_idx = keyword_index
        resume_loc_idx = location_index
        is_retry = True
    
    progress = {
        # === CATEGORY ===
        "category": {
            "index": category_index,
            "total": TOTAL_CATEGORIES,
            "keywords_in_category": total_keywords,
            "is_complete": category_complete,
            "categories_completed": categories_completed,
            "progress_pct": round(category_progress_pct, 2),
        },
        
        # === CURRENT POSITION ===
        "current_location": {
            "index": location_index,
            "name": location,
            "keyword_index": keyword_index,
            "keyword_name": keyword,
            "keywords_done": keyword_index + 1 if search_completed else keyword_index,
            "keywords_total": total_keywords,
            "progress_pct": round(location_progress_pct, 2),
            "is_complete": current_location_complete,
        },
        
        # === RESUME CURSOR ===
        "resume_cursor": {
            "category_index": resume_cat_idx,
            "location_index": resume_loc_idx,
            "keyword_index": resume_kw_idx,
            "location_name": LOCATIONS[resume_loc_idx] if resume_loc_idx < total_locations else None,
            "keyword_name": keywords[resume_kw_idx] if resume_kw_idx < total_keywords else None,
            "is_retry": is_retry,
            "category_complete": category_complete,
        },
        
        # === OVERALL (within category) ===
        "overall": {
            "completed_searches": completed_searches,
            "total_searches": total_searches_in_category,
            "progress_pct": round(overall_category_pct, 2),
            "total_leads_collected": total_leads,
            "batch_number": batch_number,
        },
        
        # === LOCATIONS SUMMARY ===
        "locations": {
            "total": total_locations,
            "used": locations_used,
            "used_pct": round(locations_used_pct, 2),
            "completed": locations_completed,
            "completed_pct": round(locations_completed_pct, 2),
            "all_used": all_locations_used,
            "all_completed": locations_completed >= total_locations,
        },
        
        # === METADATA ===
        "search_completed": search_completed,
        "last_saved": datetime.now().isoformat(),
    }
    
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def clear_progress():
    """Remove the progress file (used when starting fresh)."""
    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()
        print("🗑️ Cleared previous progress file.")


def prompt_resume() -> bool:
    """Ask the user whether to resume from saved progress."""
    print("\nDo you want to resume from where you left off? [Y/n]: ", end="")
    try:
        response = input().strip().lower()
    except EOFError:
        return True
    return response not in {"n", "no"}


# ============================================================================
# Main Scraper Logic
# ============================================================================

def prompt_to_continue(batch_number: int) -> bool:
    print(f"\nBatch {batch_number} ready. Press Enter for the next {BATCH_SIZE} leads or type 'q' to stop:", end=" ")
    try:
        response = input().strip().lower()
    except EOFError:
        return False
    return response not in {"q", "quit", "exit"}


def summarise_batch(batch_number: int, leads: List[Lead]):
    print(f"\n--- Batch {batch_number} ({len(leads)} leads) ---")
    for lead in leads:
        website = lead.website or "<no website>"
        print(f" • {lead.name}  |  {website}")


def run(headless: bool = False, fresh_start: bool = False, next_category: bool = False):
    """
    Run the scraper for the current category.
    
    Args:
        headless: Run browser in headless mode
        fresh_start: If True, ignore saved progress and start from the beginning
        next_category: If True, advance to the next category
    """
    # Determine starting state
    category_index = 0
    start_loc_idx = 0
    start_kw_idx = 0
    batch_number = 0
    total_leads = 0
    completed_searches = 0
    
    if fresh_start:
        clear_progress()
        if MASTER_OUTPUT.exists():
            MASTER_OUTPUT.unlink()
    else:
        saved_progress = load_progress()
        if saved_progress:
            cat = saved_progress.get("category", {})
            resume = saved_progress.get("resume_cursor", {})
            overall = saved_progress.get("overall", {})
            
            if next_category:
                # Advance to next category
                if cat.get("is_complete"):
                    category_index = cat.get("index", 0) + 1
                    if category_index >= TOTAL_CATEGORIES:
                        print(f"\n🎉 All {TOTAL_CATEGORIES} categories have been completed!")
                        print("   Use --fresh to start over with new data.")
                        return
                    print(f"\n➡️ Advancing to category {category_index + 1}/{TOTAL_CATEGORIES}")
                    # Reset location/keyword indices for new category
                    start_loc_idx = 0
                    start_kw_idx = 0
                    completed_searches = 0
                    # Keep batch_number and total_leads from previous
                    batch_number = overall.get("batch_number", 0)
                    total_leads = overall.get("total_leads_collected", 0)
                else:
                    print(f"\n⚠️ Current category {cat.get('index', 0)+1} is not complete yet!")
                    print("   Finish the current category first, then use --next-category.")
                    return
            elif cat.get("is_complete"):
                # Category is done, need --next-category to proceed
                print(f"\n✅ Category {cat.get('index', 0)+1}/{TOTAL_CATEGORIES} is complete.")
                print("   Use --next-category to advance to the next category.")
                return
            elif prompt_resume():
                # Resume within current category
                category_index = resume.get("category_index", 0)
                start_loc_idx = resume.get("location_index", 0)
                start_kw_idx = resume.get("keyword_index", 0)
                batch_number = overall.get("batch_number", 0)
                total_leads = overall.get("total_leads_collected", 0)
                completed_searches = overall.get("completed_searches", 0)
                
                if resume.get("is_retry"):
                    print(f"\n🔄 Retrying interrupted search...")
                else:
                    print(f"\n▶️ Resuming from saved position...")
            else:
                clear_progress()
                if MASTER_OUTPUT.exists():
                    MASTER_OUTPUT.unlink()
        elif next_category:
            print("\n⚠️ No saved progress. Starting from category 1.")
    
    # Validate category index
    if category_index >= TOTAL_CATEGORIES:
        print(f"\n🎉 All {TOTAL_CATEGORIES} categories have been completed!")
        return
    
    # Get keywords for this category
    keywords = ALL_KEYWORD_CATEGORIES[category_index]
    total_searches_in_category = len(LOCATIONS) * len(keywords)
    
    # Print session info
    print(f"\n📊 Session Info:")
    print(f"   Category: {category_index + 1}/{TOTAL_CATEGORIES}")
    print(f"   Keywords in category: {len(keywords)}")
    print(f"   Locations: {len(LOCATIONS)}")
    print(f"   Total searches for category: {total_searches_in_category}")
    print(f"   Already done: {completed_searches}")
    print(f"   Remaining: {total_searches_in_category - completed_searches}")
    
    scraper = GoogleMapsScraper(headless=headless)
    scraper.start()

    batch: List[Lead] = []
    seen_domains = set()
    seen_names = set()
    master_path = MASTER_OUTPUT

    try:
        for loc_idx, location in enumerate(LOCATIONS):
            if loc_idx < start_loc_idx:
                continue
                
            for kw_idx, keyword in enumerate(keywords):
                if loc_idx == start_loc_idx and kw_idx < start_kw_idx:
                    continue
                
                # Calculate current percentages
                current_search_num = loc_idx * len(keywords) + kw_idx + 1
                overall_pct = (current_search_num / total_searches_in_category) * 100 if total_searches_in_category > 0 else 0
                loc_pct = ((kw_idx + 1) / len(keywords)) * 100 if len(keywords) > 0 else 0
                
                # Print current position
                print(f"\n{'='*65}")
                print(f"📂 Category {category_index+1}/{TOTAL_CATEGORIES}")
                print(f"🔍 [{loc_idx+1}/{len(LOCATIONS)}] [{kw_idx+1}/{len(keywords)}] '{keyword}' in '{location}'")
                print(f"   📍 Location: {loc_pct:.1f}% | Category: {overall_pct:.1f}%")
                print(f"{'='*65}")
                
                try:
                    leads = scraper.search(keyword, location)
                except CaptchaDetected:
                    print("\n🚨 Captcha encountered. Saving progress and aborting...")
                    save_progress(category_index, loc_idx, kw_idx, location, keyword, 
                                  completed_searches, batch_number, total_leads, search_completed=False)
                    print(f"💾 Progress saved. Will retry: '{keyword}' in '{location}'")
                    return
                except Exception as exc:
                    print(f"❌ Error during search: {exc}")
                    save_progress(category_index, loc_idx, kw_idx, location, keyword,
                                  completed_searches, batch_number, total_leads, search_completed=False)
                    continue

                # Process leads
                for lead in dedupe_leads(leads, seen_domains, seen_names):
                    validation_status = get_website_status(lead.website)
                    validated_lead = Lead(
                        name=lead.name,
                        website=lead.website,
                        location=lead.location,
                        keyword=lead.keyword,
                        place_url=lead.place_url,
                        status=validation_status
                    )
                    
                    batch.append(validated_lead)
                    total_leads += 1
                    
                    if len(batch) == BATCH_SIZE:
                        batch_number += 1
                        export_current_batch(batch, batch_number, master_path)
                        save_progress(category_index, loc_idx, kw_idx, location, keyword,
                                      completed_searches, batch_number, total_leads)
                        if not prompt_to_continue(batch_number):
                            print("\n⏸️ Stopping as requested. Progress saved.")
                            return
                        batch = []

                # Mark search as completed
                completed_searches += 1
                save_progress(category_index, loc_idx, kw_idx, location, keyword,
                              completed_searches, batch_number, total_leads)
                
                pct_done = (completed_searches / total_searches_in_category) * 100 if total_searches_in_category > 0 else 0
                print(f"✅ Completed {completed_searches}/{total_searches_in_category} ({pct_done:.1f}%) | Leads: {total_leads}")

        # Export remaining leads
        if batch:
            batch_number += 1
            export_current_batch(batch, batch_number, master_path)

        # Category complete!
        save_progress(category_index, len(LOCATIONS)-1, len(keywords)-1, LOCATIONS[-1], keywords[-1],
                      completed_searches, batch_number, total_leads, search_completed=True, category_complete=True)
        
        print(f"\n🎉 Category {category_index + 1}/{TOTAL_CATEGORIES} complete! Leads: {total_leads}")
        if category_index + 1 < TOTAL_CATEGORIES:
            print(f"   Use --next-category to advance to category {category_index + 2}.")
        else:
            print(f"   All categories completed!")
        
    finally:
        scraper.stop()


def export_current_batch(batch: List[Lead], batch_number: int, master_path: Path):
    destination = OUTPUT_DIR / f"leads_batch_{batch_number:03d}.csv"
    export_batch(batch, destination)
    append_to_master(master_path, batch)
    send_batch_to_webhook(batch_number, batch)
    summarise_batch(batch_number, batch)
    print(f"Saved batch {batch_number} → {destination}")
    print(f"Updated consolidated dataset → {master_path}")


if __name__ == "__main__":
    headless_arg = "--headless" in sys.argv
    fresh_arg = "--fresh" in sys.argv
    next_cat_arg = "--next-category" in sys.argv
    run(headless=headless_arg, fresh_start=fresh_arg, next_category=next_cat_arg)
