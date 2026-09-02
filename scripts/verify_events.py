#!/usr/bin/env python3
"""
Event verification engine.

Checks each event's source URL to confirm details (date, location, status)
are still accurate. Produces a structured report for human review.

Verification strategies by URL type:
  1. Direct organizer site  -- HTTP fetch + JSON-LD / text extraction
  2. Facebook event URL     -- og-scraper fetch, date read from the preview
  3. Facebook page URL      -- needs browser MCP (flagged for agent)
  4. Instagram / social     -- flagged as unverifiable
  5. No URL                 -- flagged for web search

Facebook event pages carry no JSON-LD, but their link preview states the
date outright ("Event in Cambridge, MA by Liz Lister on Saturday, August 15
2026"), and asking as `facebookexternalhit/1.1` is what makes it readable at
all — see scripts/link_meta.py. Before that these short-circuited to
needs_browser, a status nothing ever drained, so a Facebook-linked event was
never actually checked.

Usage:
    python3 scripts/verify_events.py                # verify all active events
    python3 scripts/verify_events.py --id <id>      # verify one event
    python3 scripts/verify_events.py --stale-days 7  # only events not verified in 7+ days
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_io import write_json
from link_meta import link_meta, looks_like_render_timestamp
from scraper_utils import DEV_UA
from event_store import (
    ACTIVE_JSON,
    EVENTS_DIR,
    NY_TZ,
    load_active,
    parse_date,
    save_active,
    _append_changelog,
)


def _ny_calendar_day(iso_str: str) -> Optional[str]:
    """Return the Boston calendar day (YYYY-MM-DD) for an ISO datetime/date string."""
    if not iso_str:
        return None
    dt = parse_date(iso_str)
    if dt is None:
        try:
            dt = datetime.fromisoformat(iso_str[:10])
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY_TZ)
    return dt.astimezone(NY_TZ).date().isoformat()

REPORT_PATH = EVENTS_DIR / "verification-report.json"
# The honest identity (with a contact address) — the same one the scrapers use.
UA = {"User-Agent": DEV_UA}


# ── URL classification ────────────────────────────────────────────────

def classify_url(url: Optional[str]) -> str:
    """Classify a URL into a verification strategy type."""
    if not url:
        return "no_url"
    lower = url.lower()
    parsed = urlparse(lower)
    host = parsed.hostname or ""

    if "facebook.com" in host or "fb.com" in host:
        if "/events/" in lower or "/events?" in lower:
            return "facebook_event"
        return "facebook_page"
    if "instagram.com" in host:
        return "instagram"
    if "eventbrite.com" in host:
        return "eventbrite"
    if host in ("twitter.com", "x.com", "www.twitter.com", "www.x.com"):
        return "social"
    return "direct"


# ── Strategy 1: Direct website fetch ─────────────────────────────────

_JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S
)

_CANCELLED_PATTERNS = [
    re.compile(r"\b(cancel+ed|postponed|rescheduled)\b", re.I),
]

# Policy boilerplate like "all concerts will be canceled in the event of
# inclement weather" (boston.gov) is not a cancellation notice.
_CONDITIONAL_CONTEXT = re.compile(
    r"(in the event of|in case of|\bif\b|will be|may be|weather|rain)", re.I
)


def _cancellation_mention(html: str) -> Optional[re.Match]:
    """Find a cancellation mention that isn't conditional/policy phrasing."""
    for pat in _CANCELLED_PATTERNS:
        for m in pat.finditer(html[:5000]):
            prefix = html[max(0, m.start() - 80):m.start()]
            if _CONDITIONAL_CONTEXT.search(prefix):
                continue
            return m
    return None


def _extract_jsonld_events(html: str) -> list[dict]:
    events = []
    for m in _JSONLD_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") in ("Event", "SocialEvent", "DanceEvent"):
                    events.append(item)
        except (json.JSONDecodeError, TypeError):
            pass
    return events


def _location_from_jsonld(event_ld: dict) -> Optional[str]:
    loc = event_ld.get("location", {})
    if isinstance(loc, str):
        return loc
    if isinstance(loc, dict):
        name = loc.get("name", "")
        addr = loc.get("address", "")
        if isinstance(addr, dict):
            addr = addr.get("streetAddress", "")
        parts = [p for p in [name, addr] if p]
        return ", ".join(parts) if parts else None
    return None


