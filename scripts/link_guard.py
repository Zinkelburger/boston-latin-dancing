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

Usage:
  python3 scripts/link_guard.py <event_id> <url>
"""

import json
import re
import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from event_store.names import content_words, distinctive_words, normalize_name  # noqa: E402
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
        name_words = content_words(name) - _ADDRESS_NOISE
        return (len(words & page_words) >= max(1, len(words) * 0.6)
                and len(name_words & page_words) >= max(1, len(name_words) * 0.6))
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
