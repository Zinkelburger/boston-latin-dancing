#!/usr/bin/env python3
"""
Read what a link actually says: titles, descriptions, structured data.

Two things make this more than a wrapper around requests.get.

First, the user-agent decides whether you get an answer at all. Facebook and
Instagram serve a browser UA from a datacenter IP a blanket 400 or an
identical login wall whatever is behind the URL — but both still answer their
own og-scraper honestly, because that is what renders link previews. Asking as
`facebookexternalhit/1.1` turns the whole of Meta from unreadable into
readable, which is the difference between "needs a human with a browser" and
a checked date.

Second, Facebook event pages carry no JSON-LD at all, but their og:description
is a fixed sentence with the facts in it:

    Event in Cambridge, MA by Liz Lister on Saturday, August 15 2026
    Dance event in Boston, MA by Clara y Al and 4 others on Saturday, May 23 2026

so date, city and organizer parse straight out of the preview text. That is
the only machine-readable date a Facebook event will give us.

Usage:
    python3 scripts/link_meta.py URL          # what does this link say?
    python3 scripts/link_meta.py URL --json   # same, as JSON
"""

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from html import unescape
from zoneinfo import ZoneInfo
from typing import Optional
from urllib.parse import urlparse

import requests

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# Meta's own link-preview scraper. Facebook and Instagram answer it with real
# metadata; they stonewall everything else.
META_UA = "facebookexternalhit/1.1"

META_HOSTS = ("facebook.com", "fb.com", "instagram.com")

TIMEOUT = 20
RETRIES = 3

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S
)
_CANONICAL_RE = re.compile(
    r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', re.I
)


def _meta_re(prop: str) -> re.Pattern:
    return re.compile(
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)',
        re.I,
    )


_OG_TITLE_RE = _meta_re("og:title")
_OG_DESC_RE = _meta_re("og:description")
_DESC_RE = _meta_re("description")


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_meta_host(url: str) -> bool:
    host = host_of(url)
    return any(h in host for h in META_HOSTS)


def ua_for(url: str) -> str:
    return META_UA if is_meta_host(url) else BROWSER_UA


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _first(pattern: re.Pattern, html: str) -> str:
    m = pattern.search(html)
    return _clean(m.group(1)) if m else ""


def jsonld_events(html: str) -> list[dict]:
    """Every schema.org Event object embedded in the page."""
    found = []
    for m in _JSONLD_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            for node in (graph if isinstance(graph, list) else [item]):
                if isinstance(node, dict) and node.get("@type") in (
                    "Event", "SocialEvent", "DanceEvent", "MusicEvent",
                ):
                    found.append(node)
    return found


def jsonld_location(event_ld: dict) -> Optional[str]:
    """Flatten a schema.org Event's location into one comparable string."""
    loc = event_ld.get("location")
    if isinstance(loc, str):
        return loc.strip() or None
    if not isinstance(loc, dict):
        return None

    name = (loc.get("name") or "").strip()
    addr = loc.get("address")
    if isinstance(addr, dict):
        parts = [addr.get(k, "") for k in
                 ("streetAddress", "addressLocality", "addressRegion", "postalCode")]
        addr = ", ".join(p.strip() for p in parts if p and p.strip())
    addr = (addr or "").strip()

    joined = ", ".join(p for p in (name, addr) if p)
    return joined or None


def jsonld_start(event_ld: dict) -> Optional[str]:
    start = event_ld.get("startDate")
    return start if isinstance(start, str) and start else None


# A page that stamps "now" into startDate will sit this close to the clock.
# Real events are scheduled in advance and essentially never begin within a
# few minutes of the moment we happen to fetch the page.
_RENDER_TIMESTAMP_WINDOW_S = 15 * 60

# The sites we read are Boston ones, so a timestamp with no offset is Boston's.
LOCAL_TZ = ZoneInfo("America/New_York")


def looks_like_render_timestamp(iso_str: Optional[str], now: Optional[datetime] = None) -> bool:
    """Whether a JSON-LD date is really the page's own render clock.

    boston.gov emits `"startDate": "2026-08-13T15:36:39"` and the seconds
    advance between fetches — it is stamping the current time, not the event's.
    Believing it is worse than ignoring it: the review process treats a date
    mismatch as "the source wins", so this would quietly rewrite a correct
    date to today's and send people to a concert that already happened.

    Such a stamp is naive and written in the site's own wall clock, so a naive
    value is read as Boston time. Guessing across every possible offset instead
    would catch tonight's genuine 8pm event whenever we happened to run near
    the hour, which is the false positive that matters here.
    """
    if not iso_str:
        return False
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)

    return abs((dt - now).total_seconds()) <= _RENDER_TIMESTAMP_WINDOW_S


