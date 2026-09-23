"""Human-reviewed duplicate verdicts (data/known_duplicates.json).

A "same" verdict auto-merges a pair forever; "different" keeps it out of the
review queue forever.
"""

from datetime import datetime, timezone
from typing import Optional

import atomic_io

from . import paths
from .storage import locked


def load_known_duplicates() -> list[dict]:
    """Always read the file. It is a few KB, and a process-level cache is how
    a long-lived MCP server wrote a stale list back over verdicts the cron
    pipeline had recorded in the meantime. A corrupt file raises rather than
    reading as "no verdicts"."""
    return atomic_io.read_json(paths.KNOWN_DUPLICATES_JSON, default=[])


def known_duplicate_verdict(a: dict, b: dict) -> Optional[str]:
    """Return 'certain' if confirmed same, 'skip' if confirmed different, else None."""
    id_a, id_b = a.get("id"), b.get("id")
    if not id_a or not id_b:
        return None
    for entry in load_known_duplicates():
        if {entry["id_a"], entry["id_b"]} == {id_a, id_b}:
            if entry["verdict"] == "same":
                return "certain"
            if entry["verdict"] == "different":
                return "skip"
    return None


@locked
def persist_known_duplicate(id_a: str, id_b: str, verdict: str) -> None:
    """Save a human-reviewed duplicate pair to known_duplicates.json.

    Read + modify + write under the store lock, so two reviewers (or the
    server and the pipeline) cannot each append to their own copy and have
    the second save erase the first.
    """
    pair = sorted([id_a, id_b])
    id_a, id_b = pair[0], pair[1]

    entries = load_known_duplicates()
    for entry in entries:
        if entry["id_a"] == id_a and entry["id_b"] == id_b:
            entry["verdict"] = verdict
            entry["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            break
    else:
        entries.append({
            "id_a": id_a,
            "id_b": id_b,
            "verdict": verdict,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        })

    atomic_io.write_json(paths.KNOWN_DUPLICATES_JSON, entries)


def list_known_duplicates() -> list[dict]:
    """Return all human-reviewed duplicate verdicts (a copy, newest first)."""
    entries = list(load_known_duplicates())
    entries.sort(key=lambda e: e.get("reviewed_at", ""), reverse=True)
    return entries


@locked
def forget_known_duplicate(id_a: str, id_b: str) -> dict:
    """Delete a stored duplicate verdict so the pair is re-evaluated from scratch.

    Undoes a wrong ``verdict:"same"`` (which otherwise auto-merges the pair
    forever) or a wrong ``verdict:"different"`` (which suppresses the pair from
    review forever). Removing the record does not un-merge already-merged events.
    """
    pair = set([id_a, id_b])
    entries = load_known_duplicates()
    kept = [e for e in entries if {e.get("id_a"), e.get("id_b")} != pair]
    if len(kept) == len(entries):
        return {"status": "not_found",
                "message": f"No stored verdict for pair {sorted(pair)}"}
    atomic_io.write_json(paths.KNOWN_DUPLICATES_JSON, kept)
    return {"status": "forgotten", "pair": sorted(pair),
            "remaining": len(kept)}
