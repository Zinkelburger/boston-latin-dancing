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
    approve_rejected,
    archive_past_events,
    block_event,
    dismiss_rejected,
    edit_event,
    expand_venues,
    forget_known_duplicate,
    ingest_scraped,
    list_known_duplicates,
    load_active,
    load_archive,
    load_blocked,
    load_pending,
    load_rejected,
    publish_guarded,
    reject_pending,
    remove_active_event,
    save_pending,
    unblock_event,
    validate_event,
    _looks_like_class,
    _special_edition_mismatch,
    VALID_BLOCK_CATEGORIES,
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
    """List events by status (active/pending/rejected/blocked/archive). Optionally filter by style or text search."""
    if status == "active":
        events = load_active()
    elif status == "pending":
        events = load_pending()
    elif status == "rejected":
        events = load_rejected()
    elif status == "blocked":
        events = load_blocked()
    elif status == "archive":
        events = load_archive()
    else:
        return json.dumps({"error": f"Invalid status '{status}'. Use active/pending/rejected/blocked/archive."})

    if style:
        events = [e for e in events if style.lower() in [s.lower() for s in e.get("styles", [])]]

    if search:
        q = search.lower()
        events = [e for e in events if q in e.get("name", "").lower() or q in e.get("location", "").lower() or q in e.get("description", "").lower()]

    events = events[:limit]
    # Cache active once for pending dedup-candidate comparisons below.
    active_by_id = {a["id"]: a for a in load_active()} if status == "pending" else {}
    summary = []
    for e in events:
        row = {
            "id": e["id"],
            "name": e["name"],
            "startDate": e.get("startDate", ""),
            "location": e.get("location", ""),
            "styles": e.get("styles", []),
            "recurring": e.get("recurring", False),
            "has_coords": e.get("lat") is not None,
        }
        if status == "rejected":
            row["rejected_reason"] = e.get("_rejected_reason", "")
            row["review_type"] = e.get("_review_type", "")
        if status == "pending":
            if e.get("_quarantined_new"):
                row["quarantined_new"] = True
            if e.get("_dedup_candidate_of"):
                row["dedup_candidate_of"] = e["_dedup_candidate_of"]
                row["dedup_reason"] = e.get("_dedup_reason", "")
                candidate = active_by_id.get(e["_dedup_candidate_of"])
                # Approving across this line would fold a special edition into
                # its recurring series (blocked without force) — surface it.
                if candidate is not None and _special_edition_mismatch(e, candidate):
                    row["special_edition_mismatch"] = True
            # Advisory: reads like a class/workshop rather than a social dance.
            if _looks_like_class(e):
                row["looks_like_class"] = True
        summary.append(row)

    return json.dumps({"count": len(summary), "total": len(load_active() if status == "active" else events), "events": summary}, indent=2)


@mcp.tool()
def event_get(event_id: str) -> str:
    """Get full details of a specific event by ID. Searches active, pending, rejected, blocked, then archive."""
    for pool_name, pool in [
        ("active", load_active()),
        ("pending", load_pending()),
        ("rejected", load_rejected()),
        ("blocked", load_blocked()),
        ("archive", load_archive()),
    ]:
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

    from event_store import parse_date, DAYS_LIST, NY_TZ
    dt = parse_date(start_date)
    if dt:
        event["dayOfWeek"] = DAYS_LIST[dt.astimezone(NY_TZ).isoweekday() % 7]

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
def event_approve(event_id: str, force: bool = False) -> str:
    """Approve a pending submission, moving it to active after validation and geocoding.

    For a dedup pair this MERGES the two events and permanently records them as
    the same (future occurrences auto-merge with no review). If the pair straddles
    a special-edition boundary (an anniversary/festival/takeover/guest night vs its
    recurring series) the merge is refused — pass force=True only if they truly are
    the same event; otherwise use event_reject(reason="distinct event").
    """
    result = approve_pending(event_id, force=force)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def event_reject(event_id: str, reason: str = "") -> str:
    """Reject a pending submission with an optional reason."""
    result = reject_pending(event_id, reason)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def event_remove(event_id: str, reason: str = "removed from active", block: bool = False, block_category: str = "other") -> str:
    """Remove an active event.

    If block=False (default): queues in rejected.json for review.
    If block=True: permanently blocks the event (prevents re-scraping).

    block_category (when block=True): defunct, class_only, not_latin, not_dance, out_of_area, duplicate_source, other
    """
    result = remove_active_event(event_id, reason, block=block, block_category=block_category)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def event_approve_rejected(event_id: str) -> str:
    """Promote a rejected event to active (bypasses Latin dance keyword check)."""
    result = approve_rejected(event_id)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def event_dismiss_rejected(event_id: str, reason: str = "", block: bool = False, block_category: str = "other") -> str:
    """Dismiss a rejected event.

    If block=True, permanently blocks the event (prevents re-scraping from adding it back).
    If block=False (default), just removes from rejected queue.

    block_category (when block=True): defunct, class_only, not_latin, not_dance, out_of_area, duplicate_source, other
    """
    result = dismiss_rejected(event_id, reason, block=block, block_category=block_category)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def event_block(event_id: str, category: str, notes: str = "") -> str:
    """Permanently block an event from appearing on the map. Prevents re-scraping from adding it back.

    Searches active, rejected, and archive to find and remove the event, then adds to blocked.json.

    Categories:
      defunct          - event used to exist but organizer discontinued it
      class_only       - only classes, no social dancing
      not_latin        - confirmed not Latin dance relevant
      not_dance        - music class, fitness, drum circle, etc.
      out_of_area      - not in Boston metro area
      duplicate_source - covered by another source or venue entry
      other            - catch-all
    """
    result = block_event(event_id, category, notes)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def event_unblock(event_id: str) -> str:
    """Remove an event from the blocklist. It will be re-added on the next scrape if still in the source."""
    result = unblock_event(event_id)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def event_list_blocked(category: Optional[str] = None) -> str:
    """List all permanently blocked events. Optionally filter by category.

    Categories: defunct, class_only, not_latin, not_dance, out_of_area, duplicate_source, other
    """
    blocked = load_blocked()
    if category:
        if category not in VALID_BLOCK_CATEGORIES:
            return json.dumps({"error": f"Invalid category '{category}'. Use one of: {list(VALID_BLOCK_CATEGORIES)}"})
        blocked = [b for b in blocked if b.get("blocked_category") == category]
    summary = [{
        "id": b["id"],
        "name": b.get("name", ""),
        "blocked_category": b.get("blocked_category", ""),
        "blocked_reason": b.get("blocked_reason", ""),
        "blocked_at": b.get("blocked_at", ""),
    } for b in blocked]
    return json.dumps({"count": len(summary), "blocked": summary}, indent=2)


