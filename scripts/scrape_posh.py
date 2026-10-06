#!/usr/bin/env python3
"""Scrape Latin dance events in Massachusetts from Posh (posh.vip).

Posh has no city calendar we can read: its explore feed is nightlife, organizer
pages need a login, and event pages only carry their JSON-LD inside Next.js
flight data. The site's own search does work logged out, though, and returns
everything we need — local timezone, start/end, venue and street address::

    GET https://posh.vip/api/web/v2/trpc/search.searchForEvents
        ?input={"searchQuery":"bachata boston","showPreviousEvents":false,"limit":50}

Search is national and caps out after a few pages, and recurring series from
other cities crowd the plain style words, so each query in the source's
``queries`` list names a town too. Hits are kept when the address is in
Massachusetts and the name or description names a Latin style; the run-wide
Latin filter at ingest still applies after that.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import (
    NY_TZ,
    ScrapeResult,
    detect_styles,
    fetch,
    make_event,
    run_scraper,
    scraper_argparser,
)

SOURCE_ID = "posh"
SEARCH = "https://posh.vip/api/web/v2/trpc/search.searchForEvents"
EVENT_PAGE = "https://posh.vip/e/{slug}"
DEFAULT_QUERIES = ["bachata boston", "salsa boston", "salsa cambridge", "bachata cambridge",
                   "latin boston", "kizomba", "zouk boston", "tardeo"]
MAX_PAGES = 3
MA_ADDRESS_RE = re.compile(r",\s*MA\s+0[12]\d{3}\b")
PRICE_RE = re.compile(r"\$\s?\d+(?:\.\d{2})?")
# Posh lists promoter "weekly lineup" posts as events; they are guest-list ads.
NOT_AN_EVENT_RE = re.compile(r"\bweekly lineup\b|\bguest ?lists?\b|\bbar crawl\b", re.I)


def search(query: str) -> list[dict]:
    hits: list[dict] = []
    cursor = None
    for _ in range(MAX_PAGES):
        payload = {"searchQuery": query, "showPreviousEvents": False, "limit": 50}
        if cursor is not None:
            payload["cursor"] = cursor
        body = fetch(SEARCH, browser=True, params={"input": json.dumps(payload)}).json()
        try:
            data = body["result"]["data"]
            rows = data["events"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(f"Posh search answered without result.data.events — API changed? {exc}")
        hits.extend(rows)
        cursor = data.get("nextCursor")
        if not rows or cursor is None:
            break
        time.sleep(1)
    return hits


def _local(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(NY_TZ)
    except ValueError:
        return None


def build_event(hit: dict) -> dict | None:
    address = (hit.get("venueAddress") or "").strip()
    name = (hit.get("name") or "").strip()
    if not MA_ADDRESS_RE.search(address) or not name or NOT_AN_EVENT_RE.search(name):
        return None
    description = (hit.get("description") or hit.get("shortDescription") or "").strip()
    styles = detect_styles(f"{name} {hit.get('shortDescription') or ''} {description}")
    if styles == ["other"]:
        return None
    start = _local(hit.get("startUtc"))
    if start is None:
        return None
    venue = (hit.get("venueName") or "").strip()
    address = re.sub(r",\s*USA$", "", address)
    location = address if not venue or address.startswith(venue) else f"{venue}, {address}"
    price = PRICE_RE.search(hit.get("startingTicketPriceCTA") or "")
    return make_event(
        id=f"posh-{hit['url']}",
        name=name,
        start=start,
        end=_local(hit.get("endUtc")) or start,
        location=location,
        description=re.sub(r"[#*]+", "", description)[:1500],
        url=EVENT_PAGE.format(slug=hit["url"]),
        styles=styles,
        cost=f"From {price.group(0)}" if price else None,
        source=SOURCE_ID,
    )


def fetch_source(source: dict) -> ScrapeResult:
    seen: dict[str, dict] = {}
    raw = 0
    for query in source.get("queries") or DEFAULT_QUERIES:
        hits = search(query)
        raw += len(hits)
        print(f"[{source['id']}] '{query}': {len(hits)} hits")
        for hit in hits:
            if hit.get("url") and hit["url"] not in seen:
                seen[hit["url"]] = hit
        time.sleep(1)
    events = []
    for hit in seen.values():
        ev = build_event(hit)
        if ev:
            events.append(ev)
            print(f"  [keep] {ev['startDate'][:16]}  {ev['name'][:50]}  {ev['location'][:45]}")
    return ScrapeResult(events, raw_found=raw,
                        note="" if raw else "every Posh search came back empty — endpoint changed?")


def main(argv: list[str] | None = None) -> int:
    args = scraper_argparser(__doc__, default_source_id=SOURCE_ID).parse_args(argv)
    return run_scraper(args.source_id, fetch_source)


if __name__ == "__main__":
    sys.exit(main())
