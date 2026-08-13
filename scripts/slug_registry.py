#!/usr/bin/env python3
"""
Append-only registry of every event URL we have ever published.

A slug is `<name-slug>-<first-8-of-id>`, so it moves whenever the name or the
surviving id moves — and both move routinely. A dedup merge keeps the
higher-priority record, so "Battle of the Beats 2026: Latin vs. Hip Hop"
(lister) became "Battle of the Beats 2026: BOSTON" (beatrice) and the URL
Google had already indexed stopped existing. Venue-hub suppression, unreliable-
source demotion and ordinary merges all remove events from the published feed
the same way, each one quietly killing a URL that is still in someone's search
results.

Chasing every removal path is hopeless. Instead the URL layer is kept durable
on its own: once a slug ships, it resolves forever — as the live event, as a
redirect to wherever that event now lives, or as an "ended" page. Never a 404.

Resolution for a slug that is no longer published, in order:
  1. its id is still live under a new name          -> redirect (rename)
  2. its id merged into a live event (known_dupes)  -> redirect (merge)
  3. same normalized name + venue as a live event   -> redirect (re-scrape)
  4. same venue, same date, overlapping title       -> redirect (retitled)
  5. nothing matches                                -> ended page

Step 4 exists because a merge rewrites the name as well as the id: the lister
record was titled "…: Latin vs. Hip Hop" and the beatrice record that replaced
it "…: BOSTON", so neither an id nor an exact-name lookup connects them. It is
deliberately narrow — same venue, start times within two days, and a majority
of significant title words shared — because a wrong redirect sends a reader to
the wrong night, which is worse than an honest "this event has ended".

Usage:
    python3 scripts/slug_registry.py                 # record + resolve, write registry
    python3 scripts/slug_registry.py --backfill      # seed from git history first
    python3 scripts/slug_registry.py --report        # show what resolves where
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from math import cos, radians, sqrt
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
PUBLISHED = ROOT / "data" / "events-published.json"
KNOWN_DUPES = ROOT / "data" / "known_duplicates.json"
REGISTRY_PATH = ROOT / "data" / "slug-registry.json"

LIVE = "live"
ALIAS = "alias"
ENDED = "ended"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (name or "").lower())).strip()


def _venue(location: str) -> str:
    return (location or "").split(",")[0].lower().strip()


# Words that carry no identifying weight in a Boston dance-event title.
_STOPWORDS = {
    "the", "a", "an", "and", "at", "in", "of", "on", "with", "for", "to", "vs",
    "night", "party", "social", "dance", "dancing", "boston", "salsa", "bachata",
    "kizomba", "zouk", "merengue", "latin", "presents", "featuring", "edition",
}


def _tokens(name: str) -> set[str]:
    return {t for t in _norm_name(name).split() if t not in _STOPWORDS and len(t) > 1}


def _title_overlap(a: str, b: str) -> float:
    """Jaccard overlap of significant title words. 0 when either side is bare."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _same_place(a: dict, b: dict) -> bool:
    """Whether two records sit at the same venue.

    Coordinates first: the address strings are written by different scrapers
    and disagree constantly — "Havana Club, 288 Green St, Cambridge" against
    "288 Green St, Cambridge, MA 02139-3312" is the same door, and comparing
    the leading comma-segment says it is not. Falls back to the strings only
    when a coordinate is missing.
    """
    lat_a, lng_a = a.get("lat"), a.get("lng")
    lat_b, lng_b = b.get("lat"), b.get("lng")
    if None not in (lat_a, lng_a, lat_b, lng_b):
        # ~200m: close enough to be one venue, tight enough to separate
        # neighbouring bars on the same block.
        dlat = (lat_a - lat_b) * 111.0
        dlng = (lng_a - lng_b) * 111.0 * cos(radians((lat_a + lat_b) / 2))
        return sqrt(dlat * dlat + dlng * dlng) <= 0.2

    va, vb = _venue(a.get("location", "")), _venue(b.get("location", ""))
    if not va or not vb:
        return False
    return va == vb or va in vb or vb in va


