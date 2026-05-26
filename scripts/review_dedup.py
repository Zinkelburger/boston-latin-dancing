#!/usr/bin/env python3
"""
Review dedup candidates — JSON-friendly wrapper around dedup_report.

Usage:
    python3 scripts/review_dedup.py              # human-readable report
    python3 scripts/review_dedup.py --json       # JSON output for agents/tools
    python3 scripts/review_dedup.py --active     # scan active store instead of published
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dedup_report import load_events, print_report, report
from event_store import ACTIVE_JSON, PUBLIC_EVENTS_JSON


def main():
    use_active = "--active" in sys.argv
    as_json = "--json" in sys.argv

    path = ACTIVE_JSON if use_active else PUBLIC_EVENTS_JSON
    label = "active" if use_active else "published"

    if not as_json:
        print(f"Scanning {label} events from {path}...")

    events = load_events(path)
    pairs = report(events)

    if as_json:
        print(json.dumps({"source": label, "path": str(path), "count": len(events), "pairs": pairs}, indent=2))
    else:
        print(f"Loaded {len(events)} events")
        print_report(pairs)


if __name__ == "__main__":
    main()
