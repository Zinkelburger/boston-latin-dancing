#!/usr/bin/env python3
"""
Ask every source we have about an event and see whether they agree.

Verification checks one URL — normally the primary — so a listing is only ever
as right as whichever source happened to win the merge. "BachaTipico Hangout"
sat at Suegra's Molino Lounge on the map while its own Partiful page said Gran
Peñol, two and a half kilometres away. One source disagreeing with our stored
value is exactly the shape of that bug, and it is invisible unless you compare
them side by side.

So: pull the claimed location and date out of every URL attached to an event,
put our own stored values alongside them, and report where they diverge. The
comparison is deliberately conservative — sources phrase addresses completely
differently ("Havana Club, 288 Green St, Cambridge, MA 02139, USA" against
"288 Green St, Cambridge, MA 02139-3312, United States" is the same door), so
strings are only ever a first pass and coordinates decide it. Anything we
cannot resolve is reported "unknown", never as a disagreement: this queues
work for a human, and a queue full of false alarms is a queue nobody reads.

Usage:
    python3 scripts/cross_check.py                 # check every active event
    python3 scripts/cross_check.py --id <event_id> # one event
    python3 scripts/cross_check.py --disagreements # only what conflicts
    python3 scripts/cross_check.py --json
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from math import cos, radians, sqrt
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from link_meta import (jsonld_location, jsonld_start, link_meta,
                       looks_like_render_timestamp)
from event_store import EVENTS_DIR, NY_TZ, load_active, parse_date
from scraper_utils import geocode

REPORT_PATH = EVENTS_DIR / "source-agreement.json"

AGREE = "agree"
DISAGREE = "disagree"
UNKNOWN = "unknown"

# Two addresses within this far apart are the same venue. Wide enough to
# absorb a geocoder putting one at the door and the other at the parcel
# centroid, tight enough to separate neighbouring bars.
SAME_VENUE_KM = 0.25

# Facebook previews give a town and nothing else ("Boston, MA"), which
# geocodes to the city centroid — kilometres from any actual venue in it.
# Compared at venue precision every such event reads as a disagreement, so a
# city-level claim is only ever asked the question it can answer: is this the
# right town? Wide enough to cover Boston end to end, narrow enough that
# Portland ME still fails.
SAME_TOWN_KM = 15.0

WORKERS = 6

_STREET_NUM_RE = re.compile(r"\b(\d{1,5})\b")
_NOISE_WORDS = {
    "the", "at", "in", "of", "and", "usa", "us", "united", "states", "ma",
    "massachusetts", "street", "st", "ave", "avenue", "rd", "road", "sq",
    "square", "suite", "floor", "restaurant", "bar", "lounge", "club",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if t not in _NOISE_WORDS and len(t) > 1}


def _street_numbers(text: str) -> set[str]:
    # Zip codes are five digits and are not street numbers.
    return {n for n in _STREET_NUM_RE.findall(text or "") if len(n) <= 4}


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    dlat = (a[0] - b[0]) * 111.0
    dlng = (a[1] - b[1]) * 111.0 * cos(radians((a[0] + b[0]) / 2))
    return sqrt(dlat * dlat + dlng * dlng)


def locations_agree(a: str, b: str, allow_geocode: bool = True,
                    radius_km: float = SAME_VENUE_KM) -> tuple[str, str]:
    """Compare two free-text addresses. Returns (verdict, why).

    Strings first because they are free; coordinates settle anything the
    strings leave open, since two sources almost never phrase an address the
    same way. `radius_km` is the precision the comparison is entitled to —
    venue-tight for two street addresses, town-wide when one side only names
    a city.
    """
    if not a or not b:
        return UNKNOWN, "one side has no location"

    na, nb = _norm(a), _norm(b)
    if na == nb or na in nb or nb in na:
        return AGREE, "same address text"

    ta, tb = _tokens(a), _tokens(b)
    shared = ta & tb
    nums_a, nums_b = _street_numbers(a), _street_numbers(b)

    # A shared street number plus a shared word is about as strong as text
    # evidence gets without geocoding.
    if nums_a & nums_b and shared:
        return AGREE, f"same street number ({', '.join(sorted(nums_a & nums_b))})"

    if not allow_geocode:
        return UNKNOWN, "text differs, geocoding disabled"

    ca, cb = geocode(a), geocode(b)
    if not ca or not cb:
        return UNKNOWN, "could not geocode both addresses"

    km = _distance_km(ca, cb)
    if km <= radius_km:
        return AGREE, (f"{int(km * 1000)}m apart" if km < 1
                       else f"{km:.1f}km apart, within the same town")
    return DISAGREE, f"{km:.1f}km apart"


def _ny_day(iso_str: Optional[str]) -> Optional[str]:
    if not iso_str:
        return None
    dt = parse_date(iso_str)
    if dt is None:
        try:
            dt = datetime.fromisoformat(iso_str[:10])
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY_TZ)
    return dt.astimezone(NY_TZ).date().isoformat()


# A timezone artifact is a whole-offset shift, not a vague difference. Sources
# legitimately disagree by minutes (doors vs lesson vs "event starts"), so only
# a gap that lands exactly on a US-Eastern offset is worth reporting.
EASTERN_OFFSETS_H = (4, 5)


def whole_offset_shift(a: Optional[str], b: Optional[str]) -> Optional[int]:
    """Hours between two instants when the gap is exactly an Eastern offset.

    Catches the artifact _ny_day cannot see: a source rendering 9 PM as 5 PM
    keeps the same calendar day, so a day-granularity check calls it agreement.
    """
    da, db = parse_date(a or ""), parse_date(b or "")
    if da is None or db is None:
        return None
    if da.tzinfo is None:
        da = da.replace(tzinfo=NY_TZ)
    if db.tzinfo is None:
        db = db.replace(tzinfo=NY_TZ)
    delta = abs((da - db).total_seconds())
    for hours in EASTERN_OFFSETS_H:
        if abs(delta - hours * 3600) < 60:
            return hours
    return None


# ── what one source claims ────────────────────────────────────────────

def source_claim(url: str) -> dict:
    """Read one source's claimed location and date."""
    claim = {"url": url, "location": None, "date": None, "instant": None,
             "via": None, "city_level": False, "error": None}

    meta = link_meta(url)
    status = meta["status"]
    if status is None or status >= 400:
        claim["error"] = meta["error"] or f"HTTP {status}"
        return claim

    for ld in meta["jsonld_events"]:
        loc, start = jsonld_location(ld), jsonld_start(ld)
        if loc or start:
            # Some calendars stamp the page's render clock into startDate
            # instead of the event's; that is not a date, it is a clock.
            if looks_like_render_timestamp(start):
                start = None
                claim["error"] = "startDate was the page's render clock — ignored"
            claim.update({"location": loc, "date": _ny_day(start),
                          "instant": start, "via": "json-ld"})
            return claim

    fb = meta.get("facebook_event")
    if fb:
        # Facebook gives a city, never a street address — usable for catching
        # an event in the wrong town, useless for telling two venues apart.
        claim.update({"location": fb["location"], "date": fb["date"],
                      "via": "facebook-preview", "city_level": True})
        return claim

    claim["error"] = "no structured data"
    return claim