# ── Known-duplicate verdicts ──────────────────────────────────────────


@mcp.tool()
def known_duplicate_list() -> str:
    """List human-reviewed duplicate verdicts from data/known_duplicates.json.

    Each entry is a pair of event IDs with a verdict: "same" (future occurrences
    auto-merge silently) or "different" (the pair is never flagged for review
    again). Use this to audit what past approvals committed the pipeline to.
    """
    entries = list_known_duplicates()
    return json.dumps({"count": len(entries), "known_duplicates": entries}, indent=2)


@mcp.tool()
def known_duplicate_forget(id_a: str, id_b: str) -> str:
    """Delete a stored duplicate verdict so the pair is re-evaluated on next scrape.

    Undoes a wrong "same" verdict (which otherwise auto-merges the pair forever)
    or a wrong "different" verdict (which suppresses it from review forever).
    Removing the record does NOT un-merge events that were already merged — fix
    those with event_edit / event_add if needed.
    """
    result = forget_known_duplicate(id_a, id_b)
    return json.dumps(result, indent=2)


# ── Scraping and ingestion ────────────────────────────────────────────


@mcp.tool()
def event_scrape(source_id: Optional[str] = None, quarantine_new: bool = False) -> str:
    """Run scrapers and ingest new events into the active store.

    If source_id is given, run only that scraper. Otherwise run all enabled scrapers.
    After scraping, automatically archives past events. Does NOT publish — call event_publish() separately.
    quarantine_new=True routes brand-new events to pending.json for review instead of active.

    Available source_ids: beatrice-calendar, sensualeros-boston, unabulla-cuban-boston, lister-events, eventbrite-boston-latin, fiesta-dance-company, submissions
    (Facebook sources require browser MCP and are not auto-runnable.)
    """
    runnable = {
        "beatrice-calendar": ["scrape_ics.py"],
        "sensualeros-boston": ["scrape_ics.py", "sensualeros-boston"],
        "unabulla-cuban-boston": ["scrape_ics.py", "unabulla-cuban-boston"],
        "lister-events": ["scrape_lister.py"],
        "eventbrite-boston-latin": ["scrape_eventbrite.py"],
        "fiesta-dance-company": ["scrape_fiesta_dance.py"],
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
    ingest_result = ingest_scraped(source_id, quarantine_new=quarantine_new)

    # Auto-archive past events
    archived = archive_past_events()

    return json.dumps({
        "scrape_results": scrape_results,
        "ingestion": ingest_result,
        "archived_count": len(archived),
    }, indent=2)


@mcp.tool()
def event_ingest(source_id: Optional[str] = None, quarantine_new: bool = False) -> str:
    """Ingest events from data/scraped/ into active store WITHOUT re-running scrapers.

    Useful after manually placing a JSON file in data/scraped/ or after browser-based scraping.
    quarantine_new=True routes brand-new events to pending.json for review instead of active.
    """
    result = ingest_scraped(source_id, quarantine_new=quarantine_new)
    return json.dumps(result, indent=2)


# ── Publishing ────────────────────────────────────────────────────────


@mcp.tool()
def event_publish() -> str:
    """Regenerate public/events.json from active events + expanded venues.

    This is the build step that produces the file the frontend reads.
    Always run this after making changes to see them on the map.

    Guarded: if the live-event count collapses below 70% of the previously
    published file, the published files are restored and the result reports
    "status": "tripwire" with "tripped": true — do NOT commit; investigate first.
    """
    result = publish_guarded()
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

    # "confirmed" and "reachable_only" both need no action — a reachable page with
    # no structured data just couldn't be checked, it isn't a problem to fix.
    flagged = [
        {
            "event_id": e["event_id"],
            "event_name": e["event_name"],
            "status": e["status"],
            "notes": e.get("notes", ""),
            "source_url": e.get("source_url"),
            "our_location": e.get("our_location", ""),
            "source_location": e.get("source_location", ""),
            "our_date": e.get("our_date", ""),
            "source_date": e.get("source_date", ""),
        }
        for e in report
        if e["status"] not in ("confirmed", "reachable_only")
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
