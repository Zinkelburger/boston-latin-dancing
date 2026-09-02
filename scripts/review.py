#!/usr/bin/env python3
"""CLI tool for reviewing and resolving pending duplicate events.

Usage:
  python scripts/review.py list              — show all pending events and their conflicts
  python scripts/review.py show <id>         — side-by-side comparison with its active conflict
  python scripts/review.py merge <id>        — merge pending event's details into the active one
  python scripts/review.py add <id>          — force-add pending event to active (not a duplicate)
  python scripts/review.py dismiss <id>      — discard from pending (true duplicate, no new info)
  python scripts/review.py mark-different <id_a> <id_b>  — record these are different events
  python scripts/review.py audit             — scan active.json for internal duplicates
"""

import sys
import json
from pathlib import Path
from textwrap import fill, indent

sys.path.insert(0, str(Path(__file__).resolve().parent))
from event_store import (
    load_active, save_active, load_pending, save_pending,
    merge_event, _persist_known_duplicate, _append_changelog,
    _enrich_event, dedup_confidence, find_duplicate_in, _dedup_reason,
    store_lock,
)


def _short(text: str, width: int = 60) -> str:
    if not text:
        return "(empty)"
    text = text.replace("\n", " ")
    return text[:width] + "..." if len(text) > width else text


def _format_event(ev: dict, label: str = "") -> str:
    lines = []
    if label:
        lines.append(f"  [{label}]")
    lines.append(f"  ID:       {ev.get('id', '?')}")
    lines.append(f"  Name:     {ev.get('name', '?')}")
    lines.append(f"  Date:     {ev.get('startDate', '?')}")
    lines.append(f"  Location: {_short(ev.get('location', ''))}")
    lines.append(f"  Cost:     {ev.get('cost') or '(none)'}")
    lines.append(f"  Source:   {ev.get('source', '(unknown)')}")
    lines.append(f"  URL:      {ev.get('url') or '(none)'}")
    desc = ev.get("description", "")
    if desc:
        lines.append(f"  Desc:     {_short(desc, 100)}")
    return "\n".join(lines)


def cmd_list():
    pending = load_pending()
    if not pending:
        print("No pending events. All clear!")
        return

    active = load_active()
    active_by_id = {e["id"]: e for e in active}

    print(f"\n{'='*60}")
    print(f"  PENDING REVIEW: {len(pending)} event(s)")
    print(f"{'='*60}\n")

    for i, ev in enumerate(pending, 1):
        conflict_id = ev.get("_dedup_candidate_of")
        reason = ev.get("_dedup_reason", "?")
        confidence = ev.get("_dedup_confidence", "?")

        print(f"--- #{i} [{confidence}] reason: {reason} ---")
        print(_format_event(ev, "PENDING"))
        print()

        if conflict_id and conflict_id in active_by_id:
            print(_format_event(active_by_id[conflict_id], "CONFLICTS WITH (active)"))
        elif conflict_id:
            print(f"  [Conflict target {conflict_id} not found in active]")
        print()


def cmd_show(event_id: str):
    pending = load_pending()
    ev = next((e for e in pending if e["id"] == event_id), None)
    if not ev:
        print(f"Event '{event_id}' not found in pending.json")
        print("Hint: use 'python scripts/review.py list' to see available IDs")
        sys.exit(1)

    active = load_active()
    conflict_id = ev.get("_dedup_candidate_of")
    conflict = next((e for e in active if e["id"] == conflict_id), None) if conflict_id else None

    print(f"\n{'='*60}")
    print("  SIDE-BY-SIDE COMPARISON")
    print(f"{'='*60}\n")

    fields = ["name", "startDate", "endDate", "location", "cost", "url", "styles", "source", "description"]
    col_w = 45

    print(f"  {'PENDING':<{col_w}} | {'ACTIVE (conflict)':<{col_w}}")
    print(f"  {'-'*col_w}-+-{'-'*col_w}")

    for field in fields:
        pval = str(ev.get(field, ""))[:col_w]
        aval = str(conflict.get(field, ""))[:col_w] if conflict else "(no conflict)"
        marker = " " if pval == aval else "*"
        print(f"{marker} {field:<12} {pval:<{col_w}} | {aval:<{col_w}}")

    print(f"\n  Reason: {ev.get('_dedup_reason', '?')}")
    print(f"  Confidence: {ev.get('_dedup_confidence', '?')}")


def cmd_merge(event_id: str):
    with store_lock():
        _cmd_merge(event_id)