def cross_check_event(event: dict, allow_geocode: bool = True) -> dict:
    """Compare every source attached to an event against each other and us."""
    urls = [u for u in [event.get("url")] + (event.get("urls") or []) if u]
    result = {
        "event_id": event.get("id"),
        "event_name": event.get("name"),
        "our_location": event.get("location"),
        "our_date": _ny_day(event.get("startDate")),
        "source_count": len(urls),
        "claims": [],
        "location": {"verdict": UNKNOWN, "notes": []},
        "date": {"verdict": UNKNOWN, "notes": []},
    }
    if not urls:
        result["location"]["notes"].append("no source urls")
        return result

    with ThreadPoolExecutor(max_workers=min(WORKERS, len(urls))) as pool:
        result["claims"] = list(pool.map(source_claim, urls))

    # Our stored value is just another claim to be tested, not the referee.
    located = [c for c in result["claims"] if c["location"]]
    loc_verdicts, loc_notes = [], []
    for claim in located:
        radius = SAME_TOWN_KM if claim["city_level"] else SAME_VENUE_KM
        verdict, why = locations_agree(
            event.get("location", ""), claim["location"], allow_geocode, radius)
        loc_verdicts.append(verdict)
        loc_notes.append(f"{_short(claim['url'])} ({claim['via']}): {verdict} — {why}")

    # Sources disagreeing with each other matters even when both differ from
    # us: it means at least one upstream listing is wrong.
    for i in range(len(located)):
        for j in range(i + 1, len(located)):
            radius = (SAME_TOWN_KM
                      if located[i]["city_level"] or located[j]["city_level"]
                      else SAME_VENUE_KM)
            verdict, why = locations_agree(
                located[i]["location"], located[j]["location"], allow_geocode, radius)
            if verdict == DISAGREE:
                loc_verdicts.append(verdict)
                loc_notes.append(
                    f"sources disagree with each other: "
                    f"{_short(located[i]['url'])} vs {_short(located[j]['url'])} — {why}")

    result["location"]["verdict"] = _combine(loc_verdicts)
    result["location"]["notes"] = loc_notes

    dated = [c for c in result["claims"] if c["date"]]
    date_verdicts, date_notes = [], []
    our_day = result["our_date"]
    # A recurring series carries one upstream occurrence against many of ours,
    # so a difference there is expected rather than wrong.
    if our_day and not event.get("recurring") and not event.get("recurrences"):
        for claim in dated:
            verdict = AGREE if claim["date"] == our_day else DISAGREE
            shift = whole_offset_shift(claim.get("instant"), event.get("startDate"))
            if shift:
                # Same calendar day can still hide a timezone bug, so this
                # overrides an "agree" the day comparison would have given.
                verdict = DISAGREE
                date_notes.append(
                    f"{_short(claim['url'])} ({claim['via']}): time differs by exactly "
                    f"{shift}h — a timezone artifact, not a reschedule; see "
                    f"'Whose clock to trust' in .cursor/rules/verification.md")
            else:
                date_notes.append(
                    f"{_short(claim['url'])} ({claim['via']}): says {claim['date']}")
            date_verdicts.append(verdict)
    elif dated:
        date_notes.append("recurring series — upstream dates not compared")

    result["date"]["verdict"] = _combine(date_verdicts)
    result["date"]["notes"] = date_notes
    return result


