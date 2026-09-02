#!/usr/bin/env python3
"""Deterministic refresh: scrape → ingest → archive → publish.

The no-judgment half of the pipeline. Existing events get refreshed
(certain-confidence merges only) and past events get archived, but
brand-new events are QUARANTINED into pending.json — nothing appears on
the map until the weekly agent run (automation/agent_review.sh) approves it.

Which scrapers run is decided by data/sources.json alone (every enabled
entry with a ``scraper`` field, plus the submissions fetcher) via
scraper_utils.scraper_commands(); there is no second list to keep in sync.

Usage:
  python3 scripts/run_pipeline.py                 # scrape, ingest, archive, publish
  python3 scripts/run_pipeline.py --skip-scrape   # ingest/archive/publish only
  python3 scripts/run_pipeline.py --scrape-only   # every scraper, no ingest/publish
  python3 scripts/run_pipeline.py --scrape-only --only lous-live

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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from event_store import (  # noqa: E402
    PUBLIC_EVENTS_JSON,
    archive_past_events,
    ingest_scraped,
    load_pending,
    load_rejected,
    publish_guarded,
)
from scraper_utils import load_scrape_health, scraper_commands  # noqa: E402

STDERR_TAIL_CHARS = 600


def run_scrapers(only: str | None = None, timeout: int = 180) -> list[dict]:
    """Run every registered scraper sequentially, capturing output.

    Returns one dict per source, in registry order:
    ``{"source_id", "ok", "returncode", "seconds", "stderr_tail"}``. A
    timeout or a failure to launch is reported as ``ok: False`` with a
    synthetic return code; nothing here raises, so one broken scraper never
    stops the others.
    """
    results: list[dict] = []
    for sid, argv in scraper_commands(only=only):
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT),
            )
            returncode, stderr = proc.returncode, proc.stderr
        except subprocess.TimeoutExpired as exc:
            returncode = -1
            partial = exc.stderr or ""
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", "replace")
            stderr = f"{partial}\ntimeout after {timeout}s"
        except OSError as exc:
            returncode = -1
            stderr = f"could not launch scraper: {exc}"
        seconds = round(time.monotonic() - started, 1)
        results.append({
            "source_id": sid,
            "ok": returncode == 0,
            "returncode": returncode,
            "seconds": seconds,
            "stderr_tail": stderr.strip()[-STDERR_TAIL_CHARS:],
        })
        if returncode != 0:
            print(f"SCRAPER FAILED: {sid} (exit {returncode}, {seconds}s)\n"
                  f"{results[-1]['stderr_tail']}", file=sys.stderr)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-scrape", action="store_true",
                        help="ingest/archive/publish only, without re-running scrapers")
    parser.add_argument("--scrape-only", action="store_true",
                        help="run the scrapers and stop; no ingest, archive, or publish")
    parser.add_argument("--only", metavar="SOURCE_ID",
                        help="run a single scraper (source id from data/sources.json)")
    parser.add_argument("--timeout", type=int, default=180,
                        help="per-scraper timeout in seconds (default 180)")
    args = parser.parse_args()
    if args.skip_scrape and (args.scrape_only or args.only):
        parser.error("--skip-scrape cannot be combined with --scrape-only/--only")

    # Baseline snapshot taken before scrape/ingest/archive: a broken scrape that
    # empties the store must be measured against the last good published file.
    snapshot = (PUBLIC_EVENTS_JSON.read_text(encoding="utf-8")
                if PUBLIC_EVENTS_JSON.exists() else None)

    scrape_results = [] if args.skip_scrape else run_scrapers(only=args.only, timeout=args.timeout)
    scrapers_failed = {r["source_id"]: r["stderr_tail"] for r in scrape_results if not r["ok"]}

    # Scrapers that reached their page but parsed nothing structurally: the page
    # markup likely changed and the scraper needs a redesign. Only meaningful
    # for scrapers that actually ran this invocation.
    scrapers_suspect = {}
    if scrape_results:
        health = load_scrape_health()
        for r in scrape_results:
            h = health.get(r["source_id"], {})
            if h.get("status") == "structure_missing":
                scrapers_suspect[r["source_id"]] = h.get("note", "parser matched nothing — redesign needed")

    if args.scrape_only:
        summary = {
            "status": "ok",
            "scrapers": scrape_results,
            "scrapers_failed": scrapers_failed,
            "scrapers_need_redesign": scrapers_suspect,
        }
        print(json.dumps(summary, indent=2, default=str))
        _alert_suspect(scrapers_suspect)
        return 0

    ingest_result = ingest_scraped(quarantine_new=True)
    archived = archive_past_events()

    publish_result = publish_guarded(previous_snapshot=snapshot)
    tripped = publish_result["tripped"]
    previous_live = publish_result["previous_live_events"]
    new_live = publish_result["published_live_events"]

    summary = {
        "status": "TRIPWIRE" if tripped else "ok",
        "scrapers": scrape_results,
        "scrapers_failed": scrapers_failed,
        "scrapers_need_redesign": scrapers_suspect,
        "ingest": {k: ingest_result.get(k) for k in
                   ("quarantined_new", "skipped_duplicates", "reactivated",
                    "dropped_non_latin", "pending_review")},
        "archived": len(archived),
        "published_live_events": new_live,
        "previous_live_events": previous_live,
        "needs_agent_review": {
            "rejected_queue": len(load_rejected()),
            "pending_queue": len(load_pending()),
        },
    }
    print(json.dumps(summary, indent=2, default=str))
    _alert_suspect(scrapers_suspect)

    if tripped:
        # Loud and non-zero: refresh.sh runs under `set -e`, so this exit is
        # what keeps the restored files from being committed and pushed.
        print(
            "\n" + "=" * 72 + "\n"
            f"TRIPWIRE: live events fell {previous_live} → {new_live}; "
            "published files restored from the pre-run snapshot.\n"
            "DO NOT COMMIT. Investigate the scrape before re-running.\n"
            + "=" * 72,
            file=sys.stderr,
        )
        return 2
    return 0


def _alert_suspect(scrapers_suspect: dict) -> None:
    if scrapers_suspect:
        print(
            "SCRAPER ALERT: parsed nothing structurally (page markup likely "
            f"changed, redesign needed): {', '.join(scrapers_suspect)}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    sys.exit(main())
