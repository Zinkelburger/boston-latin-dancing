#!/usr/bin/env python3
"""Scrape DanceFam (dancefam.org) socials in Massachusetts.

DanceFam is an Angular app over a public JSON backend. The search endpoint
lists upcoming socials by state; it carries the name, UTC start, venue name,
price and genre names but no address or end time, so each hit is followed by
one detail call that has the street address, coordinates, end and organizer::

    GET https://backend.dancefam.org/public/search/events?state=Massachusetts&PageSize=50&PageCount=0
    GET https://backend.dancefam.org/public/event?EventId=2650

Times come back as UTC ("2026-10-11T02:00:00Z" is Sat Oct 10, 10 PM in
Boston) and are converted to Eastern before they are stored, so nothing that
reads the date part of startDate sees the next day.

Scrape health: an empty search is the normal state between seasons on a
platform this small, but the endpoint answering with something other than a
list means the API changed, which raises.
"""

from __future__ import annotations

import html as html_lib
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

SOURCE_ID = "dancefam"
API = "https://backend.dancefam.org/public"
EVENT_PAGE = "https://dancefam.org/event/{id}"
STATE = "Massachusetts"
PAGE_SIZE = 50


def _utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(NY_TZ)
    except ValueError:
        return None


def _text(fragment: str | None) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", fragment or "", flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def _address(detail: dict, venue: str) -> str:
    a = detail.get("address") or {}
    street = " ".join(p for p in (a.get("building"), a.get("street")) if p)
    state = "MA" if (a.get("state") or "").lower() == "massachusetts" else a.get("state")
    town = ", ".join(p for p in (a.get("city"), " ".join(p for p in (state, a.get("zip_code")) if p)) if p)
    # Venues without a name are listed by their street ("105 Water St").
    if venue and a.get("building") and venue.startswith(str(a["building"])):
        venue = ""
    return ", ".join(p for p in (venue, street, town) if p)


def search(state: str = STATE) -> list[dict]:
    hits: list[dict] = []
    page = 0
    while True:
        rows = fetch(f"{API}/search/events", params={
            "state": state, "PageSize": PAGE_SIZE, "PageCount": page}).json()
        if not isinstance(rows, list):
            raise RuntimeError(f"DanceFam search returned {type(rows).__name__}, not a list — API changed?")
        hits.extend(rows)
        if len(rows) < PAGE_SIZE:
            return hits
        page += 1
        time.sleep(1)


def build_event(hit: dict, detail: dict) -> dict | None:
    start = _utc(detail.get("start") or hit.get("start"))
    if start is None:
        return None
    end = _utc(detail.get("end")) or start
    name = (detail.get("name") or hit.get("name") or "").strip()
    venue = (detail.get("location") or hit.get("location") or "").strip()
    genres = [g.get("name", "").strip() for g in hit.get("genres") or []]
    organizer = (detail.get("socialName") or "").strip()
    description = _text(detail.get("description") or hit.get("shortBio"))
    if organizer:
        description = f"Hosted by {organizer}. {description}".strip()
    styles = detect_styles(f"{name} {' '.join(genres)} {description}")
    price = detail.get("minPrice", hit.get("price"))
    a = detail.get("address") or {}
    return make_event(
        id=f"dancefam-{hit['id']}",
        name=name,
        start=start,
        end=end,
        location=_address(detail, venue),
        lat=a.get("latitude"),
        lng=a.get("longitude"),
        description=description[:1500],
        url=EVENT_PAGE.format(id=hit["id"]),
        styles=styles,
        cost=f"${price:g}" if isinstance(price, (int, float)) and price > 0 else None,
        source=SOURCE_ID,
    )


def fetch_source(source: dict) -> ScrapeResult:
    hits = search(source.get("state", STATE))
    print(f"[{source['id']}] {len(hits)} upcoming DanceFam events in {source.get('state', STATE)}")
    events = []
    for hit in hits:
        try:
            detail = fetch(f"{API}/event", params={"EventId": hit["id"]}).json()
        except Exception as exc:
            print(f"  detail {hit.get('id')} unavailable ({exc}); using the search row", file=sys.stderr)
            detail = {}
        ev = build_event(hit, detail if isinstance(detail, dict) else {})
        if ev:
            events.append(ev)
            print(f"  [keep] {ev['startDate'][:16]}  {ev['name'][:50]}  {ev['location'][:50]}")
        time.sleep(1)
    if not hits:
        return ScrapeResult([], raw_found=0, skipped=True,
                            note="DanceFam search answered with no upcoming events in the state")
    return ScrapeResult(events, raw_found=len(hits))


def main(argv: list[str] | None = None) -> int:
    args = scraper_argparser(__doc__, default_source_id=SOURCE_ID).parse_args(argv)
    return run_scraper(args.source_id, fetch_source)


if __name__ == "__main__":
    sys.exit(main())
