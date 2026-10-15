#!/usr/bin/env python3
"""Is this URL really a page about this event? Asked before any reviewer —
human or agent — attaches a link to an event.

A link that goes to the wrong event is worse than no link: the pin is on the
map and the tap lands on a different night. The weekly review has shipped
exactly that. A Facebook event dated Oct 10 was attached to an Oct 17 party,
and share wrappers were saved that resolve to a photo. So a link is accepted
only when the page, read as the site's own crawler would read it:

  - answers (HTTP 2xx/3xx after redirects),
  - is not a Facebook share wrapper (those point at whatever was shared),
  - identifies itself (a title or description; a login wall says nothing),
  - names the event itself (sharing a venue is not enough), and
  - if it states a date for that event, states an actual recorded occurrence.

An organizer's untitled post is the one exception to naming: one that gives
the event's street address *and* one of its dates is that event even with no
name, because one address on one day is one event (Lili's Instagram post for
"Sazón Noche de Brujas", 2026-10-15: "423 W Broadway, Suite 202 ... 10/31,
5-8pm", no name; the name came from a calendar that copied it).

Usage:
  python3 scripts/link_guard.py <event_id> <url>
"""

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from event_store.names import content_words, distinctive_words, normalize_name, shared_word_count  # noqa: E402
from event_store.occurrences import occurrence_instants  # noqa: E402
from event_store.urls import _SHARE_WRAPPER_RE  # noqa: E402
from link_meta import jsonld_start, link_meta, looks_like_render_timestamp  # noqa: E402
from recurrence_utils import NY_TZ, parse_date  # noqa: E402

# Words in a venue string that say nothing about which venue it is.
_ADDRESS_NOISE = frozenset({
    "street", "st", "avenue", "ave", "road", "rd", "boston", "cambridge", "somerville",
    "ma", "massachusetts", "usa", "united", "states", "square", "sq", "drive", "dr",
    "the", "inside", "floor", "suite", "boulevard", "blvd", "place", "pl",
})


def _event_days(event: dict) -> set[str]:
    return {dt.astimezone(NY_TZ).date().isoformat() for dt in occurrence_instants(event)}


def _identifying_words(event: dict) -> set[str]:
    words = distinctive_words(content_words(normalize_name(event.get("name", ""))))
    return {w for w in words if w not in _ADDRESS_NOISE and not w.isdigit()}


def _names_event(text: str, event: dict) -> bool:
    name = normalize_name(event.get("name", ""))
    page_words = set(normalize_name(text).split())
    words = _identifying_words(event)
    if words:
        # One-letter spelling variants count, as they do in dedup: our
        # calendar's "Bachatween Social" is the organizer's "BachataWeen
        # Social" (2026-10-14), and the date check below still applies.
        name_words = content_words(name) - _ADDRESS_NOISE
        return (shared_word_count(words, page_words) >= max(1, len(words) * 0.6)
                and shared_word_count(name_words, page_words) >= max(1, len(name_words) * 0.6))
    # Generic names still need their complete name, with multiple words.
    # Empty/one-word names cannot establish identity by themselves.
    return len(content_words(name)) >= 2 and f" {name} " in f" {normalize_name(text)} "


def _names_alias(text: str, event: dict) -> bool:
    """The page uses the short name the event goes by, given in parentheses in
    ours: "Sunset Salsa Bachata on the Docks (BOBAS)" and a Facebook event
    titled "BOBAS Thursday 6-9pm" (2026-10-07). On its own that is weak, so the
    caller also requires the page to state a date that fits."""
    page = f" {normalize_name(text)} "
    for alias in re.findall(r"\(([^)]+)\)", event.get("name", "")):
        alias = normalize_name(alias)
        if distinctive_words(content_words(alias)) and f" {alias} " in page:
            return True
    return False


def _page_text(meta: dict) -> str:
    parts = [meta.get("title", ""), meta.get("og_title", ""), meta.get("og_description", "")]
    for ld in meta.get("jsonld_events") or []:
        parts.append(str(ld.get("name", "")))
        loc = ld.get("location")
        if isinstance(loc, dict):
            parts.append(str(loc.get("name", "")))
    return normalize_name(" ".join(p for p in parts if p))


def stated_days(meta: dict) -> list[str]:
    """Calendar days (Boston) the page states for its event, if any."""
    days: list[str] = []
    for ld in meta.get("jsonld_events") or []:
        start = jsonld_start(ld)
        if start and not looks_like_render_timestamp(start):
            dt = parse_date(start)
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=NY_TZ)
                days.append(dt.astimezone(NY_TZ).date().isoformat())
    fb = meta.get("facebook_event")
    if fb and fb.get("date"):
        days.append(fb["date"])
    return sorted(set(days))


_MONTH_NUMBERS = {m[:3].lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}
# "10/31", "10/31/26"; never inside a longer run like a fraction or a URL path.
_NUMERIC_DATE_RE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\d/])")
# "October 31", "Oct 31st", "October 31, 2026"
_WORD_DATE_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
    r"(?:,?\s+(\d{4}))?\b", re.I)
# "423 W Broadway", "260 Sumner Street": the house number and the street name.
_STREET_RE = re.compile(r"\b(\d{1,5})\s+(?:[NSEW]\.?\s+)?([A-Za-z]+)")