def extract(html: str) -> dict:
    """Pull every readable descriptor out of a page body."""
    return {
        "title": _first(_TITLE_RE, html),
        "og_title": _first(_OG_TITLE_RE, html),
        "og_description": _first(_OG_DESC_RE, html) or _first(_DESC_RE, html),
        "canonical": _first(_CANONICAL_RE, html),
        "jsonld_events": jsonld_events(html),
    }


def fetch(url: str, timeout: int = TIMEOUT, retries: int = RETRIES) -> dict:
    """Fetch a URL as whichever client its host will actually talk to."""
    headers = {"User-Agent": ua_for(url)}
    status, html, final, error = None, "", None, None

    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            status, final, html, error = resp.status_code, resp.url, resp.text, None
            break
        except requests.RequestException as exc:
            error = type(exc).__name__
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))

    return {"url": url, "status": status, "final_url": final, "html": html, "error": error}


def link_meta(url: str) -> dict:
    """Everything we can read from a URL, without the page body."""
    fetched = fetch(url)
    meta = extract(fetched["html"]) if fetched["html"] else {
        "title": "", "og_title": "", "og_description": "", "canonical": "",
        "jsonld_events": [],
    }
    meta.update({
        "url": url,
        "status": fetched["status"],
        "final_url": fetched["final_url"],
        "error": fetched["error"],
    })
    if is_meta_host(url):
        meta["facebook_event"] = facebook_event_details(meta["og_description"])
    return meta


# ── Facebook's preview sentence ───────────────────────────────────────

_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

# "Event in Cambridge, MA by Liz Lister on Saturday, August 15 2026"
# "Dance event in Boston, MA by Clara y Al and 4 others on Saturday, May 23 2026"
# Note the case: the word is capitalised when it opens the sentence and lower
# when "Dance" precedes it. The year runs straight into whatever follows it
# ("…May 23 20265 posts in the discussion"), so the year is matched as exactly
# four digits and nothing is anchored to the end of the string.
_FB_EVENT_RE = re.compile(
    r"(?:Dance\s+)?[Ee]vent\s+"
    r"(?:in\s+(?P<location>.+?)\s+)?"
    r"by\s+(?P<organizer>.+?)\s+"
    r"on\s+(?P<weekday>\w+day),\s+(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2})\s+(?P<year>\d{4})",
    re.S,
)


def facebook_event_details(og_description: str) -> Optional[dict]:
    """Parse date, city and organizer out of a Facebook event preview.

    Facebook event pages ship no JSON-LD, so this sentence is the only
    structured thing they expose. Returns None when the text is not a Facebook
    event preview — a profile or a photo post never matches.
    """
    if not og_description:
        return None
    m = _FB_EVENT_RE.search(og_description)
    if not m:
        return None

    month = _MONTHS.get(m.group("month"))
    if not month:
        return None
    try:
        when = date(int(m.group("year")), month, int(m.group("day")))
    except ValueError:
        return None

    return {
        "date": when.isoformat(),
        "weekday": m.group("weekday"),
        "location": (m.group("location") or "").strip() or None,
        "organizer": m.group("organizer").strip(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Show what a link actually says.")
    ap.add_argument("url")
    ap.add_argument("--json", action="store_true", help="emit the full record as JSON")
    args = ap.parse_args()

    meta = link_meta(args.url)

    if args.json:
        print(json.dumps(meta, indent=2))
        return 0 if meta["status"] and meta["status"] < 400 else 1

    print(f"url        {meta['url']}")
    print(f"status     {meta['status']}{'  (' + meta['error'] + ')' if meta['error'] else ''}")
    print(f"fetched as {ua_for(args.url)}")
    if meta["final_url"] and meta["final_url"] != meta["url"]:
        print(f"redirects  {meta['final_url']}")
    for label, key in (("title", "og_title"), ("", "title"), ("describes", "og_description"),
                       ("canonical", "canonical")):
        if meta.get(key):
            print(f"{label or '(html)':10} {meta[key][:300]}")

    fb = meta.get("facebook_event")
    if fb:
        print("\nfacebook event says:")
        print(f"  date       {fb['date']} ({fb['weekday']})")
        print(f"  location   {fb['location']}")
        print(f"  organizer  {fb['organizer']}")

    for ld in meta["jsonld_events"]:
        print("\njson-ld event:")
        for key in ("name", "startDate", "endDate", "eventStatus"):
            if ld.get(key):
                print(f"  {key:10} {ld[key]}")

    if not any(meta[k] for k in ("og_title", "title", "og_description")):
        print("\n(no readable metadata — deleted, or the host is stonewalling us)")
    return 0 if meta["status"] and meta["status"] < 400 else 1


if __name__ == "__main__":
    sys.exit(main())