def _cmd_merge(event_id: str):
    pending = load_pending()
    ev_idx = next((i for i, e in enumerate(pending) if e["id"] == event_id), None)
    if ev_idx is None:
        print(f"Event '{event_id}' not found in pending.json")
        sys.exit(1)

    ev = pending[ev_idx]
    conflict_id = ev.get("_dedup_candidate_of")

    active = load_active()
    active_idx = next((i for i, e in enumerate(active) if e["id"] == conflict_id), None) if conflict_id else None

    if active_idx is None:
        print(f"Conflict target '{conflict_id}' not found in active.json")
        print("Use 'add' instead to force-add this event.")
        sys.exit(1)

    # Clean dedup metadata before merging
    for key in ("_dedup_candidate_of", "_dedup_confidence", "_dedup_reason"):
        ev.pop(key, None)

    merged = merge_event(active[active_idx], ev)
    _enrich_event(merged)
    active[active_idx] = merged
    save_active(active)

    # Record as known duplicate
    _persist_known_duplicate(conflict_id, event_id, "same")

    # Remove from pending
    pending.pop(ev_idx)
    save_pending(pending)

    _append_changelog("merge", event_id, f"merged into {conflict_id} via review CLI")

    print(f"Merged '{ev['name']}' into active event '{merged['name']}'")
    print(f"Recorded as known duplicate. Removed from pending.")


def cmd_add(event_id: str):
    with store_lock():
        _cmd_add(event_id)


def _cmd_add(event_id: str):
    pending = load_pending()
    ev_idx = next((i for i, e in enumerate(pending) if e["id"] == event_id), None)
    if ev_idx is None:
        print(f"Event '{event_id}' not found in pending.json")
        sys.exit(1)

    ev = pending[ev_idx]
    conflict_id = ev.get("_dedup_candidate_of")

    # Clean dedup metadata
    for key in ("_dedup_candidate_of", "_dedup_confidence", "_dedup_reason"):
        ev.pop(key, None)

    # Record as known different so future dedup skips them
    if conflict_id:
        _persist_known_duplicate(conflict_id, event_id, "different")

    # Add to active
    _enrich_event(ev)
    active = load_active()
    active.append(ev)
    save_active(active)

    # Remove from pending
    pending.pop(ev_idx)
    save_pending(pending)

    _append_changelog("add", event_id, "force-added via review CLI (not a duplicate)")

    print(f"Added '{ev['name']}' to active.json")
    if conflict_id:
        print(f"Recorded as different from '{conflict_id}' — won't be flagged again.")


def cmd_dismiss(event_id: str):
    with store_lock():
        _cmd_dismiss(event_id)


def _cmd_dismiss(event_id: str):
    pending = load_pending()
    ev_idx = next((i for i, e in enumerate(pending) if e["id"] == event_id), None)
    if ev_idx is None:
        print(f"Event '{event_id}' not found in pending.json")
        sys.exit(1)

    ev = pending[ev_idx]
    conflict_id = ev.get("_dedup_candidate_of")

    # Record as known duplicate so future scrapes auto-merge
    if conflict_id:
        _persist_known_duplicate(conflict_id, event_id, "same")

    # Remove from pending
    pending.pop(ev_idx)
    save_pending(pending)

    _append_changelog("dismiss", event_id, "dismissed via review CLI (duplicate, no new info)")

    print(f"Dismissed '{ev['name']}' from pending.")
    if conflict_id:
        print(f"Recorded as same as '{conflict_id}' — will auto-merge in future.")


def cmd_mark_different(id_a: str, id_b: str):
    _persist_known_duplicate(id_a, id_b, "different")
    print(f"Recorded '{id_a}' and '{id_b}' as DIFFERENT events.")
    print("Future dedup will skip this pair.")


def cmd_audit():
    """Scan active.json for internal duplicates that slipped in."""
    active = load_active()
    print(f"\nAuditing {len(active)} active events for duplicates...\n")

    found = []
    checked = set()

    for i, ev_a in enumerate(active):
        for j, ev_b in enumerate(active):
            if j <= i:
                continue
            pair_key = (ev_a["id"], ev_b["id"])
            if pair_key in checked:
                continue
            checked.add(pair_key)

            conf = dedup_confidence(ev_a, ev_b)
            if conf is not None:
                reason = _dedup_reason(ev_a, ev_b, conf)
                found.append((ev_a, ev_b, conf, reason))

    if not found:
        print("No duplicates found in active.json. All clean!")
        return

    print(f"Found {len(found)} potential duplicate pair(s):\n")
    for ev_a, ev_b, conf, reason in found:
        print(f"  [{conf}] {reason}")
        print(f"    A: {ev_a['name'][:60]}")
        print(f"       id={ev_a['id']}")
        print(f"    B: {ev_b['name'][:60]}")
        print(f"       id={ev_b['id']}")
        print()

    print("To resolve: merge one into the other or mark them as different.")
    print("  python scripts/review.py mark-different <id_a> <id_b>")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        cmd_list()
    elif cmd == "show" and len(sys.argv) >= 3:
        cmd_show(sys.argv[2])
    elif cmd == "merge" and len(sys.argv) >= 3:
        cmd_merge(sys.argv[2])
    elif cmd == "add" and len(sys.argv) >= 3:
        cmd_add(sys.argv[2])
    elif cmd == "dismiss" and len(sys.argv) >= 3:
        cmd_dismiss(sys.argv[2])
    elif cmd == "mark-different" and len(sys.argv) >= 4:
        cmd_mark_different(sys.argv[2], sys.argv[3])
    elif cmd == "audit":
        cmd_audit()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
