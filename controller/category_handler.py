"""Category transition handler for the master controller.

Handles prompting for category advancement with timeout-based auto-continue.
"""

from __future__ import annotations

import select
import sys
import time
from typing import Optional

# Default timeout for category transition prompt (30 minutes)
DEFAULT_TIMEOUT_MINUTES = 30


def prompt_category_advance(timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES) -> bool:
    """
    Prompt user whether to advance to the next category.
    
    Auto-advances after timeout (default 30 minutes).
    
    Args:
        timeout_minutes: Minutes to wait before auto-advancing
    
    Returns:
        True to advance to next category, False to stop
    """
    timeout_seconds = timeout_minutes * 60
    
    print("\n" + "=" * 60)
    print("📂 CATEGORY COMPLETE")
    print("=" * 60)
    print(f"\nAdvance to next category?")
    print(f"  [Y] Yes, continue to next category")
    print(f"  [N] No, stop here")
    print(f"\n⏱️  Auto-advancing in {timeout_minutes} minutes if no response...")
    print("\nYour choice [Y/n]: ", end="", flush=True)
    
    start_time = time.time()
    
    # Try to read input with timeout
    try:
        # Check if we're in an interactive terminal
        if sys.stdin.isatty():
            # Use select for timeout on Unix-like systems
            if hasattr(select, 'select'):
                while True:
                    elapsed = time.time() - start_time
                    remaining = timeout_seconds - elapsed
                    
                    if remaining <= 0:
                        print("\n\n⏱️ Timeout reached. Auto-advancing to next category...")
                        return True
                    
                    # Check for input every second
                    ready, _, _ = select.select([sys.stdin], [], [], min(1.0, remaining))
                    
                    if ready:
                        response = sys.stdin.readline().strip().lower()
                        if response in {"n", "no"}:
                            print("⏹️ Stopping as requested.")
                            return False
                        else:
                            print("✅ Advancing to next category...")
                            return True
                    
                    # Print countdown every minute
                    if int(elapsed) % 60 == 0 and elapsed > 0:
                        mins_left = int(remaining / 60)
                        if mins_left > 0:
                            print(f"\r⏱️ {mins_left} minutes remaining... [Y/n]: ", end="", flush=True)
            else:
                # Fallback for systems without select (Windows)
                response = input().strip().lower()
                if response in {"n", "no"}:
                    return False
                return True
        else:
            # Non-interactive mode - auto-advance immediately
            print("\n(Non-interactive mode, auto-advancing)")
            return True
            
    except EOFError:
        # No input available
        print("\n\n⏱️ No input, auto-advancing to next category...")
        return True
    except KeyboardInterrupt:
        print("\n\n⏸️ Interrupted. Stopping.")
        return False


if __name__ == "__main__":
    # Test with shorter timeout
    result = prompt_category_advance(timeout_minutes=1)
    print(f"Result: {'Advance' if result else 'Stop'}")
