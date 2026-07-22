#!/usr/bin/env python3
"""One scraper for every site that embeds schema.org JSON-LD.

Modern event platforms — Wix, ma.to, "The Events Calendar" (Tribe), and most
others — embed a schema.org ``Event`` object as ``<script type="application/ld+json">``
on their pages (Google requires it for event rich-results). That JSON-LD is the
*same shape everywhere*; only the URL patterns differ. So instead of a bespoke
200-line scraper per website, this one generic scraper handles them all, driven
entirely by config in ``data/sources.json``. Adding a JSON-LD site is a config
entry, not new Python.

Two page shapes, chosen by config:

  • detail-crawl (``link_pattern``): the listing page links to per-event pages,
    each carrying its own JSON-LD. We collect the links that match ``link_pattern``
    and read the JSON-LD from each detail page.
        Wix   → "link_pattern": "/event-details/"
        ma.to → "link_pattern": "/event/"

  • listing-embedded (``jsonld_in_listing: true``): the listing page itself embeds
    a JSON-LD array of every event (Tribe / "The Events Calendar"). We read them
    all from the one page, and optionally fetch a detail page for a fuller
    description.

Config knobs (all optional unless noted):

  scraper            "scrape_jsonld.py"            (required)
  url                listing page                  (required)
  listing_urls       [extra listing pages]         default: [url]
  link_pattern       "/event-details/"             detail-crawl; omit for listing-embedded
  jsonld_in_listing  true                          listing-embedded mode
  id_prefix          "lister"                      event-id prefix (DEFAULT: source id).
                                                   Set it to preserve ids minted by a
                                                   prior bespoke scraper so a migration
                                                   doesn't re-duplicate every event.
  scrape_filter      "none"|"keyword"|"style"      scrape-time relevance gate (default "none";
                                                   ingest still keyword-filters untrusted sources)
  active_months      [6,7,8]                       only run in these months (seasonal); else no-op
  date_fix           "evening_ampm"                repair a known AM/PM encoding bug
  detail_description  "wix"|"tribe"                pull a fuller description from the detail page
  browser_ua         true                          send a browser User-Agent (Cloudflare sites)
  exclude_regions    ["portland","maine"]          drop events whose venue matches (default set)
  defaults.location  fallback venue string

Fail-safe: fetch/parse errors yield an empty scrape rather than raising, and
scraper health is recorded (raw JSON-LD events found *before* filtering) so a page
that goes structurally dark is flagged for redesign instead of silently missing
events.

Usage: python3 scripts/scrape_jsonld.py <source_id>
"""

import html as html_lib
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import (
    NY_TZ,
    clean_location,
    detect_styles,
    extract_cost,
    filter_future_events,
    get_source,
    make_event,
    mentions_latin,
    record_scrape_health,
    write_scraped,
)

DEV_UA = {"User-Agent": "boston-latin-dance-dev/0.1"}
BROWSER_UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}
DEFAULT_EXCLUDE_REGIONS = {"portland", "maine", "me 04"}
_EVENT_TYPES = {"Event", "SocialEvent", "DanceEvent", "MusicEvent", "Festival"}


# ── JSON-LD extraction ───────────────────────────────────────────────

def _iter_jsonld_objects(blob):
    """Yield every dict in a parsed JSON-LD blob, flattening arrays and @graph."""
    items = blob if isinstance(blob, list) else [blob]
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("@graph"), list):
            for sub in item["@graph"]:
                if isinstance(sub, dict):
                    yield sub
        else:
            yield item


def parse_jsonld_events(page_html: str) -> list[dict]:
    """Return every schema.org Event object embedded in a page's JSON-LD."""
    events: list[dict] = []
    for block in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        page_html, re.S,
    ):
        try:
            blob = json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        for obj in _iter_jsonld_objects(blob):
            if obj.get("@type") in _EVENT_TYPES:
                events.append(obj)
    return events


