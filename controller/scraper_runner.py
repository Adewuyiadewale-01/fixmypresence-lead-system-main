"""Scraper runner for the master controller.

Launches and monitors the maps scraper subprocess.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Tuple

# Paths
REPO_ROOT = Path(__file__).parent.parent
SCRAPER_DIR = REPO_ROOT / "Bots" / "maps_scraper"
SCRAPER_MAIN = SCRAPER_DIR / "main.py"
PARALLEL_SCRAPER = SCRAPER_DIR / "parallel_scraper.py"
DELIVERY_FAILED_EXIT_CODE = 75


def run_scraper(next_category: bool = False, headless: bool = False, parallel: bool = True) -> Tuple[int, str]:
    """
    Run the maps scraper as a subprocess.
    
    Args:
        next_category: If True, pass --next-category flag
        headless: If True, run browser in headless mode
        parallel: If True, use parallel_scraper.py (6 workers)
    
    Returns:
        (exit_code, status_message)
    """
    # Use parallel scraper by default
    script = PARALLEL_SCRAPER if parallel and PARALLEL_SCRAPER.exists() else SCRAPER_MAIN
    cmd = [sys.executable, str(script)]
    
    # For parallel scraper, always add --continuous for non-interactive mode
    if script == PARALLEL_SCRAPER:
        cmd.append("--continuous")
    
    if next_category:
        cmd.append("--next-category")
    if headless:
        cmd.append("--headless")
    
    mode = "parallel (6 workers)" if script == PARALLEL_SCRAPER else "single-threaded"
    print(f"\n🚀 Starting scraper ({mode}): {' '.join(cmd)}")
    print("-" * 50)
    
    try:
        # Run scraper interactively (inherits stdin/stdout)
        result = subprocess.run(
            cmd,
            cwd=str(SCRAPER_DIR),
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        
        exit_code = result.returncode
        
        if exit_code == 0:
            return (0, "Scraper exited normally")
        if exit_code == DELIVERY_FAILED_EXIT_CODE:
            return (exit_code, "Scraper paused after a delivery timeout. Pending leads were preserved for retry.")
        else:
            return (exit_code, f"Scraper exited with code {exit_code}")
            
    except KeyboardInterrupt:
        print("\n\n⏸️ Interrupted by user. Progress was saved by scraper.")
        return (-1, "Interrupted by user")
    except Exception as e:
        print(f"\n❌ Error running scraper: {e}")
        return (-2, f"Error: {e}")


def check_scraper_ready() -> bool:
    """Check if the scraper can be started."""
    if PARALLEL_SCRAPER.exists():
        return True
    if SCRAPER_MAIN.exists():
        return True
    print(f"❌ Scraper not found at: {SCRAPER_MAIN} or {PARALLEL_SCRAPER}")
    return False


if __name__ == "__main__":
    if check_scraper_ready():
        code, msg = run_scraper()
        print(f"\nResult: {msg} (code: {code})")
