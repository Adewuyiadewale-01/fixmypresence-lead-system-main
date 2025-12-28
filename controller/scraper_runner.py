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


def run_scraper(next_category: bool = False, headless: bool = False) -> Tuple[int, str]:
    """
    Run the maps scraper as a subprocess.
    
    Args:
        next_category: If True, pass --next-category flag
        headless: If True, run browser in headless mode
    
    Returns:
        (exit_code, status_message)
    """
    cmd = [sys.executable, str(SCRAPER_MAIN)]
    
    if next_category:
        cmd.append("--next-category")
    if headless:
        cmd.append("--headless")
    
    print(f"\n🚀 Starting scraper: {' '.join(cmd)}")
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
    if not SCRAPER_MAIN.exists():
        print(f"❌ Scraper not found at: {SCRAPER_MAIN}")
        return False
    return True


if __name__ == "__main__":
    if check_scraper_ready():
        code, msg = run_scraper()
        print(f"\nResult: {msg} (code: {code})")