def _within_days(a: Optional[str], b: Optional[str], days: int) -> bool:
    """True when two ISO timestamps sit within `days` of each other.

    Unknown on either side is not a match — this gate only ever tightens.
    """
    if not a or not b:
        return False
    try:
        da = datetime.fromisoformat(a.replace("Z", "+00:00"))
        db = datetime.fromisoformat(b.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if da.tzinfo is None or db.tzinfo is None:
        da, db = da.replace(tzinfo=timezone.utc), db.replace(tzinfo=timezone.utc)
    return abs((da - db).total_seconds()) <= days * 86400


def load_registry() -> dict:
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {"updated_at": None, "entries": {}}


def save_registry(reg: dict) -> None:
    reg["updated_at"] = _now()
    REGISTRY_PATH.write_text(json.dumps(reg, indent=2, sort_keys=True) + "\n")


def _load_published() -> list[dict]:
    if not PUBLISHED.exists():
        return []
    return json.loads(PUBLISHED.read_text())


# ── merge graph ───────────────────────────────────────────────────────

def _same_id_groups() -> dict[str, str]:
    """Union-find over known_duplicates 'same' verdicts -> id => group root."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    if KNOWN_DUPES.exists():
        for pair in json.loads(KNOWN_DUPES.read_text()):
            if pair.get("verdict") == "same" and pair.get("id_a") and pair.get("id_b"):
                union(pair["id_a"], pair["id_b"])

    return {k: find(k) for k in parent}


# ── recording ─────────────────────────────────────────────────────────

def record(reg: dict, published: list[dict]) -> int:
    """Add every currently-published slug to the registry. Returns new count."""
    added = 0
    now = _now()
    for ev in published:
        slug = ev.get("slug")
        if not slug:
            continue
        entry = reg["entries"].get(slug)
        if entry is None:
            reg["entries"][slug] = {
                "id": ev.get("id"),
                "name": ev.get("name"),
                "location": ev.get("location"),
                "startDate": ev.get("startDate"),
                "lat": ev.get("lat"),
                "lng": ev.get("lng"),
                "first_seen": now,
                "last_seen": now,
                "status": LIVE,
                "target": None,
            }
            added += 1
        else:
            entry["last_seen"] = now
            # Keep identity fresh; first_seen is never rewritten.
            entry["id"] = ev.get("id") or entry.get("id")
            entry["name"] = ev.get("name") or entry.get("name")
            entry["location"] = ev.get("location") or entry.get("location")
            entry["startDate"] = ev.get("startDate") or entry.get("startDate")
            if ev.get("lat") is not None:
                entry["lat"], entry["lng"] = ev.get("lat"), ev.get("lng")
    return added


def resolve(reg: dict, published: list[dict]) -> dict[str, int]:
    """Mark every registry slug live / alias / ended."""
    live_by_slug = {e["slug"]: e for e in published if e.get("slug")}
    live_by_id = {e.get("id"): e for e in published if e.get("slug")}
    groups = _same_id_groups()

    live_by_group: dict[str, dict] = {}
    for ev in published:
        gid = groups.get(ev.get("id"))
        if gid and gid not in live_by_group:
            live_by_group[gid] = ev

    live_by_namevenue: dict[tuple[str, str], dict] = {}
    for ev in published:
        key = (_norm_name(ev.get("name", "")), _venue(ev.get("location", "")))
        live_by_namevenue.setdefault(key, ev)

    counts = {LIVE: 0, ALIAS: 0, ENDED: 0}

    for slug, entry in reg["entries"].items():
        if slug in live_by_slug:
            entry["status"] = LIVE
            entry["target"] = None
            counts[LIVE] += 1
            continue

        target = None
        reason = None

        ev = live_by_id.get(entry.get("id"))
        if ev and ev["slug"] != slug:
            target, reason = ev["slug"], "renamed"

        if target is None:
            gid = groups.get(entry.get("id"))
            ev = live_by_group.get(gid) if gid else None
            if ev and ev["slug"] != slug:
                target, reason = ev["slug"], "merged"

        if target is None:
            key = (_norm_name(entry.get("name", "")), _venue(entry.get("location", "")))
            ev = live_by_namevenue.get(key)
            if ev and ev["slug"] != slug and key[0]:
                target, reason = ev["slug"], "rescraped"

        if target is None:
            # A merge rewrites the title too, so fall back to venue + date +
            # title overlap. Best single candidate only: if two live events at
            # the same venue tie, we cannot tell them apart and stay silent.
            scored = []
            for ev in published:
                if ev.get("slug") == slug or not _same_place(entry, ev):
                    continue
                if not _within_days(entry.get("startDate"), ev.get("startDate"), 2):
                    continue
                score = _title_overlap(entry.get("name", ""), ev.get("name", ""))
                if score >= 0.5:
                    scored.append((score, ev))
            scored.sort(key=lambda s: -s[0])
            if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                target, reason = scored[0][1]["slug"], "retitled"

        if target:
            entry["status"] = ALIAS
            entry["target"] = target
            entry["reason"] = reason
            counts[ALIAS] += 1
        else:
            entry["status"] = ENDED
            entry["target"] = None
            entry.pop("reason", None)
            counts[ENDED] += 1

    # An alias must never point at another alias or at nothing.
    for slug, entry in reg["entries"].items():
        if entry["status"] != ALIAS:
            continue
        hops = 0
        target = entry["target"]
        while target and reg["entries"].get(target, {}).get("status") == ALIAS and hops < 10:
            target = reg["entries"][target]["target"]
            hops += 1
        if not target or target not in live_by_slug:
            entry["status"] = ENDED
            entry["target"] = None
            counts[ALIAS] -= 1
            counts[ENDED] += 1
        else:
            entry["target"] = target

    return counts


# ── backfill from git history ─────────────────────────────────────────

def backfill(reg: dict) -> int:
    """Seed the registry from every past version of events-published.json.

    One-time recovery of URLs that died before the registry existed — they are
    the ones already sitting in Google's index.
    """
    log = subprocess.run(
        ["git", "log", "--format=%H", "--", "data/events-published.json"],
        capture_output=True, text=True, cwd=ROOT,
    ).stdout.split()
    log.reverse()

    added = 0
    for sha in log:
        blob = subprocess.run(
            ["git", "show", f"{sha}:data/events-published.json"],
            capture_output=True, text=True, cwd=ROOT,
        )
        if blob.returncode != 0:
            continue
        try:
            events = json.loads(blob.stdout)
        except json.JSONDecodeError:
            continue
        for ev in events:
            slug = ev.get("slug")
            if not slug or slug in reg["entries"]:
                continue
            reg["entries"][slug] = {
                "id": ev.get("id"),
                "name": ev.get("name"),
                "location": ev.get("location"),
                "startDate": ev.get("startDate"),
                "lat": ev.get("lat"),
                "lng": ev.get("lng"),
                "first_seen": f"git:{sha[:10]}",
                "last_seen": f"git:{sha[:10]}",
                "status": LIVE,
                "target": None,
            }
            added += 1
    return added


def update(do_backfill: bool = False) -> dict:
    reg = load_registry()
    backfilled = backfill(reg) if do_backfill else 0
    published = _load_published()
    added = record(reg, published)
    counts = resolve(reg, published)
    save_registry(reg)
    return {"backfilled": backfilled, "new_slugs": added, **counts,
            "total": len(reg["entries"])}


def main() -> int:
    ap = argparse.ArgumentParser(description="Maintain the published-URL registry.")
    ap.add_argument("--backfill", action="store_true",
                    help="seed from git history of events-published.json (one-time)")
    ap.add_argument("--report", action="store_true", help="list alias and ended slugs")
    args = ap.parse_args()

    result = update(do_backfill=args.backfill)
    print(f"registry: {result['total']} urls — {result[LIVE]} live, "
          f"{result[ALIAS]} redirecting, {result[ENDED]} ended"
          + (f" (backfilled {result['backfilled']})" if result["backfilled"] else ""))

    if args.report:
        reg = load_registry()
        aliases = [(s, e) for s, e in sorted(reg["entries"].items()) if e["status"] == ALIAS]
        ended = [s for s, e in sorted(reg["entries"].items()) if e["status"] == ENDED]
        if aliases:
            print(f"\nREDIRECTS ({len(aliases)}):")
            for s, e in aliases:
                print(f"  {s}\n      -> {e['target']}  ({e.get('reason')})")
        if ended:
            print(f"\nENDED ({len(ended)}) — served as 'event has ended', not 404:")
            for s in ended:
                print(f"  {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
