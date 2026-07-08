#!/usr/bin/env python3
"""Deterministic refresh: scrape → ingest → archive → publish.

The no-judgment half of the pipeline. Existing events get refreshed
(certain-confidence merges only) and past events get archived, but
brand-new events are QUARANTINED into pending.json — nothing appears on
the map until the weekly agent run (automation/agent_review.sh) approves it.

Usage: python3 scripts/run_pipeline.py [--skip-scrape]

Exit codes:
  0  ok (summary JSON on stdout)
  1  hard failure
  2  tripwire hit — published files restored from pre-run snapshot;
     the caller must NOT commit.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from event_store import (  # noqa: E402
    PUBLIC_EVENTS_JSON,
    archive_past_events,
    ingest_scraped,
    load_pending,
    load_rejected,
    publish,
)

LEGACY_PUBLIC_JSON = ROOT / "public" / "events.json"

# Mirrors the runnable map in mcp-server/server.py. Facebook sources need a
# browser and are agent-only, so they are deliberately absent here.
SCRAPERS = {
    "beatrice-calendar": ["scrape_ics.py"],
    "sensualeros-boston": ["scrape_ics.py", "sensualeros-boston"],
    "unabulla-cuban-boston": ["scrape_ics.py", "unabulla-cuban-boston"],
    "lister-events": ["scrape_lister.py"],
    "eventbrite-boston-latin": ["scrape_eventbrite.py"],
    "fiesta-dance-company": ["scrape_fiesta_dance.py"],
    "submissions": ["fetch_submissions.py"],
}

# Refuse to ship a published file whose live-event count collapsed relative
# to the previous run — a broken scrape must never wipe the site.
TRIPWIRE_MIN_PREVIOUS = 20
TRIPWIRE_MIN_RATIO = 0.7


def run_scrapers() -> dict:
    results = {}
    for sid, cmd in SCRAPERS.items():
        script = ROOT / "scripts" / cmd[0]
        try:
            proc = subprocess.run(
                [sys.executable, str(script), *cmd[1:]],
                capture_output=True, text=True, timeout=180, cwd=str(ROOT),
            )
            results[sid] = {
                "ok": proc.returncode == 0,
                "error": "" if proc.returncode == 0 else proc.stderr.strip()[-300:],
            }
        except subprocess.TimeoutExpired:
            results[sid] = {"ok": False, "error": "timeout (180s)"}
        except Exception as exc:
            results[sid] = {"ok": False, "error": str(exc)}
    return results


def live_count(text: str | None) -> int:
    if not text:
        return 0
    try:
        return sum(1 for e in json.loads(text) if not e.get("archived"))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-scrape", action="store_true",
                        help="ingest/archive/publish only, without re-running scrapers")
    args = parser.parse_args()

    snapshot = PUBLIC_EVENTS_JSON.read_text() if PUBLIC_EVENTS_JSON.exists() else None
    previous_live = live_count(snapshot)

    scrape_results = {} if args.skip_scrape else run_scrapers()
    ingest_result = ingest_scraped(quarantine_new=True)
    archived = archive_past_events()
    publish_result = publish()

    new_live = live_count(PUBLIC_EVENTS_JSON.read_text())
    tripped = (
        previous_live >= TRIPWIRE_MIN_PREVIOUS
        and new_live < previous_live * TRIPWIRE_MIN_RATIO
    )
    if tripped and snapshot is not None:
        PUBLIC_EVENTS_JSON.write_text(snapshot)
        LEGACY_PUBLIC_JSON.write_text(snapshot)

    summary = {
        "status": "TRIPWIRE" if tripped else "ok",
        "scrapers_failed": {k: v["error"] for k, v in scrape_results.items() if not v["ok"]},
        "ingest": {k: ingest_result.get(k) for k in
                   ("quarantined_new", "skipped_duplicates", "reactivated",
                    "rejected_non_latin", "pending_review")},
        "archived": len(archived),
        "published_live_events": new_live,
        "previous_live_events": previous_live,
        "needs_agent_review": {
            "rejected_queue": len(load_rejected()),
            "pending_queue": len(load_pending()),
        },
    }
    print(json.dumps(summary, indent=2, default=str))

    if tripped:
        print(
            f"TRIPWIRE: live events fell {previous_live} → {new_live}; "
            "published files restored, do not commit.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