def verify_direct(event: dict, url: str) -> dict:
    """Fetch a direct URL and compare against event data."""
    result = {
        "event_id": event["id"],
        "event_name": event["name"],
        "url_type": "direct",
        "source_url": url,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        resp = requests.get(url, headers=UA, timeout=15, allow_redirects=True)
    except requests.RequestException as e:
        result["status"] = "page_gone"
        result["notes"] = f"Request failed: {e}"
        return result

    if resp.status_code == 404:
        result["status"] = "page_gone"
        result["notes"] = f"HTTP 404"
        return result
    if resp.status_code >= 400:
        result["status"] = "page_gone"
        result["notes"] = f"HTTP {resp.status_code}"
        return result

    html = resp.text

    ld_events = _extract_jsonld_events(html)
    ld_status = ld_events[0].get("eventStatus", "") if ld_events else ""

    # JSON-LD eventStatus is authoritative; only fall back to scanning the
    # page text when the structured data doesn't declare a status.
    if ld_status == "https://schema.org/EventCancelled":
        result["status"] = "cancelled"
        result["notes"] = "JSON-LD eventStatus = EventCancelled"
        return result
    if ld_status != "https://schema.org/EventScheduled":
        m = _cancellation_mention(html)
        if m:
            result["status"] = "needs_review"
            result["notes"] = f"Page contains '{m.group(0)}' — may be cancelled or rescheduled"
            return result

    if ld_events:
        ld = ld_events[0]
        source_loc = _location_from_jsonld(ld)
        source_start = ld.get("startDate", "")

        issues = []
        notes = []

        # Date check — highest-stakes field: a wrong day sends people to an empty
        # room. Skip recurring series (JSON-LD carries one occurrence; our stored
        # startDate is the first of many, so a diff there is expected, not wrong).
        our_start = event.get("startDate", "")
        # Some calendars stamp the page's render clock into startDate rather
        # than the event's — boston.gov does, and the seconds tick between
        # fetches. A date_mismatch is acted on as "the source wins", so
        # believing it would rewrite a correct date to today's.
        if looks_like_render_timestamp(source_start):
            source_start = ""
            notes.append("source startDate is the page's render clock — ignored")
        date_checked = False
        if source_start and our_start and not event.get("recurrences") and not event.get("recurring"):
            src_day = _ny_calendar_day(source_start)
            our_day = _ny_calendar_day(our_start)
            date_checked = bool(src_day and our_day)
            if src_day and our_day and src_day != our_day:
                result["source_date"] = source_start
                result["our_date"] = our_start
                issues.append("date_mismatch")
                notes.append(f"date: source {src_day} vs our {our_day}")

        if source_loc:
            result["source_location"] = source_loc
            result["our_location"] = event.get("location", "")
            our_loc_lower = event.get("location", "").lower()
            src_loc_lower = source_loc.lower()
            if our_loc_lower and src_loc_lower:
                if our_loc_lower not in src_loc_lower and src_loc_lower not in our_loc_lower:
                    our_words = set(our_loc_lower.split()) - {"the", "at", "in"}
                    src_words = set(src_loc_lower.split()) - {"the", "at", "in"}
                    if our_words and src_words:
                        overlap = our_words & src_words
                        if len(overlap) < max(1, min(len(our_words), len(src_words)) * 0.3):
                            issues.append("location_mismatch")
                            notes.append(f"location: source '{source_loc}' vs our '{event.get('location', '')}'")

        if issues:
            # date_mismatch is the higher-stakes signal — surface it first.
            issues.sort(key=lambda s: 0 if s == "date_mismatch" else 1)
            result["status"] = issues[0]
            result["notes"] = "; ".join(notes)
        else:
            # Say what was actually checked. Claiming "date + location" when
            # the date was discarded as a render clock, or when the source
            # published no date at all, reads as a stronger confirmation than
            # we earned — and a reviewer trusts it.
            checked = [f for f, present in
                       (("date", date_checked), ("location", bool(source_loc))) if present]
            if checked:
                result["status"] = "confirmed"
                result["notes"] = "; ".join(
                    notes + [f"JSON-LD matches ({' + '.join(checked)} checked)"])
            elif ld_status == "https://schema.org/EventScheduled":
                # Nothing comparable was published, but the source does state
                # outright that the event is still on — worth more than a page
                # that merely loaded.
                result["status"] = "confirmed"
                result["notes"] = "; ".join(
                    notes + ["JSON-LD says the event is scheduled; "
                             "no date or location published to compare"])
            else:
                result["status"] = "reachable_only"
                result["notes"] = "; ".join(
                    notes + ["JSON-LD found but it published nothing to check"])
        return result

    # A reachable page with no structured data proves the URL is live but confirms
    # nothing about date or location — do not call that "confirmed".
    result["status"] = "reachable_only"
    result["notes"] = "Page accessible, no JSON-LD found — date/location NOT verified"
    return result


def verify_eventbrite(event: dict, url: str) -> dict:
    """Eventbrite pages have good JSON-LD — use the direct strategy."""
    return verify_direct(event, url)


# ── Strategies that need browser MCP ─────────────────────────────────

def verify_facebook_event(event: dict, url: str) -> dict:
    """Verify a Facebook event against its own link preview.

    This used to short-circuit to needs_browser, a status nothing ever drained,
    so every Facebook-linked event went permanently unchecked. Facebook ships
    no JSON-LD, but asked as its own og-scraper it states the facts in the
    preview sentence — "Event in Cambridge, MA by Liz Lister on Saturday,
    August 15 2026" — which is enough to check the date, the field where being
    wrong sends people to an empty room.
    """
    result = {
        "event_id": event["id"],
        "event_name": event["name"],
        "url_type": "facebook_event",
        "source_url": url,
        "our_location": event.get("location", ""),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }

    meta = link_meta(url)
    status = meta["status"]

    if status == 404:
        result["status"] = "page_gone"
        result["notes"] = "HTTP 404"
        return result
    if status is None or status >= 500 or status in (401, 403, 429):
        result["status"] = "needs_browser"
        result["notes"] = f"Facebook would not answer ({meta['error'] or f'HTTP {status}'})"
        return result
    if not meta["og_title"]:
        # A live event always previews with a title; its absence is the same
        # signal a deleted post gives.
        result["status"] = "page_gone"
        result["notes"] = "No preview metadata — event appears deleted"
        return result

    details = meta.get("facebook_event")
    if not details:
        result["status"] = "reachable_only"
        result["notes"] = f"Event page live ('{meta['og_title'][:60]}'), but the preview carried no date"
        return result

    result["source_date"] = details["date"]
    result["our_date"] = event.get("startDate", "")
    if details["location"]:
        result["source_location"] = details["location"]

    # Recurring series carry one occurrence upstream against many of ours, so a
    # difference there is expected rather than wrong.
    our_day = _ny_calendar_day(event.get("startDate", ""))
    if our_day and not event.get("recurring") and not event.get("recurrences"):
        if details["date"] != our_day:
            result["status"] = "date_mismatch"
            result["notes"] = (f"date: source {details['date']} ({details['weekday']}) "
                               f"vs our {our_day}")
            return result
        result["status"] = "confirmed"
        result["notes"] = (f"Facebook preview matches (date checked); "
                           f"by {details['organizer']}")
        return result

    result["status"] = "reachable_only"
    result["notes"] = (f"Event page live, upstream date {details['date']} — recurring series, "
                       f"not compared against our first occurrence")
    return result


def flag_facebook_page(event: dict, url: str) -> dict:
    return {
        "event_id": event["id"],
        "event_name": event["name"],
        "url_type": "facebook_page",
        "source_url": url,
        "status": "needs_browser",
        "notes": "Facebook page — agent should check Events tab for upcoming events",
        "our_location": event.get("location", ""),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def flag_instagram(event: dict, url: str) -> dict:
    return {
        "event_id": event["id"],
        "event_name": event["name"],
        "url_type": "instagram",
        "source_url": url,
        "status": "unverifiable",
        "notes": "Instagram link — cannot scrape programmatically. Manual check recommended.",
        "our_location": event.get("location", ""),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def flag_no_url(event: dict) -> dict:
    location = event.get("location", "")
    name = event.get("name", "")
    search_hint = f"{name} {location} boston"
    return {
        "event_id": event["id"],
        "event_name": event["name"],
        "url_type": "no_url",
        "source_url": None,
        "status": "no_source",
        "notes": f"No URL. Search suggestion: \"{search_hint}\"",
        "our_location": location,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def flag_social(event: dict, url: str) -> dict:
    return {
        "event_id": event["id"],
        "event_name": event["name"],
        "url_type": "social",
        "source_url": url,
        "status": "unverifiable",
        "notes": "Social media link — limited scrapeability. Manual check recommended.",
        "our_location": event.get("location", ""),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Main verification dispatcher ─────────────────────────────────────

def _verifiable_alternate(event: dict) -> Optional[str]:
    """First entry in urls[] we can actually check without a browser.

    Social links short-circuit to needs_browser, which is a status nothing ever
    drains — so an event whose primary is Facebook went unverified forever even
    when a scrapeable organizer page sat one field over in urls[].
    """
    for alt in event.get("urls") or []:
        if alt and classify_url(alt) in ("direct", "eventbrite"):
            return alt
    return None


def verify_event(event: dict) -> dict:
    """Verify a single event. Returns a report entry."""
    url = event.get("url")
    url_type = classify_url(url)

    # Primary needs a browser, but an alternate doesn't: verify against the
    # alternate and record which URL actually answered.
    if url_type in ("facebook_event", "facebook_page", "instagram", "social"):
        alt = _verifiable_alternate(event)
        if alt:
            result = (verify_eventbrite if classify_url(alt) == "eventbrite"
                      else verify_direct)(event, alt)
            result["notes"] = (
                f"{result.get('notes', '')} (verified via urls[] alternate; "
                f"primary {url_type} link not checkable)"
            ).strip()
            return result

    if url_type == "direct":
        return verify_direct(event, url)
    elif url_type == "eventbrite":
        return verify_eventbrite(event, url)
    elif url_type == "facebook_event":
        return verify_facebook_event(event, url)
    elif url_type == "facebook_page":
        return flag_facebook_page(event, url)
    elif url_type == "instagram":
        return flag_instagram(event, url)
    elif url_type == "social":
        return flag_social(event, url)
    elif url_type == "no_url":
        return flag_no_url(event)
    else:
        return flag_no_url(event)


def update_event_verification(event_id: str, report_entry: dict) -> None:
    """Write verification metadata back to the event in active.json."""
    active = load_active()
    for ev in active:
        if ev["id"] == event_id:
            ev["_verified_at"] = report_entry["verified_at"]
            ev["_verified_status"] = report_entry["status"]
            ev["_verified_notes"] = report_entry.get("notes", "")
            ev["_verification_url"] = report_entry.get("source_url")
            break
    save_active(active)


def verify_all(
    event_id: Optional[str] = None,
    stale_days: Optional[int] = None,
) -> list[dict]:
    """Verify events and produce a report.

    Args:
        event_id: If set, verify only this event.
        stale_days: If set, only verify events not checked in N+ days.
    """
    active = load_active()

    if event_id:
        active = [e for e in active if e["id"] == event_id]
        if not active:
            print(f"Event '{event_id}' not found in active store.")
            return []

    if stale_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
        filtered = []
        for e in active:
            last_check = e.get("_verified_at")
            if not last_check:
                filtered.append(e)
                continue
            try:
                last_dt = datetime.fromisoformat(last_check)
                if last_dt < cutoff:
                    filtered.append(e)
            except ValueError:
                filtered.append(e)
        active = filtered

    report: list[dict] = []
    for i, event in enumerate(active):
        print(f"[{i+1}/{len(active)}] Verifying: {event['name'][:60]}...")
        entry = verify_event(event)
        report.append(entry)
        update_event_verification(event["id"], entry)

    write_json(REPORT_PATH, report)
    print(f"\nWrote verification report to {REPORT_PATH}")
    return report


def print_report(report: list[dict]) -> None:
    """Print a human-readable summary of the verification report."""
    status_groups: dict[str, list[dict]] = {}
    for entry in report:
        status = entry["status"]
        status_groups.setdefault(status, []).append(entry)

    status_order = [
        "cancelled", "page_gone", "date_mismatch", "location_mismatch",
        "needs_review", "needs_browser", "no_source", "unverifiable",
        "reachable_only", "confirmed",
    ]

    labels = {
        "confirmed": "CONFIRMED (date + location)",
        "reachable_only": "REACHABLE ONLY (URL live, details unverified)",
        "location_mismatch": "LOCATION MISMATCH",
        "date_mismatch": "DATE MISMATCH",
        "cancelled": "CANCELLED",
        "page_gone": "PAGE GONE (404)",
        "needs_review": "NEEDS REVIEW",
        "needs_browser": "NEEDS BROWSER (Facebook)",
        "no_source": "NO SOURCE URL",
        "unverifiable": "UNVERIFIABLE (social link)",
    }

    print(f"\n{'='*70}")
    print(f"  Event Verification Report  ({len(report)} events)")
    print(f"{'='*70}")

    for status in status_order:
        entries = status_groups.pop(status, [])
        if not entries:
            continue
        label = labels.get(status, status.upper())
        print(f"\n  --- {label} ({len(entries)}) ---")
        for e in entries:
            print(f"    [{e['event_id'][:16]}] {e['event_name'][:55]}")
            if e.get("source_url"):
                print(f"      URL: {e['source_url'][:70]}")
            if e.get("notes"):
                print(f"      {e['notes'][:100]}")
            if e.get("source_location"):
                print(f"      Source loc: {e['source_location'][:60]}")
                print(f"      Our loc:    {e.get('our_location', '')[:60]}")

    for status, entries in status_groups.items():
        if entries:
            print(f"\n  --- {status.upper()} ({len(entries)}) ---")
            for e in entries:
                print(f"    [{e['event_id'][:16]}] {e['event_name'][:55]}")

    counts = {s: len(es) for s, es in {**{s: [e for e in report if e["status"] == s] for s in set(e["status"] for e in report)}}.items()}
    print(f"\n  Summary: {counts}")
    print()


def main():
    event_id = None
    stale_days = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--id" and i + 1 < len(args):
            event_id = args[i + 1]
            i += 2
        elif args[i] == "--stale-days" and i + 1 < len(args):
            stale_days = int(args[i + 1])
            i += 2
        else:
            i += 1

    report = verify_all(event_id=event_id, stale_days=stale_days)
    print_report(report)


if __name__ == "__main__":
    main()
