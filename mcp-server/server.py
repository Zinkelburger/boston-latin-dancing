#!/usr/bin/env python3
"""
Boston Latin Dance – Event Lifecycle MCP Server.

Local stdio-based MCP server for managing the event pipeline:
  - Add/edit/archive events
  - Approve/reject submissions
  - Run scrapers and ingest results
  - Publish public/events.json

Run: python3 mcp-server/server.py
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from mcp.server.fastmcp import FastMCP

from event_store import (
    add_event,
    approve_pending,
    archive_past_events,
    edit_event,
    expand_venues,
    ingest_scraped,
    load_active,
    load_archive,
    load_pending,
    publish,
    reject_pending,
    save_pending,
    validate_event,
    VENUES_JSON,
    SCRAPED_DIR,
)
from scraper_utils import (
    ROOT,
    DATA_DIR,
    geocode,
    detect_styles,
    extract_cost,
    load_sources,
)

mcp = FastMCP("boston-latin-dance")

SCRIPTS_DIR = ROOT / "scripts"


# ── Event tools ───────────────────────────────────────────────────────


@mcp.tool()
def event_list(
    status: str = "active",
    style: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
) -> str:
    """List events by status (active/pending/archive). Optionally filter by style or text search."""
    if status == "active":
        events = load_active()
    elif status == "pending":
        events = load_pending()
    elif status == "archive":
        events = load_archive()
    else:
        return json.dumps({"error": f"Invalid status '{status}'. Use active/pending/archive."})

    if style:
        events = [e for e in events if style.lower() in [s.lower() for s in e.get("styles", [])]]

    if search:
        q = search.lower()
        events = [e for e in events if q in e.get("name", "").lower() or q in e.get("location", "").lower() or q in e.get("description", "").lower()]

    events = events[:limit]
    summary = []
    for e in events:
        summary.append({
            "id": e["id"],
            "name": e["name"],
            "startDate": e.get("startDate", ""),
            "location": e.get("location", ""),
            "styles": e.get("styles", []),
            "recurring": e.get("recurring", False),
            "has_coords": e.get("lat") is not None,
        })

    return json.dumps({"count": len(summary), "total": len(load_active() if status == "active" else events), "events": summary}, indent=2)


@mcp.tool()
def event_get(event_id: str) -> str:
    """Get full details of a specific event by ID. Searches active, then pending, then archive."""
    for pool_name, pool in [("active", load_active()), ("pending", load_pending()), ("archive", load_archive())]:
        for ev in pool:
            if ev["id"] == event_id:
                return json.dumps({"status": pool_name, "event": ev}, indent=2)
    return json.dumps({"error": f"Event '{event_id}' not found in any pool."})


@mcp.tool()
def event_add(
    name: str,
    start_date: str,
    location: str,
    end_date: Optional[str] = None,
    description: str = "",
    url: Optional[str] = None,
    styles: Optional[str] = None,
    cost: Optional[str] = None,
    recurring: bool = False,
    source: str = "manual",
    event_id: Optional[str] = None,
    force: bool = False,
) -> str:
    """Add a new event to the active store. Handles dedup, geocode, and style detection automatically.

    Args:
        name: Event name
        start_date: ISO datetime string (e.g. 2026-06-15T20:00:00-04:00)
        location: Venue address
        end_date: ISO datetime for event end (defaults to start_date)
        description: Event description
        url: Event URL
        styles: Comma-separated dance styles (e.g. "salsa,bachata"). Auto-detected if omitted.
        cost: Cost string (e.g. "$15", "Free"). Auto-detected if omitted.
        recurring: Whether this is a recurring event
        source: Source identifier
        event_id: Custom event ID (auto-generated if omitted)
        force: If True, merge into existing duplicate instead of rejecting
    """
    import hashlib

    if not event_id:
        hash_input = f"{name}{start_date}{location}"
        event_id = f"{source}-{hashlib.sha1(hash_input.encode()).hexdigest()[:16]}"

    style_list = None
    if styles:
        style_list = [s.strip().lower() for s in styles.split(",")]

    event = {
        "id": event_id,
        "name": name,
        "startDate": start_date,
        "endDate": end_date or start_date,
        "location": location,
        "lat": None,
        "lng": None,
        "description": description,
        "url": url,
        "styles": style_list or detect_styles(f"{name} {description}"),
        "cost": cost or extract_cost(f"{name} {description}"),
        "recurring": recurring,
        "source": source,
    }

    coords = geocode(location)
    if coords:
        event["lat"], event["lng"] = coords

    from event_store import parse_date, DAYS_LIST
    dt = parse_date(start_date)
    if dt:
        event["dayOfWeek"] = DAYS_LIST[dt.isoweekday() % 7]

    result = add_event(event, force=force)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def event_edit(event_id: str, updates_json: str) -> str:
    """Edit fields on an active event. Pass updates as a JSON object string.

    Example updates_json: {"cost": "$20", "url": "https://example.com"}
    """
    try:
        updates = json.loads(updates_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON: {e}"})

    result = edit_event(event_id, updates)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def event_archive(event_id: Optional[str] = None) -> str:
    """Archive past events. If event_id is given, archive that specific event. Otherwise archive all past events automatically."""
    if event_id:
        from event_store import load_active, save_active, load_archive, save_archive, _append_changelog
        active = load_active()
        idx = None
        for i, ev in enumerate(active):
            if ev["id"] == event_id:
                idx = i
                break
        if idx is None:
            return json.dumps({"error": f"Event '{event_id}' not found in active."})

        ev = active.pop(idx)
        from datetime import datetime, timezone
        ev["archivedAt"] = datetime.now(timezone.utc).isoformat()
        archive = load_archive()
        archive.append(ev)
        save_active(active)
        save_archive(archive)
        _append_changelog("archive", event_id, "manual")
        return json.dumps({"status": "archived", "event_name": ev["name"]})

    archived = archive_past_events()
    return json.dumps({
        "status": "done",
        "archived_count": len(archived),
        "archived": [{"id": e["id"], "name": e["name"]} for e in archived],
    }, indent=2)


# ── Pending / submission review ───────────────────────────────────────


@mcp.tool()
def event_approve(event_id: str) -> str:
    """Approve a pending submission, moving it to active after validation and geocoding."""
    result = approve_pending(event_id)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def event_reject(event_id: str, reason: str = "") -> str:
    """Reject a pending submission with an optional reason."""
    result = reject_pending(event_id, reason)
    return json.dumps(result, indent=2, default=str)


# ── Scraping and ingestion ────────────────────────────────────────────


@mcp.tool()
def event_scrape(source_id: Optional[str] = None) -> str:
    """Run scrapers and ingest new events into the active store.

    If source_id is given, run only that scraper. Otherwise run all enabled scrapers.
    After scraping, automatically archives past events and publishes.

    Available source_ids: beatrice-calendar, sensualeros-boston, lister-events, eventbrite-boston-latin, submissions
    (Facebook sources require browser MCP and are not auto-runnable.)
    """
    runnable = {
        "beatrice-calendar": ["scrape_ics.py"],
        "sensualeros-boston": ["scrape_ics.py", "sensualeros-boston"],
        "lister-events": ["scrape_lister.py"],
        "eventbrite-boston-latin": ["scrape_eventbrite.py"],
        "submissions": ["fetch_submissions.py"],
    }

    if source_id and source_id not in runnable:
        return json.dumps({"error": f"Unknown or non-runnable source '{source_id}'. Available: {list(runnable.keys())}"})

    targets = {source_id: runnable[source_id]} if source_id else runnable
    scrape_results = {}

    for sid, cmd_parts in targets.items():
        script_path = SCRIPTS_DIR / cmd_parts[0]
        extra_args = cmd_parts[1:]
        try:
            result = subprocess.run(
                [sys.executable, str(script_path)] + extra_args,
                capture_output=True, text=True, timeout=120,
                cwd=str(ROOT),
            )
            scrape_results[sid] = {
                "exit_code": result.returncode,
                "output": result.stdout[-500:] if result.stdout else "",
                "error": result.stderr[-300:] if result.stderr else "",
            }
        except subprocess.TimeoutExpired:
            scrape_results[sid] = {"exit_code": -1, "error": "timeout (120s)"}
        except Exception as e:
            scrape_results[sid] = {"exit_code": -1, "error": str(e)}

    # Ingest all scraped files
    ingest_result = ingest_scraped(source_id)

    # Auto-archive past events
    archived = archive_past_events()

    return json.dumps({
        "scrape_results": scrape_results,
        "ingestion": ingest_result,
        "archived_count": len(archived),
    }, indent=2)


@mcp.tool()
def event_ingest(source_id: Optional[str] = None) -> str:
    """Ingest events from data/scraped/ into active store WITHOUT re-running scrapers.

    Useful after manually placing a JSON file in data/scraped/ or after browser-based scraping.
    """
    result = ingest_scraped(source_id)
    return json.dumps(result, indent=2)


# ── Publishing ────────────────────────────────────────────────────────


@mcp.tool()
def event_publish() -> str:
    """Regenerate public/events.json from active events + expanded venues.

    This is the build step that produces the file the frontend reads.
    Always run this after making changes to see them on the map.
    """
    result = publish()
    return json.dumps(result, indent=2)


# ── Venue management ──────────────────────────────────────────────────


@mcp.tool()
def venue_list() -> str:
    """List all permanent weekly venues from data/venues.json."""
    if not VENUES_JSON.exists():
        return json.dumps({"venues": []})
    venues = json.loads(VENUES_JSON.read_text())
    summary = []
    for v in venues:
        summary.append({
            "id": v["id"],
            "name": v["name"],
            "location": v.get("location", ""),
            "styles": v.get("styles", []),
            "schedule_days": [s["dayOfWeek"] for s in v.get("schedule", [])],
        })
    return json.dumps({"count": len(summary), "venues": summary}, indent=2)


@mcp.tool()
def venue_add(
    venue_id: str,
    name: str,
    location: str,
    schedule_json: str,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    url: Optional[str] = None,
    styles: Optional[str] = None,
    cost: Optional[str] = None,
    description: str = "",
) -> str:
    """Add a new permanent venue to data/venues.json.

    Args:
        venue_id: Unique venue identifier (e.g. "havana-club")
        name: Venue/event series name
        location: Full street address
        schedule_json: JSON array of schedule objects, e.g. [{"dayOfWeek": "Friday", "time": "9:00 PM – 2:00 AM", "note": ""}]
        lat/lng: Coordinates (auto-geocoded if omitted)
        url: Website URL
        styles: Comma-separated styles (e.g. "salsa,bachata")
        cost: Cost string
        description: Venue description
    """
    try:
        schedule = json.loads(schedule_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid schedule JSON: {e}"})

    if lat is None or lng is None:
        coords = geocode(location)
        if coords:
            lat, lng = coords

    style_list = [s.strip().lower() for s in styles.split(",")] if styles else detect_styles(f"{name} {description}")

    venue = {
        "id": venue_id,
        "name": name,
        "location": location,
        "lat": lat,
        "lng": lng,
        "url": url,
        "styles": style_list,
        "cost": cost,
        "description": description,
        "schedule": schedule,
    }

    venues = json.loads(VENUES_JSON.read_text()) if VENUES_JSON.exists() else []
    for existing in venues:
        if existing["id"] == venue_id:
            return json.dumps({"error": f"Venue '{venue_id}' already exists. Edit it directly in data/venues.json."})

    venues.append(venue)
    VENUES_JSON.write_text(json.dumps(venues, indent=2, ensure_ascii=False))

    from event_store import slugify
    venue["slug"] = slugify(name, venue_id)

    return json.dumps({"status": "added", "venue": venue}, indent=2)


# ── Source management ─────────────────────────────────────────────────


@mcp.tool()
def source_list() -> str:
    """List all registered event sources from data/sources.json."""
    sources = load_sources()
    return json.dumps({"count": len(sources), "sources": sources}, indent=2)


@mcp.tool()
def source_add(
    source_id: str,
    source_type: str,
    name: str,
    scraper: str,
    url: Optional[str] = None,
    config_json: Optional[str] = None,
) -> str:
    """Register a new event source in data/sources.json.

    Args:
        source_id: Unique source identifier (e.g. "my-venue-events")
        source_type: Source type (ics, eventbrite, wix-events, facebook, api)
        name: Human-readable source name
        scraper: Script filename that handles this source (e.g. "scrape_ics.py")
        url: Primary URL for the source
        config_json: Additional config as JSON object (merged into entry)
    """
    sources_path = DATA_DIR / "sources.json"
    sources = json.loads(sources_path.read_text()) if sources_path.exists() else []

    for s in sources:
        if s["id"] == source_id:
            return json.dumps({"error": f"Source '{source_id}' already exists."})

    entry = {
        "id": source_id,
        "type": source_type,
        "name": name,
        "scraper": scraper,
        "enabled": True,
    }
    if url:
        entry["url"] = url
    if config_json:
        try:
            extra = json.loads(config_json)
            entry.update(extra)
        except json.JSONDecodeError:
            pass

    sources.append(entry)
    sources_path.write_text(json.dumps(sources, indent=2, ensure_ascii=False))
    return json.dumps({"status": "added", "source": entry}, indent=2)


# ── Verification ──────────────────────────────────────────────────────


@mcp.tool()
def event_verify(
    event_id: Optional[str] = None,
    stale_days: Optional[int] = None,
) -> str:
    """Verify event details against their source URLs.

    Checks each event's URL (organizer site, Eventbrite, etc.) to confirm
    the event is still active and details are accurate. Facebook and Instagram
    events are flagged for browser-based follow-up.

    Args:
        event_id: Verify just this one event. If omitted, verify all.
        stale_days: Only re-verify events not checked in N+ days.

    Returns a report with status per event:
      confirmed, location_mismatch, date_mismatch, cancelled, page_gone,
      needs_review, needs_browser, no_source, unverifiable
    """
    from verify_events import verify_all

    report = verify_all(event_id=event_id, stale_days=stale_days)

    summary: dict[str, int] = {}
    for entry in report:
        s = entry["status"]
        summary[s] = summary.get(s, 0) + 1

    flagged = [
        {
            "event_id": e["event_id"],
            "event_name": e["event_name"],
            "status": e["status"],
            "notes": e.get("notes", ""),
            "source_url": e.get("source_url"),
            "our_location": e.get("our_location", ""),
            "source_location": e.get("source_location", ""),
        }
        for e in report
        if e["status"] != "confirmed"
    ]

    return json.dumps({
        "total_verified": len(report),
        "summary": summary,
        "flagged_count": len(flagged),
        "flagged": flagged,
    }, indent=2)


@mcp.tool()
def event_set_location_override(event_id: str, location: str) -> str:
    """Set a location override on an event so re-scraping won't revert it.

    Use after verifying that an event's location in the ICS/source is wrong
    and you've confirmed the correct location.

    Args:
        event_id: The event to fix
        location: The correct location string
    """
    active = load_active()
    for ev in active:
        if ev["id"] == event_id:
            ev["_location_override"] = location
            ev["location"] = location
            coords = geocode(location)
            if coords:
                ev["lat"], ev["lng"] = coords
            from event_store import save_active, _append_changelog
            save_active(active)
            _append_changelog("location_override", event_id, location)
            return json.dumps({
                "status": "override_set",
                "event_id": event_id,
                "location": location,
                "geocoded": coords is not None,
            }, indent=2)

    return json.dumps({"error": f"Event '{event_id}' not found in active."})


# ── Entry point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
