#!/usr/bin/env python3
"""Convenient launcher/shutdown CLI for the dashboard server."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.server_control import dashboard_status, restart_dashboard, start_dashboard, stop_dashboard


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the FixMyPresence dashboard server.")
    sub = parser.add_subparsers(dest="command", required=True)

    start_p = sub.add_parser("start", help="Start the dashboard server")
    start_p.add_argument("--reload", action="store_true", help="Run dashboard with uvicorn reload")

    stop_p = sub.add_parser("stop", help="Stop the dashboard server")
    stop_p.add_argument("--force", action="store_true", help="Use hard kill if needed")
    stop_p.add_argument("--delay", type=float, default=0.0, help="Delay shutdown by N seconds")

    restart_p = sub.add_parser("restart", help="Restart the dashboard server")
    restart_p.add_argument("--reload", action="store_true", help="Run dashboard with uvicorn reload after restart")
    restart_p.add_argument("--delay", type=float, default=0.0, help="Delay restart by N seconds")

    sub.add_parser("status", help="Show dashboard server status")

    args = parser.parse_args()

    if args.command == "start":
        result = start_dashboard(reload=args.reload)
    elif args.command == "stop":
        result = stop_dashboard(force=args.force, delay_seconds=args.delay)
    elif args.command == "restart":
        result = restart_dashboard(reload=args.reload, delay_seconds=args.delay)
    else:
        result = dashboard_status()

    print(json.dumps(result, indent=2))
    return 0 if result.get("success", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