# Instagram's "19 likes, 0 comments - lililatindance on October 5, 2026: ..."
# is when the post went up, not when the event is.
_POSTED_ON_RE = re.compile(r"\bon [A-Z][a-z]+ \d{1,2}, \d{4}:")


def _raw_page_text(meta: dict) -> str:
    text = " ".join(str(meta.get(k) or "") for k in ("title", "og_title", "og_description"))
    return _POSTED_ON_RE.sub(" ", text)


def _text_days(text: str, event: dict) -> list[str]:
    """Days a caption states in words or digits. A date with no year takes
    the year of one of the event's own days."""
    years = sorted({int(d[:4]) for d in _event_days(event)})
    found: set[str] = set()
    cands = [(int(m), int(d), y) for m, d, y in _NUMERIC_DATE_RE.findall(text)]
    cands += [(_MONTH_NUMBERS[m.lower()], int(d), y) for m, d, y in _WORD_DATE_RE.findall(text)]
    for month, day, year in cands:
        for y in ([int(year) + (2000 if len(year) == 2 else 0)] if year else years):
            try:
                found.add(date(y, month, day).isoformat())
            except ValueError:
                pass
    return sorted(found)


def _states_street_address(text: str, event: dict) -> bool:
    m = _STREET_RE.search(event.get("location") or "")
    if not m or m.group(2).lower() in _ADDRESS_NOISE:
        return False
    number, street = m.groups()
    return bool(re.search(rf"\b{number}\s+(?:[NSEW]\.?\s+)?{re.escape(street)}\b", text, re.I))


def _dates_fit(event: dict, days: list[str]) -> bool:
    return bool(_event_days(event) & set(days))


def check_link_for_event(url: str, event: dict,
                         fetch: Optional[Callable[[str], dict]] = None) -> dict:
    """Return {"accepted": bool, "reason": str, "page": {...}} for url on event."""
    fetch = fetch or link_meta
    url = (url or "").strip()
    if not re.match(r"^https?://[^/\s]+\.[^/\s]+", url):
        return {"accepted": False, "reason": "not an http(s) URL"}
    if _SHARE_WRAPPER_RE.search(url):
        return {"accepted": False, "reason": (
            "Facebook share/short links point at whatever was shared (often a photo), "
            "not the event. Open it and use the facebook.com/events/<number>/ URL instead.")}

    meta = fetch(url)
    status = meta.get("status")
    page = {
        "status": status,
        "title": meta.get("og_title") or meta.get("title") or "",
        "description": (meta.get("og_description") or "")[:300],
        "stated_dates": stated_days(meta),
        "final_url": meta.get("final_url"),
    }
    if status is None or not 200 <= status < 400:
        return {"accepted": False, "page": page,
                "reason": f"the page does not load ({meta.get('error') or f'HTTP {status}'})"}

    text = _page_text(meta)
    if not text:
        return {"accepted": False, "page": page, "reason": (
            "the page says nothing about itself (login wall, deleted, or a bare app page), "
            "so nobody can tell which event it is")}

    # Bind the name and date to the SAME Event object. Calendar pages often
    # list several events; another event's date is not corroboration.
    structured = meta.get("jsonld_events") or []
    matching = [ld for ld in structured if _names_event(str(ld.get("name", "")), event)]
    by_alias = False
    if (structured and not matching) or (not structured and not _names_event(text, event)):
        matching = [ld for ld in structured if _names_alias(str(ld.get("name", "")), event)]
        by_alias = bool(matching) if structured else _names_alias(text, event)
        if not by_alias and not structured:
            raw = _raw_page_text(meta)
            days = _text_days(raw, event)
            if _states_street_address(raw, event) and _dates_fit(event, days):
                page["stated_dates"] = days
                return {"accepted": True, "page": page, "reason": (
                    "page loads, gives this event's street address, and states its date")}
        if not by_alias:
            return {"accepted": False, "page": page, "reason": (
                f"the page never names this event clearly enough in its event data, "
                f"title or description ({page['title'][:80]!r}); a shared venue is not enough")}

    if structured:
        page["stated_dates"] = stated_days({"jsonld_events": matching,
                                             "facebook_event": meta.get("facebook_event")})

    if by_alias and not page["stated_dates"]:
        return {"accepted": False, "page": page, "reason": (
            "the page uses only the event's short name and states no date, so it could be "
            "any night of it")}

    if page["stated_dates"] and not _dates_fit(event, page["stated_dates"]):
        return {"accepted": False, "page": page, "reason": (
            f"the page is for {', '.join(page['stated_dates'])}, but this event is on "
            f"{', '.join(sorted(_event_days(event))[:4])} — it is a different night")}

    return {"accepted": True, "page": page, "reason": (
        "page loads, names the event, and its date fits" if page["stated_dates"] else
        "page loads and names the event; no machine-readable date was available")}


def main() -> int:
    from event_store import load_active, load_pending

    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    event_id, url = sys.argv[1], sys.argv[2]
    event = next((e for e in load_active() + load_pending() if e.get("id") == event_id), None)
    if event is None:
        print(f"no active or pending event {event_id}", file=sys.stderr)
        return 2
    print(json.dumps(check_link_for_event(url, event), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
