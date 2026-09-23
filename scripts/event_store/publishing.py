"""Publishing: build data/events-published.json from active events,
expanded venues and the archive, behind a tripwire that refuses to ship a
collapsed map."""

import json
from datetime import datetime
from typing import Optional

import atomic_io
import source_signal
from atomic_io import CorruptJSONError
from recurrence_utils import NY_TZ, recurrence_label

from . import paths, sources, storage
from .classify import derive_special, enrich_event
from .dedup import deduplicate
from .occurrences import implausible_start_hour
from .series import collapse_recurring_series, roll_series_forward
from .slugs import resolve_slug_collisions, slugify
from .storage import log
from .venue_conflicts import suppress_venue_covered_events, write_venue_conflicts
from .venues import expand_venues


def _strip_internal_fields(ev: dict, source_names: dict[str, str]) -> None:
    """Add slug/organizer/special, remove internal fields from an event dict."""
    ev["slug"] = slugify(ev["name"], ev["id"])
    derive_special(ev)
    if ev.get("recurring") and not ev.get("recurrenceLabel"):
        label = recurrence_label(ev)
        if label:
            ev["recurrenceLabel"] = label
    # Map source ID to human-readable organizer name
    source_id = ev.get("source", "")
    if source_id and source_id in source_names:
        ev["organizer"] = source_names[source_id]
    ev.pop("source", None)
    ev.pop("archivedAt", None)
    ev.pop("reactivatedAt", None)
    for key in list(ev.keys()):
        if key.startswith("_"):
            ev.pop(key)


# Archived rows are published only so their /event/ pages stay up; a past
# event's page needs a preview of its blurb, not the whole thing.
ARCHIVED_DESCRIPTION_LIMIT = 300


def _truncate_description(text: str, limit: int = ARCHIVED_DESCRIPTION_LIMIT) -> str:
    """Cut to at most ``limit`` characters at a word boundary, ending in "…"."""
    text = text or ""
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    boundary = max(cut.rfind(" "), cut.rfind("\n"), cut.rfind("\t"))
    if boundary > 0:
        cut = cut[:boundary]
    return cut.rstrip(" \n\t,;:-–—") + "…"


