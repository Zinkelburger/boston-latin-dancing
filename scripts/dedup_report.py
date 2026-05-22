#!/usr/bin/env python3
"""
Scan published events.json for suspicious near-duplicates.

Outputs a readable report of pairs that look like they might be the same event.
Does NOT modify any files — read-only analysis tool.

Usage:
    python3 scripts/dedup_report.py              # scan public/events.json
    python3 scripts/dedup_report.py --active      # scan data/events/active.json
    python3 scripts/dedup_report.py --log         # also print recent dedup log entries
"""

import json
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from event_store import (
    normalize_name,
    _content_words,
    _locations_same,
    _dates_within,
    dedup_confidence,
    DEDUP_LOG,
    ACTIVE_JSON,
    PUBLIC_EVENTS_JSON,
)

def load_events(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def report(events: list[dict]) -> list[dict]:
    """Find suspicious pairs and return them sorted by confidence."""
    pairs = []
    for i, j in combinations(range(len(events)), 2):
        a, b = events[i], events[j]
        conf = dedup_confidence(a, b)
        if conf is None:
            continue

        name_a = normalize_name(a["name"])
        name_b = normalize_name(b["name"])
        words_a = _content_words(name_a)
        words_b = _content_words(name_b)
        overlap = words_a & words_b

        pairs.append({
            "confidence": conf,
            "a_id": a["id"][:16],
            "a_name": a["name"][:60],
            "b_id": b["id"][:16],
            "b_name": b["name"][:60],
            "a_date": a.get("startDate", "")[:10],
            "b_date": b.get("startDate", "")[:10],
            "a_loc": (a.get("location") or "")[:50],
            "b_loc": (b.get("location") or "")[:50],
            "shared_words": sorted(overlap) if overlap else [],
            "same_location": _locations_same(a, b),
        })

    conf_order = {"certain": 0, "likely": 1, "uncertain": 2}
    pairs.sort(key=lambda p: conf_order.get(p["confidence"], 3))
    return pairs


def print_report(pairs: list[dict]) -> None:
    if not pairs:
        print("No suspicious pairs found.")
        return

    for tier in ("certain", "likely", "uncertain"):
        tier_pairs = [p for p in pairs if p["confidence"] == tier]
        if not tier_pairs:
            continue

        label = {"certain": "CERTAIN (auto-merged)", "likely": "LIKELY (auto-merged, logged)", "uncertain": "UNCERTAIN (needs review)"}
        print(f"\n{'='*70}")
        print(f"  {label[tier]}  ({len(tier_pairs)} pairs)")
        print(f"{'='*70}")

        for p in tier_pairs:
            print(f"\n  [{p['a_id']}] {p['a_name']}")
            print(f"  [{p['b_id']}] {p['b_name']}")
            print(f"  Dates: {p['a_date']} vs {p['b_date']}  |  Same location: {p['same_location']}")
            if p["a_loc"] or p["b_loc"]:
                print(f"  Locs:  \"{p['a_loc']}\" vs \"{p['b_loc']}\"")
            if p["shared_words"]:
                print(f"  Shared words: {', '.join(p['shared_words'])}")

    totals = {}
    for p in pairs:
        totals[p["confidence"]] = totals.get(p["confidence"], 0) + 1
    print(f"\n--- Summary: {totals} ---\n")


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
        print(f"  [{entry['confidence']:>9}] {entry['action']:<16}  {entry['kept_name'][:40]:<40}  <->  {entry['candidate_name'][:40]}")
        print(f"             reason: {entry['reason']}")


def main():
    show_log = "--log" in sys.argv
    use_active = "--active" in sys.argv

    path = ACTIVE_JSON if use_active else PUBLIC_EVENTS_JSON
    label = "active" if use_active else "published"
    print(f"Scanning {label} events from {path}...")

    events = load_events(path)
    print(f"Loaded {len(events)} events")

    pairs = report(events)
    print_report(pairs)

    if show_log:
        print_log()


if __name__ == "__main__":
    main()
