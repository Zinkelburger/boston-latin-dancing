#!/usr/bin/env python3
"""Read-only release preflight for the Boston Latin Dance event pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import atomic_io
from dedup_report import report as duplicate_report
from event_store import (
    PUBLIC_EVENTS_JSON,
    TRIPWIRE_MIN_PREVIOUS,
    TRIPWIRE_MIN_RATIO,
    _live_event_count,
    load_active,
    load_pending,
    load_rejected,
    preview_publish,
)
from scrape_facebook import raw_input_path, validate_capture
from scraper_utils import ROOT, load_scrape_health, load_sources, scraper_commands
from verify_events import REPORT_PATH

LEGACY_PUBLIC_EVENTS_JSON = ROOT / "public" / "events.json"
GOOD_VERIFICATION_STATUSES = {"confirmed", "reachable_only"}


def _parse_aware(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _item(event: dict) -> dict:
    return {
        "id": event.get("id"),
        "name": event.get("name", ""),
        "source": event.get("source", ""),
    }


def _artifacts_equivalent(stored: object, preview: list[dict]) -> bool:
    """Compare artifacts while allowing publish-only geocoding of archives.

    The read-only preview intentionally does not call the geocoder. Historical
    rows that lack stored coordinates can therefore have coordinates in the
    last generated artifact and ``None`` in the preview; that alone is not
    meaningful drift.
    """
    if not isinstance(stored, list) or len(stored) != len(preview):
        return False
    normalized: list[dict] = []
    for old, proposed in zip(stored, preview):
        if not isinstance(old, dict) or old.get("id") != proposed.get("id"):
            return False
        old_copy = dict(old)
        for key in ("lat", "lng"):
            if proposed.get(key) is None:
                old_copy[key] = None
        normalized.append(old_copy)
    return normalized == preview


def run_doctor(
    *,
    health_max_age_hours: int = 48,
    facebook_max_age_days: int = 14,
    verification_max_age_days: int = 14,
    now: datetime | None = None,
    include_publish_preview: bool = True,
) -> dict:
    """Return a structured, non-mutating pipeline health report."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_utc = now.astimezone(timezone.utc)

    checks: dict[str, dict] = {}
    blockers: list[dict] = []
    warnings: list[dict] = []

    def record(name: str, status: str, message: str, items: list | None = None, **extra) -> None:
        row = {"status": status, "message": message, "count": len(items or [])}
        if items:
            row["items"] = items
        row.update(extra)
        checks[name] = row
        if status == "blocker":
            blockers.append({"check": name, "message": message, "items": items or []})
        elif status == "warning":
            warnings.append({"check": name, "message": message, "items": items or []})

    sources = load_sources()
    health = load_scrape_health()
    runnable_ids = [source_id for source_id, _ in scraper_commands()]
    health_issues: list[dict] = []
    for source_id in runnable_ids:
        entry = health.get(source_id)
        if not isinstance(entry, dict):
            health_issues.append({"source_id": source_id, "problem": "no health record"})
            continue
        status = entry.get("status")
        if status in ("structure_missing", "fetch_error"):
            health_issues.append({
                "source_id": source_id,
                "problem": status,
                "note": entry.get("note", ""),
            })
        last_run = _parse_aware(entry.get("last_run"))
        if last_run is None:
            health_issues.append({"source_id": source_id, "problem": "invalid last_run"})
        elif now_utc - last_run.astimezone(timezone.utc) > timedelta(hours=health_max_age_hours):
            health_issues.append({
                "source_id": source_id,
                "problem": "stale health",
                "last_run": last_run.isoformat(),
            })
    record(
        "scraper_health",
        "blocker" if health_issues else "ok",
        "Refresh or repair every enabled scraper with missing, failed, structurally empty, or stale health."
        if health_issues else f"All {len(runnable_ids)} runnable scrapers have recent non-failing health.",
        health_issues,
    )

    facebook_issues: list[dict] = []
    facebook_ok: list[dict] = []
    facebook_sources = [
        source for source in sources
        if source.get("enabled") and source.get("type") == "facebook"
    ]
    for source in facebook_sources:
        source_id = source["id"]
        path = raw_input_path(source_id)
        if not path.exists():
            facebook_issues.append({"source_id": source_id, "problem": "missing evidence envelope"})
            continue
        try:
            capture = atomic_io.read_json(path)
            if not isinstance(capture, dict):
                raise ValueError("legacy capture is not an evidence envelope")
            status, events, checked_at = validate_capture(
                capture,
                source,
                now=now_utc,
                max_age_days=facebook_max_age_days,
            )
            facebook_ok.append({
                "source_id": source_id,
                "status": status,
                "checked_at": checked_at.isoformat(),
                "events": len(events),
            })
        except Exception as exc:  # malformed evidence is a release blocker
            facebook_issues.append({"source_id": source_id, "problem": str(exc)})
    record(
        "facebook_evidence",
        "blocker" if facebook_issues else "ok",
        "Re-check each listed Facebook page in a browser and save a fresh evidence envelope."
        if facebook_issues else f"All {len(facebook_ok)} enabled Facebook sources have fresh browser evidence.",
        facebook_issues,
        evidence=facebook_ok,
    )

    active = load_active()
    pending = load_pending()
    rejected = load_rejected()
    record(
        "pending_review",
        "blocker" if pending else "ok",
        "Resolve every pending event before publishing." if pending else "Pending review queue is empty.",
        [_item(event) for event in pending],
    )
    record(
        "rejected_audit",
        "warning" if rejected else "ok",
        "Review rejected entries for new or unexplained decisions." if rejected else "Rejected audit queue is empty.",
        [_item(event) for event in rejected],
    )

    missing_coords = [event for event in active if event.get("lat") is None or event.get("lng") is None]
    record(
        "coordinates",
        "blocker" if missing_coords else "ok",
        "Geocode or explicitly locate every active event." if missing_coords else "Every active event has coordinates.",
        [_item(event) | {"location": event.get("location", "")} for event in missing_coords],
    )

    verification = atomic_io.read_json(REPORT_PATH, default=[])
    if not isinstance(verification, list):
        verification = []
    verification_by_id = {row.get("event_id"): row for row in verification if isinstance(row, dict)}
    verification_issues: list[dict] = []
    reachable_only: list[dict] = []
    for event in active:
        row = verification_by_id.get(event.get("id"))
        if row is None:
            verification_issues.append(_item(event) | {"problem": "missing verification"})
            continue
        if row.get("status") not in GOOD_VERIFICATION_STATUSES:
            verification_issues.append(_item(event) | {
                "problem": row.get("status", "unknown status"),
                "notes": row.get("notes", ""),
            })
        verified_at = _parse_aware(row.get("verified_at"))
        if verified_at is None:
            verification_issues.append(_item(event) | {"problem": "invalid verified_at"})
        elif now_utc - verified_at.astimezone(timezone.utc) > timedelta(days=verification_max_age_days):
            verification_issues.append(_item(event) | {
                "problem": "stale verification",
                "verified_at": verified_at.isoformat(),
            })
        if row.get("status") == "reachable_only":
            reachable_only.append(_item(event))
    record(
        "verification",
        "blocker" if verification_issues else ("warning" if reachable_only else "ok"),
        "Verify or browser-attest every listed event."
        if verification_issues else (
            "Some URLs were reachable but their event facts were not machine-verifiable."
            if reachable_only else "Every active event has fresh confirmed verification."
        ),
        verification_issues if verification_issues else reachable_only,
        reachable_only_count=len(reachable_only),
    )

    duplicates = duplicate_report(active)
    record(
        "active_duplicates",
        "blocker" if duplicates else "ok",
        "Resolve suspicious active-event pairs before publishing."
        if duplicates else "No suspicious active-event pairs found.",
        duplicates,
    )

    if include_publish_preview:
        preview = preview_publish()
        conflicts = preview["venue_report"].get("conflicts", [])
        record(
            "venue_conflicts",
            "blocker" if conflicts else "ok",
            "Resolve recurring-venue conflicts before publishing."
            if conflicts else "No unresolved recurring-venue conflicts.",
            conflicts,
        )
        preview_missing = [_item(event) for event in preview["missing"]]
        record(
            "publish_coordinates",
            "blocker" if preview_missing else "ok",
            "The publish preview contains records without coordinates."
            if preview_missing else "Publish preview has coordinates for all live pins.",
            preview_missing,
        )
        odd_hours = [
            _item(event) | {"hour": hour, "startDate": event.get("startDate", "")}
            for event, hour in preview["odd_hours"]
        ]
        record(
            "start_hours",
            "blocker" if odd_hours else "ok",
            "Correct likely timezone errors for implausible start hours."
            if odd_hours else "No implausible live-event start hours.",
            odd_hours,
        )

        previous_text = PUBLIC_EVENTS_JSON.read_text() if PUBLIC_EVENTS_JSON.exists() else None
        previous_live = _live_event_count(previous_text)
        new_live = sum(1 for event in preview["published"] if not event.get("archived"))
        tripped = previous_live >= TRIPWIRE_MIN_PREVIOUS and new_live < previous_live * TRIPWIRE_MIN_RATIO
        record(
            "publish_tripwire",
            "blocker" if tripped else "ok",
            f"Publish would drop live events {previous_live} → {new_live}; investigate before publishing."
            if tripped else f"Publish count {previous_live} → {new_live} is within the safety threshold.",
            [{"previous_live": previous_live, "new_live": new_live}] if tripped else [],
        )

        drift: list[dict] = []
        for label, path in (
            ("data", PUBLIC_EVENTS_JSON),
            ("public", LEGACY_PUBLIC_EVENTS_JSON),
        ):
            try:
                stored = atomic_io.read_json(path)
            except FileNotFoundError:
                stored = None
            if not _artifacts_equivalent(stored, preview["published"]):
                drift.append({"artifact": label, "path": str(path)})
        record(
            "generated_artifacts",
            "warning" if drift else "ok",
            "Generated event artifacts differ from the current publish preview; publish after blockers are resolved."
            if drift else "Generated event artifacts match the current publish preview.",
            drift,
        )

    return {
        "status": "blocked" if blockers else "healthy",
        "ok": not blockers,
        "generated_at": now_utc.isoformat(),
        "summary": {
            "active_events": len(active),
            "blockers": len(blockers),
            "warnings": len(warnings),
        },
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-max-age-hours", type=int, default=48)
    parser.add_argument("--facebook-max-age-days", type=int, default=14)
    parser.add_argument("--verification-max-age-days", type=int, default=14)
    parser.add_argument("--no-publish-preview", action="store_true")
    args = parser.parse_args(argv)
    result = run_doctor(
        health_max_age_hours=args.health_max_age_hours,
        facebook_max_age_days=args.facebook_max_age_days,
        verification_max_age_days=args.verification_max_age_days,
        include_publish_preview=not args.no_publish_preview,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