def _clean_html(text: str) -> str:
    """Strip tags / unescape entities from a JSON-LD text blob."""
    if not text:
        return ""
    text = html_lib.unescape(str(text))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _jsonld_location(obj: dict) -> str:
    """Best-effort venue string from a JSON-LD Event's location field."""
    loc = obj.get("location") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if isinstance(loc, str):
        return clean_location(loc)
    name = loc.get("name") or ""
    addr = loc.get("address") or ""
    if isinstance(addr, dict):
        addr = ", ".join(
            str(addr.get(k, "")).strip()
            for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode")
            if addr.get(k)
        )
    parts = [p for p in (name, addr) if p]
    return clean_location(", ".join(parts))


def _jsonld_geo(obj: dict):
    """(lat, lng) from a JSON-LD Event's location.geo, or (None, None)."""
    loc = obj.get("location") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    geo = loc.get("geo") if isinstance(loc, dict) else None
    if isinstance(geo, dict):
        return geo.get("latitude"), geo.get("longitude")
    return None, None


def _jsonld_cost(obj: dict):
    """Cost string from a JSON-LD Event's offers, or None."""
    offers = obj.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if isinstance(offers, dict):
        price = offers.get("price", offers.get("lowPrice"))
        if price is not None:
            try:
                return "Free" if float(price) == 0 else f"${price}"
            except (TypeError, ValueError):
                return None
    return None


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY_TZ)
    return dt


def _fix_evening_ampm(start, end):
    """Repair an AM/PM encoding bug some Tribe calendars have for evening socials.

    An evening start is sometimes stored as its AM twin — a 7 PM–10 PM party comes
    through as 07:00–22:00. A social that runs from morning until night isn't real,
    so when a pre-noon start pairs with a late-evening end spanning 6+ hours, shift
    the start into the PM. Daytime festivals and normal evening events are untouched.
    """
    if (start and end and start.hour < 12 and end.hour >= 17
            and (end - start) >= timedelta(hours=6)):
        return start + timedelta(hours=12), end
    return start, end


# ── Description enrichment (site-specific, opt-in via config) ─────────

def _wix_about_text(soup: BeautifulSoup) -> str:
    """Pull the 'About the event' description from a Wix detail page."""
    for heading in soup.find_all(["h2", "h3"]):
        text = heading.get_text(strip=True).lower()
        if "about the event" in text or "about this event" in text:
            container = heading.find_parent()
            while container and len(container.get_text(strip=True)) < 100:
                container = container.find_parent()
            if not container:
                continue
            full = container.get_text(separator="\n", strip=True)
            idx = full.lower().find("about the event")
            if idx < 0:
                idx = full.lower().find("about this event")
            if idx < 0:
                continue
            desc = full[idx:].split(":", 1)[-1].strip()
            for cutoff in ("Share this event", "Share This Event", "bottom of page",
                           "Show More", "Show more", "See More", "See more",
                           "Read More", "Read more"):
                ci = desc.find(cutoff)
                if ci > 0:
                    desc = desc[:ci].strip()
            return desc
    return ""


def _tribe_detail_text(soup: BeautifulSoup) -> str:
    """Pull a readable description from a Tribe Events detail page."""
    content = (
        soup.find("div", class_="tribe-events-single-event-description")
        or soup.find("div", class_="tribe-events-content")
        or soup.find("div", class_="entry-content")
    )
    if not content:
        return ""
    text = content.get_text(separator="\n", strip=True)
    return re.sub(r"\n{2,}", "\n", text)[:1500].strip()


def _enrich_description(base: str, soup: BeautifulSoup, mode: str) -> str:
    """Prefer the longer of the JSON-LD description and the detail-page text."""
    extra = ""
    if mode == "wix":
        extra = _wix_about_text(soup)
    elif mode == "tribe":
        extra = _tribe_detail_text(soup)
    if extra and len(extra) > len(base or ""):
        return extra
    return base


# ── Fetch / build ────────────────────────────────────────────────────

