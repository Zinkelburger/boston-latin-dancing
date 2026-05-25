#!/usr/bin/env python3
"""
List uncertain dedup pairs for review by a Cursor/Claude Code agent via MCP tools.

No external API keys required — the agent reads this output and calls MCP tools
(event_get, event_approve, event_reject, event_add) to resolve each pair.

Sources of uncertain pairs:
  1. data/events/pending.json — events flagged during ingest with _dedup_candidate_of
  2. Active + venue-expanded events — pairs where dedup_confidence() == "uncertain"

Usage:
    npm run review-dedup                       # JSON for agent consumption (default)
    python3 scripts/review_dedup.py            # same as above
    python3 scripts/review_dedup.py --text     # human-readable terminal output

Agent workflow:
    1. Run this script (or npm run review-dedup)
    2. For each pair_index, call event_get on both event IDs
    3. Decide same real-world event vs distinct:
         pending origin  → event_approve(pending_id)  OR  event_reject(pending_id, reason=...)
         active_scan     → event_add(..., force=True) on loser  OR  no action if distinct
    4. Re-run to confirm count is 0

MCP tools (boston-latin-dance server):
    event_list(status='pending')   — browse pending queue
    event_get(event_id)            — full event details before deciding
    event_approve(event_id)        — merge pending duplicate into active store
    event_reject(event_id, reason) — reject pending as distinct event
    event_add(..., force=True)     — force-merge two active records
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from event_store import (
    _dedup_reason,
    _locations_same,
    dedup_confidence,
    expand_venues,
    load_active,
    load_pending,
    pick_winner,
)

MCP_TOOLS = [
    "event_list",
    "event_get",
    "event_approve",
    "event_reject",
    "event_add",
]

AGENT_INSTRUCTIONS = (
    "For each pair: (1) event_get both IDs, (2) decide same event vs distinct, "
    "(3) call the matching branch in mcp_actions. Re-run review-dedup when done."
)


def _event_brief(ev: dict) -> dict:
    return {
        "id": ev["id"],
        "name": ev["name"],
        "startDate": ev.get("startDate", ""),
        "location": ev.get("location", ""),
        "source": ev.get("source", ""),
        "url": ev.get("url", ""),
        "has_schedule": bool(ev.get("schedule")),
    }


def _review_hint(a: dict, b: dict) -> str:
    a_sched = bool(a.get("schedule"))
    b_sched = bool(b.get("schedule"))
    if a_sched != b_sched:
        return (
            "One record is a venue schedule hub — usually distinct from a scraped "
            "night-specific listing unless they represent the same recurring night."
        )
    if not _locations_same(a, b):
        return (
            "Different locations — likely distinct events unless a venue alias is "
            "missing from LOCATION_ALIASES in event_store.py."
        )
    return (
        "Compare names, dates, URLs, and sources. Prefer keeping the venue schedule "
        "hub or higher-priority source when merging."
    )


def _review_steps(a_id: str, b_id: str) -> list[dict]:
    return [
        {"tool": "event_get", "args": {"event_id": a_id}},
        {"tool": "event_get", "args": {"event_id": b_id}},
    ]


def _mcp_actions(origin: str, a: dict, b: dict, pending_id: str | None = None) -> dict:
    review = _review_steps(a["id"], b["id"])

    if origin == "pending":
        pid = pending_id or b["id"]
        return {
            "review_steps": review,
            "if_same_event": {
                "tool": "event_approve",
                "args": {"event_id": pid},
                "description": "Pending item duplicates the existing active event — approve to merge.",
            },
            "if_distinct": {
                "tool": "event_reject",
                "args": {
                    "event_id": pid,
                    "reason": "distinct event (customize reason after review)",
                },
                "description": "Different real-world events — reject pending, then re-add if needed.",
            },
        }

    winner, loser = pick_winner(a, b)
    loser_brief = _event_brief(loser)
    return {
        "review_steps": review,
        "if_same_event": {
            "tool": "event_add",
            "args": {
                "name": loser["name"],
                "start_date": loser.get("startDate", ""),
                "location": loser.get("location", ""),
                "end_date": loser.get("endDate"),
                "description": loser.get("description", ""),
                "url": loser.get("url"),
                "styles": ",".join(loser.get("styles", [])) or None,
                "cost": loser.get("cost"),
                "recurring": loser.get("recurring", False),
                "source": loser.get("source", "manual"),
                "event_id": loser["id"],
                "force": True,
            },
            "keep_event_id": winner["id"],
            "merge_event_id": loser["id"],
            "description": (
                f"Force-merge {loser_brief['id'][:16]} into kept record "
                f"{winner['id'][:16]} (higher source precedence)."
            ),
        },
        "if_distinct": {
            "tool": None,
            "args": {},
            "description": "No store change — leave both active listings as separate events.",
        },
    }


def _pair_record(
    pair_index: int,
    a: dict,
    b: dict,
    confidence: str,
    origin: str,
    pending_id: str | None = None,
    existing_id: str | None = None,
) -> dict:
    record = {
        "pair_index": pair_index,
        "confidence": confidence,
        "origin": origin,
        "reason": _dedup_reason(a, b, confidence),
        "hint": _review_hint(a, b),
        "a": _event_brief(a),
        "b": _event_brief(b),
        "mcp_actions": _mcp_actions(origin, a, b, pending_id=pending_id),
    }
    if pending_id:
        record["pending_id"] = pending_id
    if existing_id:
        record["existing_id"] = existing_id
    return record


def collect_pairs() -> list[dict]:
    pairs: list[dict] = []
    pair_index = 0

    for ev in load_pending():
        candidate_of = ev.get("_dedup_candidate_of")
        if not candidate_of:
            continue
        existing = next((e for e in load_active() if e["id"] == candidate_of), None)
        if not existing:
            continue
        conf = ev.get("_dedup_confidence", "uncertain")
        pairs.append(
            _pair_record(
                pair_index,
                existing,
                ev,
                conf,
                "pending",
                pending_id=ev["id"],
                existing_id=existing["id"],
            )
        )
        pair_index += 1

    events = expand_venues() + load_active()
    seen: set[tuple[str, str]] = set()
    for a, b in combinations(events, 2):
        conf = dedup_confidence(a, b)
        if conf != "uncertain":
            continue
        key = tuple(sorted([a["id"], b["id"]]))
        if key in seen:
            continue
        seen.add(key)
        pairs.append(_pair_record(pair_index, a, b, conf, "active_scan"))
        pair_index += 1

    return pairs


def build_output(pairs: list[dict]) -> dict:
    return {
        "workflow": "cursor_mcp",
        "count": len(pairs),
        "instructions": AGENT_INSTRUCTIONS,
        "tools": MCP_TOOLS,
        "pairs": pairs,
    }


def print_text_report(output: dict) -> None:
    pairs = output["pairs"]
    if not pairs:
        print("No uncertain dedup pairs found.")
        print("\nOptional: event_list(status='pending') to browse the submission queue.")
        return

    print(f"Found {len(pairs)} uncertain pair(s) — resolve via MCP tools:\n")
    for p in pairs:
        idx = p["pair_index"]
        print(f"--- Pair {idx} [{p['origin']}] ({p['confidence']}) ---")
        print(f"  reason: {p['reason']}")
        print(f"  hint:   {p['hint']}")
        print(f"  A: [{p['a']['id']}] {p['a']['name']}")
        print(f"  B: [{p['b']['id']}] {p['b']['name']}")
        actions = p["mcp_actions"]
        same = actions["if_same_event"]
        distinct = actions["if_distinct"]
        print(f"  if same:     {same['tool']}({json.dumps(same['args'])})")
        if distinct["tool"]:
            print(f"  if distinct: {distinct['tool']}({json.dumps(distinct['args'])})")
        else:
            print(f"  if distinct: (no action)")
        print()
    print("Re-run with --json (default) for machine-readable MCP action payloads.")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="List uncertain dedup pairs for Cursor MCP agent review (no API keys)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Agent workflow:\n"
            "  1. Run this script (outputs JSON by default)\n"
            "  2. event_get both IDs for each pair\n"
            "  3. event_approve / event_reject (pending) or event_add(force=True) (active)\n"
            "  4. Re-run to confirm zero pairs remain\n"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON output for Cursor agent (default; flag kept for compatibility)",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="Human-readable terminal output instead of JSON",
    )
    args = parser.parse_args(argv)

    output = build_output(collect_pairs())

    if args.text:
        print_text_report(output)
    else:
        print(json.dumps(output, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