def _compute_publish(*, enrich_missing: bool = True, record_dedup_log: bool = True) -> dict:
    """Everything a publish would ship, computed without writing a byte.

    Split from the write step so publish_guarded() can measure the result
    against the previous file and, when the tripwire trips, ship nothing at
    all — no half-written JSON, no slug registry that has already retired
    URLs for a publish that never happened.
    """
    source_names = sources.load_source_names()
    unreliable_sources = source_signal.unreliable_source_ids()

    # Belt-and-suspenders: never ship pins from unreliable sources even if a
    # stale active row survived (e.g. before the source was demoted).
    active = [
        e for e in storage.load_active()
        if e.get("source") not in unreliable_sources
    ]
    venue_events = expand_venues()

    active, suppressed_venue_ids, venue_report = suppress_venue_covered_events(venue_events, active)
    # Irregular-schedule venues (nextDateApproximate) never get a pin: their
    # expanded dates are pattern guesses, so users only ever see the venue via
    # a confirmed scraped event. But the venue itself stays findable — when no
    # scraped event covers it, we publish a dateless search-only record below.
    irregular_venues = [
        v for v in venue_events
        if v.get("nextDateApproximate") and v.get("id") not in suppressed_venue_ids
    ]
    venue_events = [
        v for v in venue_events
        if v.get("id") not in suppressed_venue_ids and not v.get("nextDateApproximate")
    ]
    all_events = venue_events + active
    deduped = deduplicate(all_events, record_log=record_dedup_log)
    deduped = collapse_recurring_series(deduped)

    # A live series must advertise its next night, not the one it was first
    # scraped with.
    today = datetime.now(NY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    rolled = [ev.get("id") for ev in deduped if roll_series_forward(ev, today)]

    # Sort by start date
    deduped.sort(key=lambda e: e.get("startDate", ""))

    # Publishing may repair missing coordinates; read-only diagnostics must not
    # make network calls or mutate even their in-memory preview unexpectedly.
    if enrich_missing:
        for ev in deduped:
            if ev.get("lat") is None or ev.get("lng") is None:
                enrich_event(ev)

    # Strip internal fields from active events
    for ev in deduped:
        _strip_internal_fields(ev, source_names)

    # Include archived events so their pages persist for SEO. Only their own
    # pages use them, so their descriptions are cut down to a preview.
    archive = storage.load_archive()
    archived_out = []
    for ev in archive:
        if enrich_missing and (ev.get("lat") is None or ev.get("lng") is None):
            enrich_event(ev)
        _strip_internal_fields(ev, source_names)
        ev["archived"] = True
        if ev.get("description"):
            ev["description"] = _truncate_description(ev["description"])
        archived_out.append(ev)

    # Dateless search-only records for irregular venues: searchable, with a
    # detail page and a ghost dot when opened — never a pin, feed row, or
    # filter hit. Guessed dates are stripped so no uncertain date ever ships.
    searchonly_out = []
    for ev in irregular_venues:
        rec = dict(ev)
        rec["startDate"] = ""
        rec["endDate"] = ""
        rec.pop("recurrences", None)
        # Weekly-schedule rows would read as "happens every week"; the
        # recurrenceLabel + description carry the real cadence.
        rec.pop("schedule", None)
        rec["searchOnly"] = True
        if rec.get("_sourceId"):
            rec["source"] = rec["_sourceId"]
        if enrich_missing and (rec.get("lat") is None or rec.get("lng") is None):
            enrich_event(rec)
        _strip_internal_fields(rec, source_names)
        searchonly_out.append(rec)

    published = deduped + archived_out + searchonly_out
    moved_slugs = resolve_slug_collisions(published)

    missing = [ev for ev in deduped if ev.get("lat") is None or ev.get("lng") is None]
    odd_hours = [(e, h) for e in deduped
                 if (h := implausible_start_hour(e)) is not None]

    return {
        "published": published,
        "deduped": deduped,
        "archived_out": archived_out,
        "searchonly_out": searchonly_out,
        "venue_report": venue_report,
        "moved_slugs": moved_slugs,
        "missing": missing,
        "odd_hours": odd_hours,
        "rolled": rolled,
    }


def preview_publish() -> dict:
    """Compute publish artifacts without writes, geocoding, or dedup logging."""
    with storage.store_lock():
        return _compute_publish(enrich_missing=False, record_dedup_log=False)


def _commit_publish(art: dict) -> dict:
    """Write a computed publish to disk and report on it (stderr only)."""
    published = art["published"]
    deduped = art["deduped"]
    venue_report = art["venue_report"]

    write_venue_conflicts(venue_report)

    if art["searchonly_out"]:
        names = ", ".join(repr(e.get("name", "?")) for e in art["searchonly_out"])
        log(f"  ℹ️  {len(art['searchonly_out'])} irregular venue(s) published as search-only records: {names}")

    if art["moved_slugs"]:
        log(f"  🔀 {len(art['moved_slugs'])} event(s) had a colliding slug and were re-slugged:")
        for event_id, slug in art["moved_slugs"]:
            log(f"       - {event_id} → {slug}")

    # Loudly surface anything shipping without coordinates — those events never
    # render a pin on the map, so they're effectively invisible to visitors.
    if art["missing"]:
        log(f"  ⚠️  {len(art['missing'])} active event(s) have no coordinates (won't appear on map):")
        for ev in art["missing"]:
            log(f"       - {ev.get('name', '?')!r}  ({ev.get('location') or 'no location'})")

    if art["rolled"]:
        log(f"  📅 {len(art['rolled'])} recurring series rolled forward to their next occurrence")

    atomic_io.write_json(paths.PUBLIC_EVENTS_JSON, published)

    # Record this run's URLs and re-point any that this publish just retired.
    # Every publish path goes through here — the pipeline's and the agent's —
    # so a slug can never quietly disappear between the index and the site.
    # A registry *problem* must not un-ship the events already written, so it
    # is reported rather than raised — except a corrupt registry file, which
    # must stop the run instead of being rebuilt from nothing.
    registry_result = None
    try:
        from slug_registry import update as _update_slug_registry
        registry_result = _update_slug_registry()
        if registry_result["alias"] or registry_result["ended"]:
            log(f"  🔗 urls: {registry_result['live']} live, "
                 f"{registry_result['alias']} redirecting, {registry_result['ended']} ended")
    except CorruptJSONError:
        raise
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        log(f"  ⚠️  slug registry not updated ({exc}) — retired URLs may 404")

    odd_hours = art["odd_hours"]
    if odd_hours:
        log(f"  ⏰ {len(odd_hours)} event(s) start in the small hours — check for a "
             f"timezone conversion bug before trusting these:")
        for e, h in odd_hours[:5]:
            log(f"       - {e.get('name', '?')[:52]} starts {h}:00 AM Boston time")

    return {
        "status": "published",
        "count": len(deduped),
        "implausible_start_hours": [
            {"id": e.get("id"), "name": e.get("name"), "hour": h} for e, h in odd_hours
        ],
        "retired_urls": (registry_result["alias"] + registry_result["ended"]) if registry_result else None,
        "archived_count": len(art["archived_out"]),
        "search_only_count": len(art["searchonly_out"]),
        "venue_suppressed_count": len(venue_report.get("suppressed", [])),
        "venue_conflict_count": len(venue_report.get("conflicts", [])),
        "series_rolled_forward": len(art["rolled"]),
        "path": str(paths.PUBLIC_EVENTS_JSON),
    }


def publish() -> dict:
    """Generate events-published.json from active + archived events + expanded venues."""
    with storage.store_lock():
        return _commit_publish(_compute_publish())


# Refuse to ship a published file whose live-event count collapsed relative to a
# baseline — a broken scrape or an over-zealous review pass must never wipe the
# map. Shared by the deterministic pipeline and the agent's own publish.
TRIPWIRE_MIN_PREVIOUS = 20
TRIPWIRE_MIN_RATIO = 0.7


def live_event_count(text: Optional[str]) -> int:
    if not text:
        return 0
    try:
        return sum(1 for e in json.loads(text) if not e.get("archived"))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0


def publish_guarded(previous_snapshot: Optional[str] = None) -> dict:
    """publish(), unless the live-event count would collapse below
    ``TRIPWIRE_MIN_RATIO`` of the baseline — then write nothing and report
    ``tripped: True``.

    The check runs on the computed result *before* any file is touched, so a
    tripped publish leaves the published JSON, the venue-conflict queue and the
    slug registry exactly as they were. (Restoring after the fact used to leave
    the registry with URLs retired for a publish that was then rolled back.)

    Baseline defaults to the current published file — the right reference for
    the agent's own publish, which runs after the deterministic refresh already
    published. Callers holding an earlier baseline (run_pipeline, which snapshots
    before scrape/ingest/archive) pass it in explicitly.
    """
    with storage.store_lock():
        if previous_snapshot is None:
            previous_snapshot = (
                paths.PUBLIC_EVENTS_JSON.read_text() if paths.PUBLIC_EVENTS_JSON.exists() else None
            )
        previous_live = live_event_count(previous_snapshot)

        art = _compute_publish()
        new_live = sum(1 for e in art["published"] if not e.get("archived"))
        tripped = (
            previous_live >= TRIPWIRE_MIN_PREVIOUS
            and new_live < previous_live * TRIPWIRE_MIN_RATIO
        )
        if tripped:
            message = (
                f"live events would fall {previous_live} → {new_live} "
                f"(below {int(TRIPWIRE_MIN_RATIO * 100)}% of baseline); nothing was "
                "written — do NOT commit. Investigate first."
            )
            log(f"  🚨 tripwire: {message}")
            return {
                "status": "tripwire",
                "tripped": True,
                "message": message,
                "count": len(art["deduped"]),
                "previous_live_events": previous_live,
                "published_live_events": new_live,
                "venue_suppressed_count": len(art["venue_report"].get("suppressed", [])),
                "venue_conflict_count": len(art["venue_report"].get("conflicts", [])),
                "path": str(paths.PUBLIC_EVENTS_JSON),
            }

        result = _commit_publish(art)
        result["tripped"] = False
        result["previous_live_events"] = previous_live
        result["published_live_events"] = new_live
        return result