def _fetch(url: str, ua: dict, timeout: int = 20):
    resp = requests.get(url, headers=ua, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _collect_detail_links(listing_urls, link_pattern, ua) -> tuple[list[str], bool]:
    """Detail-crawl mode: gather per-event links matching link_pattern.

    Returns (links, any_listing_fetched) so the caller can tell a structure change
    (page loaded, no links) from the site being unreachable.
    """
    seen: set[str] = set()
    links: list[str] = []
    any_fetched = False
    for listing_url in listing_urls:
        print(f"  Listing page: {listing_url}")
        try:
            page = _fetch(listing_url, ua, timeout=15)
            any_fetched = True
        except Exception as exc:
            print(f"    Failed: {exc}")
            continue
        soup = BeautifulSoup(page, "html.parser")
        listing_tail = listing_url.rstrip("/").split("/")[-1]
        for a in soup.find_all("a", href=True):
            href = a["href"].split("?")[0].split("#")[0]
            if link_pattern not in href:
                continue
            full = urljoin(listing_url, href)
            if full.rstrip("/").split("/")[-1] == listing_tail:
                continue  # the listing page itself
            if full not in seen:
                seen.add(full)
                links.append(full)
        time.sleep(0.4)
    return links, any_fetched


def _event_id(url: str, prefix: str) -> str:
    slug = (url or "").rstrip("/").split("/")[-1] or "event"
    return f"{prefix}-{slug}"


def _build_event(obj: dict, page_url: str, soup, cfg: dict):
    """Turn one JSON-LD Event object into a DanceEvent dict (or None to skip)."""
    name = _clean_html(obj.get("name") or "Untitled")
    description = _clean_html(obj.get("description") or "")
    if soup is not None and cfg["detail_description"]:
        description = _enrich_description(description, soup, cfg["detail_description"])

    location = _jsonld_location(obj) or cfg["default_location"]
    if any(r in location.lower() for r in cfg["exclude_regions"]):
        print(f"    Skipping (excluded region): {name} @ {location}")
        return None

    start = _parse_dt(obj.get("startDate"))
    if not start:
        print(f"    Bad/missing date: {name}")
        return None
    end = _parse_dt(obj.get("endDate")) or start
    if cfg["date_fix"] == "evening_ampm":
        start, end = _fix_evening_ampm(start, end)

    # A range spanning many weeks is a recurring weekly series, not one long event.
    recurring = (end - start).days > 14

    text = f"{name} {description}"
    styles = detect_styles(text)
    if cfg["scrape_filter"] == "style" and styles == ["other"]:
        print(f"    Skipping (no social-dance style): {name}")
        return None
    if cfg["scrape_filter"] == "keyword" and not mentions_latin(text):
        print(f"    Skipping (no Latin keyword): {name}")
        return None

    lat, lng = _jsonld_geo(obj)
    url = obj.get("url") or page_url
    return make_event(
        id=_event_id(url, cfg["id_prefix"]),
        name=name,
        start=start,
        end=start if recurring else end,
        location=location,
        lat=lat,
        lng=lng,
        description=description,
        url=url,
        styles=styles if styles != ["other"] else None,
        cost=_jsonld_cost(obj) or extract_cost(text),
        recurring=recurring,
        source=cfg["source_id"],
    )


def scrape_source(source_id: str) -> list[dict]:
    source = get_source(source_id)
    if not source or not source.get("enabled"):
        print(f"Source '{source_id}' is disabled or not found in sources.json")
        return []

    # Seasonal gate: outside active_months this scraper is a deliberate no-op.
    active_months = source.get("active_months")
    if active_months and datetime.now(NY_TZ).month not in active_months:
        print(f"Off-season (month not in {active_months}); skipping {source_id}.")
        write_scraped(source_id, [])
        record_scrape_health(source_id, 0, 0, note="off-season no-op")
        return []

    cfg = {
        "source_id": source_id,
        "id_prefix": source.get("id_prefix", source_id),
        "scrape_filter": source.get("scrape_filter", "none"),
        "date_fix": source.get("date_fix", ""),
        "detail_description": source.get("detail_description", ""),
        "default_location": (source.get("defaults") or {}).get("location", ""),
        "exclude_regions": set(source.get("exclude_regions", DEFAULT_EXCLUDE_REGIONS)),
    }
    ua = BROWSER_UA if source.get("browser_ua") else DEV_UA
    listing_urls = source.get("listing_urls") or [source["url"]]

    raw_objs: list[tuple[dict, str, object]] = []  # (jsonld_obj, page_url, soup|None)
    fetched = False

    if source.get("jsonld_in_listing"):
        # Listing-embedded: JSON-LD array lives on the listing page(s) themselves.
        for listing_url in listing_urls:
            print(f"[{source_id}] Fetching {listing_url}")
            try:
                page = _fetch(listing_url, ua)
                fetched = True
            except Exception as exc:
                print(f"  failed: {exc}")
                continue
            objs = parse_jsonld_events(page)
            print(f"  {len(objs)} JSON-LD events on page")
            # Detail pages are fetched later, only for events that survive the
            # filter — never pre-fetch all N (these listings can hold hundreds).
            for obj in objs:
                raw_objs.append((obj, listing_url, None))
    else:
        # Detail-crawl: follow link_pattern to per-event pages carrying JSON-LD.
        link_pattern = source.get("link_pattern", "/event-details/")
        print(f"[{source_id}] Collecting '{link_pattern}' links from "
              f"{len(listing_urls)} listing page(s)")
        links, fetched = _collect_detail_links(listing_urls, link_pattern, ua)
        print(f"[{source_id}] Found {len(links)} event detail pages")
        for i, link in enumerate(links):
            try:
                soup = BeautifulSoup(_fetch(link, ua, timeout=15), "html.parser")
            except Exception as exc:
                print(f"  [{i+1}/{len(links)}] fetch failed: {exc}")
                continue
            objs = parse_jsonld_events(str(soup))
            if not objs:
                print(f"  [{i+1}/{len(links)}] no JSON-LD: {link.split('/')[-1][:50]}")
                continue
            # One event per detail page (take the first Event object).
            raw_objs.append((objs[0], link, soup))
            time.sleep(0.4)

    # Build, filter, and de-dupe by id.
    events: list[dict] = []
    seen: set[str] = set()
    for obj, page_url, soup in raw_objs:
        try:
            ev = _build_event(obj, page_url, soup, cfg)
        except Exception as exc:
            print(f"    ERROR building event: {exc}")
            continue
        if not ev or ev["id"] in seen:
            continue
        # Listing-embedded survivors: fetch the detail page NOW (post-filter) for a
        # fuller description, then re-detect styles from the richer text.
        if soup is None and cfg["detail_description"] and obj.get("url"):
            try:
                detail = BeautifulSoup(_fetch(obj["url"], ua, timeout=15), "html.parser")
                richer = _enrich_description(ev["description"], detail, cfg["detail_description"])
                if richer != ev["description"]:
                    ev["description"] = richer
                    styles = detect_styles(f"{ev['name']} {richer}")
                    if styles != ["other"]:
                        ev["styles"] = styles
                time.sleep(0.4)
            except Exception:
                pass
        seen.add(ev["id"])
        events.append(ev)
        print(f"  [keep] {ev['name'][:48]} | {ev['dayOfWeek']} "
              f"{ev['startDate'][:10]} | styles={ev['styles']}")

    upcoming = filter_future_events(events)

    # Health: raw_found = JSON-LD events parsed BEFORE filtering. Zero on a page
    # that loaded means the JSON-LD is gone / markup changed → redesign needed.
    note = ""
    if fetched and not raw_objs:
        note = ("page loaded but no schema.org JSON-LD events found — the site's "
                "markup may have changed; redesign/repoint the scraper")
    record_scrape_health(source_id, len(raw_objs), len(upcoming),
                         fetched=fetched, note=note)

    write_scraped(source_id, upcoming)
    return upcoming


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/scrape_jsonld.py <source_id>")
        sys.exit(1)
    scrape_source(sys.argv[1])


if __name__ == "__main__":
    main()
