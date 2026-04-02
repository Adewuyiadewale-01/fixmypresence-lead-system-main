"""Shared lock module for enrichment concurrency control.

Used by both enrichment_watcher.py and enrich_linkedin.py to prevent
duplicate enrichment runs and handle crash recovery.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

# Lock file location (relative to controller directory)
_CONTROLLER_DIR = Path(__file__).parent
LOCK_FILE = _CONTROLLER_DIR / "enrichment_lock.json"

# Default stale timeout (minutes)
DEFAULT_STALE_TIMEOUT_MINUTES = 90


def _now_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(iso_str: str) -> Optional[datetime]:
    """Parse ISO datetime string."""
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def is_locked() -> Tuple[bool, Optional[str], Optional[str]]:
    """Check current lock state.
    
    Returns:
        (is_locked, batch_id, locked_at) where:
        - is_locked: True if lock file exists and has locked=True
        - batch_id: The batch ID holding the lock (or None)
        - locked_at: ISO timestamp when lock was acquired (or None)
    """
    if not LOCK_FILE.exists():
        return (False, None, None)
    
    try:
        with open(LOCK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        if data.get("locked"):
            return (True, data.get("batch_id"), data.get("locked_at"))
        return (False, None, None)
    except (json.JSONDecodeError, IOError):
        return (False, None, None)


def is_stale(timeout_minutes: int = DEFAULT_STALE_TIMEOUT_MINUTES) -> bool:
    """Check if current lock is stale (older than timeout).
    
    A stale lock indicates the holder crashed without releasing.
    """
    locked, _, locked_at = is_locked()
    if not locked or not locked_at:
        return False
    
    lock_time = _parse_iso(locked_at)
    if not lock_time:
        return True  # Can't parse = treat as stale
    
    now = datetime.now(timezone.utc)
    age_minutes = (now - lock_time).total_seconds() / 60
    return age_minutes > timeout_minutes


def acquire_lock(batch_id: str, force_if_stale: bool = True) -> bool:
    """Attempt to acquire the enrichment lock.
    
    Args:
        batch_id: Unique identifier for this enrichment run
        force_if_stale: If True, replace stale locks automatically
        
    Returns:
        True if lock was acquired, False if already locked by another batch
    """
    locked, existing_batch, _ = is_locked()
    
    if locked:
        # Same batch already has lock - that's fine
        if existing_batch == batch_id:
            return True
        
        # Different batch has lock - check if stale
        if force_if_stale and is_stale():
            print(f"⚠️ Stale lock detected (batch {existing_batch}). Replacing with {batch_id}.")
        else:
            return False
    
    # Write lock file
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_data = {
        "locked": True,
        "batch_id": batch_id,
        "locked_at": _now_iso()
    }
    
    try:
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            json.dump(lock_data, f, indent=2)
        return True
    except IOError as e:
        print(f"❌ Failed to acquire lock: {e}")
        return False


def release_lock(batch_id: Optional[str] = None) -> bool:
    """Release the enrichment lock.
    
    Args:
        batch_id: If provided, only release if lock matches this batch.
                  If None, release unconditionally.
                  
    Returns:
        True if lock was released, False if lock didn't match batch_id
    """
    if not LOCK_FILE.exists():
        return True
    
    if batch_id:
        locked, existing_batch, _ = is_locked()
        if locked and existing_batch != batch_id:
            print(f"⚠️ Lock held by {existing_batch}, not {batch_id}. Not releasing.")
            return False
    
    try:
        os.remove(LOCK_FILE)
        return True
    except IOError:
        # Try to write unlocked state instead
        try:
            with open(LOCK_FILE, "w", encoding="utf-8") as f:
                json.dump({"locked": False}, f)
            return True
        except IOError:
            return False


def get_lock_info() -> dict:
    """Get full lock information for debugging/logging."""
    locked, batch_id, locked_at = is_locked()
    stale = is_stale() if locked else False
    
    return {
        "locked": locked,
        "batch_id": batch_id,
        "locked_at": locked_at,
        "is_stale": stale,
        "lock_file": str(LOCK_FILE)
    }
