#!/usr/bin/env python3
"""
Scan events for suspicious near-duplicates, or list pending review items.

Does NOT modify any files — read-only analysis tool.

Usage:
    python3 scripts/dedup_report.py              # scan published events
    python3 scripts/dedup_report.py --active     # scan data/events/active.json
    python3 scripts/dedup_report.py --pending    # list pending dedup review queue
    python3 scripts/dedup_report.py --json         # machine-readable output
    python3 scripts/dedup_report.py --log          # also print recent dedup log entries
"""

import json
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_io
from event_store import (
    normalize_name,
    _content_words,
    _locations_same,
    dedup_confidence,
    DEDUP_LOG,
    ACTIVE_JSON,
    PENDING_JSON,
    PUBLIC_EVENTS_JSON,
    load_active,
    load_pending,
)


def load_events(path: Path) -> list[dict]:
    return atomic_io.read_json(path, default=[])


def report(events: list[dict]) -> list[dict]:
    """Find suspicious pairs and return them sorted by confidence."""
    pairs = []
    for i, j in combinations(range(len(events)), 2):
        a, b = events[i], events[j]
        conf = dedup_confidence(a, b)
        if conf is None:
            continue

        name_a = normalize_name(a.get("name", ""))
        name_b = normalize_name(b.get("name", ""))
        words_a = _content_words(name_a)
        words_b = _content_words(name_b)
        overlap = words_a & words_b

        pairs.append({
            "confidence": conf,
            "a_id": a["id"],
            "a_name": a.get("name", ""),
            "b_id": b["id"],
            "b_name": b.get("name", ""),
            "a_date": a.get("startDate", "")[:10],
            "b_date": b.get("startDate", "")[:10],
            "a_loc": a.get("location") or "",
            "b_loc": b.get("location") or "",
            "shared_words": sorted(overlap) if overlap else [],
            "same_location": _locations_same(a, b),
        })

    conf_order = {"certain": 0, "review": 1}
    pairs.sort(key=lambda p: conf_order.get(p["confidence"], 2))
    return pairs


def pending_report() -> list[dict]:
    """Return pending dedup review items with their matched active events."""
    pending = load_pending()
    active = {e["id"]: e for e in load_active()}
    items = []
    for ev in pending:
        candidate_id = ev.get("_dedup_candidate_of")
        existing = active.get(candidate_id) if candidate_id else None
        items.append({
            "id": ev["id"],
            "name": ev.get("name", ""),
            "startDate": ev.get("startDate", ""),
            "location": ev.get("location") or "",
            "confidence": ev.get("_dedup_confidence", "review"),
            "reason": ev.get("_dedup_reason", ""),
            "candidate_id": candidate_id,
            "candidate_name": existing.get("name", "") if existing else None,
            "candidate_date": existing.get("startDate", "")[:10] if existing else None,
            "candidate_location": existing.get("location") or "" if existing else None,
        })
    return items


def print_report(pairs: list[dict]) -> None:
    if not pairs:
        print("No suspicious pairs found.")
        return

    for tier in ("certain", "review"):
        tier_pairs = [p for p in pairs if p["confidence"] == tier]
        if not tier_pairs:
            continue

        label = {"certain": "CERTAIN (auto-merge)", "review": "REVIEW (needs human/agent review)"}
        print(f"\n{'='*70}")
        print(f"  {label[tier]}  ({len(tier_pairs)} pairs)")
        print(f"{'='*70}")

        for p in tier_pairs:
            print(f"\n  [{p['a_id'][:16]}] {p['a_name'][:60]}")
            print(f"  [{p['b_id'][:16]}] {p['b_name'][:60]}")
            print(f"  Dates: {p['a_date']} vs {p['b_date']}  |  Same location: {p['same_location']}")
            if p["a_loc"] or p["b_loc"]:
                print(f"  Locs:  \"{p['a_loc'][:50]}\" vs \"{p['b_loc'][:50]}\"")
            if p["shared_words"]:
                print(f"  Shared words: {', '.join(p['shared_words'])}")

    totals = {}
    for p in pairs:
        totals[p["confidence"]] = totals.get(p["confidence"], 0) + 1
    print(f"\n--- Summary: {totals} ---\n")


def print_pending(items: list[dict]) -> None:
    if not items:
        print("No pending dedup review items.")
        return

    print(f"\n{'='*70}")
    print(f"  PENDING DEDUP REVIEW  ({len(items)} items)")
    print(f"{'='*70}")

    for item in items:
        print(f"\n  NEW:      [{item['id'][:16]}] {item['name'][:60]}")
        print(f"            {item['startDate'][:10]}  |  {item['location'][:50]}")
        if item["candidate_id"]:
            print(f"  MATCHES:  [{item['candidate_id'][:16]}] {item['candidate_name'][:60]}")
            print(f"            {item['candidate_date']}  |  {item['candidate_location'][:50]}")
        print(f"  Reason:   {item['reason']}")


def print_log(n: int = 20) -> None:
    if not DEDUP_LOG.exists():
        print("\nNo dedup log found yet.")
        return
    lines = DEDUP_LOG.read_text().strip().split("\n")
    recent = lines[-n:]
    print(f"\n{'='*70}")
    print(f"  Recent dedup log entries (last {len(recent)})")
    print(f"{'='*70}")
    for line in recent:
        entry = json.loads(line)
        print(f"  [{entry['confidence']:>7}] {entry['action']:<10}  {entry['kept_name'][:40]:<40}  <->  {entry['candidate_name'][:40]}")
        print(f"           reason: {entry['reason']}")


def main():
    args = sys.argv[1:]
    show_log = "--log" in args
    use_json = "--json" in args
    use_pending = "--pending" in args
    use_active = "--active" in args

    if use_pending:
        items = pending_report()
        if use_json:
            print(json.dumps({"count": len(items), "items": items}, indent=2))
        else:
            print_pending(items)
        if show_log:
            print_log()
        return

    path = ACTIVE_JSON if use_active else PUBLIC_EVENTS_JSON
    label = "active" if use_active else "published"
    if not use_json:
        print(f"Scanning {label} events from {path}...")

    events = load_events(path)
    if not use_json:
        print(f"Loaded {len(events)} events")

    pairs = report(events)
    if use_json:
        print(json.dumps({"count": len(pairs), "pairs": pairs}, indent=2))
    else:
        print_report(pairs)

    if show_log:
        print_log()


if __name__ == "__main__":
    main()