def _combine(verdicts: list[str]) -> str:
    """One disagreement is enough to flag; otherwise agreement needs evidence."""
    if not verdicts:
        return UNKNOWN
    if DISAGREE in verdicts:
        return DISAGREE
    if AGREE in verdicts:
        return AGREE
    return UNKNOWN


def _short(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url or "")[:45]


def run(events: list[dict], allow_geocode: bool = True) -> dict:
    checked = [cross_check_event(e, allow_geocode) for e in events]
    disagreements = [
        c for c in checked
        if c["location"]["verdict"] == DISAGREE or c["date"]["verdict"] == DISAGREE
    ]
    multi = [c for c in checked if c["source_count"] > 1]
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "events": len(checked),
            "with_multiple_sources": len(multi),
            "disagreements": len(disagreements),
            "location_agreed": sum(1 for c in checked if c["location"]["verdict"] == AGREE),
            "location_unknown": sum(1 for c in checked if c["location"]["verdict"] == UNKNOWN),
        },
        "disagreements": disagreements,
        "checked": checked,
    }


def _print(report: dict, only_disagreements: bool) -> None:
    t = report["totals"]
    print(f"checked {t['events']} events ({t['with_multiple_sources']} with more than one source) — "
          f"{t['location_agreed']} locations confirmed, {t['disagreements']} disagreeing, "
          f"{t['location_unknown']} unconfirmed")

    if report["disagreements"]:
        print(f"\nDISAGREEMENTS ({len(report['disagreements'])}):")
        for c in report["disagreements"]:
            print(f"\n  {c['event_name']}")
            print(f"    ours: {c['our_location']}")
            for claim in c["claims"]:
                if claim["location"]:
                    print(f"    {_short(claim['url'])}: {claim['location']}")
            for note in c["location"]["notes"] + c["date"]["notes"]:
                print(f"      · {note}")

    if not only_disagreements:
        confirmed = [c for c in report["checked"] if c["location"]["verdict"] == AGREE]
        if confirmed:
            print(f"\nCONFIRMED BY SOURCE ({len(confirmed)}):")
            for c in confirmed:
                print(f"  {c['event_name'][:60]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Check whether an event's sources agree.")
    ap.add_argument("--id", help="check a single event by id")
    ap.add_argument("--disagreements", action="store_true", help="print only conflicts")
    ap.add_argument("--no-geocode", action="store_true",
                    help="skip coordinate comparison (text only, faster, less certain)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    events = load_active()
    if args.id:
        events = [e for e in events if e.get("id") == args.id]
        if not events:
            print(f"no active event with id {args.id}", file=sys.stderr)
            return 2

    report = run(events, allow_geocode=not args.no_geocode)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print(report, args.disagreements)
        print(f"\nreport written to {REPORT_PATH.relative_to(EVENTS_DIR.parent.parent)}")
    return 1 if report["disagreements"] else 0


if __name__ == "__main__":
    sys.exit(main())
