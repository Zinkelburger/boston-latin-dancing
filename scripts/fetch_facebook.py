#!/usr/bin/env python3
"""
Headless-Chrome capture of a Facebook page: Events tab, latest post, albums
and flyer text.

Facebook serves its public pages to a logged-out browser, but only as a
rendered DOM: the Events tab, the newest post, the album list and the
auto-generated image descriptions ("May be an image of ... text that says
'...'") are all there once Chrome has run the page's scripts. This module
drives ``google-chrome --headless=new --dump-dom`` and turns what comes back
into the evidence envelope ``scrape_facebook.py`` normalizes, plus a
*signals* block for the things the Events tab does not carry.

Why signals matter: Tambó announces its regular Friday socials as photo
albums ("Tambo Salsa Social 2026/09/11") and never as Facebook Events, and
Fuego y Candela posted its autumn schedule as a flyer image whose OCR'd alt
text reads "October 10th Saturday, November 14th Saturday ...". Neither is
an event object, so neither is auto-published; both are dated claims from
the organizer's own page, so both are written to ``data/facebook-signals.json``
and the doctor warns when such a date has no event on the map.

Usage:
  python3 scripts/fetch_facebook.py <source_id>      # capture one source
  python3 scripts/fetch_facebook.py --all            # every enabled Facebook source
  python3 scripts/fetch_facebook.py <source_id> --dry-run   # print, write nothing

Environment:
  BLD_CHROME             path to the Chrome/Chromium binary (else searched on PATH)
  BLD_FACEBOOK_BROWSER   "0"/"off" disables headless capture entirely; the
                         Facebook scraper then only normalizes an existing
                         hand-written envelope (the old behaviour)

Failure semantics: a page that does not prove it loaded (no Events tab
header, login wall, truncated DOM) raises :class:`CaptureError` and writes
nothing, so the previous envelope stays in place. A bare "no cards" render
is never turned into ``no_upcoming``; that status needs the tab header
without an "Upcoming" label.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, NamedTuple, Optional
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_io import locked, read_json, write_json  # noqa: E402
from link_meta import facebook_event_details  # noqa: E402
from scraper_utils import (  # noqa: E402
    DATA_DIR,
    MONTH_NAME_RE,
    NY_TZ,
    SCRAPED_DIR,
    load_sources,
    month_number,
    resolve_year,
)

SIGNALS_PATH = DATA_DIR / "facebook-signals.json"
CAPTURE_SCHEMA_VERSION = 1

CHROME_CANDIDATES = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
)
RENDER_TIMEOUT_SECONDS = 60
VIRTUAL_TIME_BUDGET_MS = 12000
# A logged-out Facebook DOM is ~0.7-1.4 MB; anything this small is an error
# page, a redirect stub or a blocked request.
MIN_RENDER_BYTES = 50_000
MAX_EVENT_PAGES = 8
MAX_FLYERS = 12
MAX_ALBUMS = 12
POST_TEXT_MAX = 600
DESCRIPTION_MAX = 500
# A month/day with no year (flyer "October 10th", album "Salsa Social Jul 31")
# is this year unless it is further back than this; then it means next year.
NO_YEAR_GRACE_DAYS = 120

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEEKDAY_INDEX = {name: i for i, name in enumerate(WEEKDAYS)}
_WEEKDAY_RE = "|".join(WEEKDAYS)
_MONTH_ABBR_RE = r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
_TIME_RE = r"\d{1,2}(?::\d{2})?\s?[AP]M"

# Event card as it reads after tag stripping:
#   "Fri, Oct 30 at 8:30 PM EDT Halloween Party Tambó Salsa · Cambridge Event by Tambó Salsa"
CARD_RE = re.compile(
    rf"\b(?P<weekday>{_WEEKDAY_RE}),\s+(?P<month>{_MONTH_ABBR_RE})\s+(?P<day>\d{{1,2}})"
    rf"(?:\s+at\s+(?P<time>{_TIME_RE})(?:\s+[A-Z]{{2,5}})?)?"
    r"\s+(?P<middle>.+?)\s+Event by\s+(?P<host>.+?)(?=\s+See more on Facebook|$)",
    re.S,
)
EVENTS_HEADER_RE = re.compile(r"\bEvents\s+(?P<tabs>(?:Upcoming\s+)?Past)\s+More\b")
EVENT_ID_RE = re.compile(r"/events/(\d{6,})")
# Facebook's own image descriptions; the OCR'd flyer text sits inside quotes.
FLYER_RE = re.compile(r'(?:alt|aria-label)="(May be an image[^"]*)"')
FLYER_TEXT_RE = re.compile(r"text that says\s*'(.+?)'\s*$", re.S)
# Event page header: "Friday, October 30, 2026 at 8:30 PM EDT" (end time optional)
EVENT_WHEN_RE = re.compile(
    rf"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    rf"(?P<date>{MONTH_NAME_RE}\s+\d{{1,2}},\s+\d{{4}})"
    rf"(?:\s+at\s+(?P<start>{_TIME_RE}))?"
    rf"(?:\s*[–-]\s*(?P<end>{_TIME_RE}))?"
)
STREET_ADDRESS_RE = re.compile(
    r"\b\d{1,5}[A-Za-z]?\s[^,]{2,60},\s[A-Za-z .'-]{2,40},\s[A-Z]{2}\s\d{5}(?:-\d{4})?"
)
ALBUM_SPLIT_RE = re.compile(r"\s+(\d+)\s+Items?\b")
ALBUM_SKIP = {
    "photos", "cover photos", "profile pictures", "videos", "mobile uploads",
    "timeline photos", "untitled album", "albums", "tagged photos",
}
_NUMERIC_DATE_RE = re.compile(
    r"\b(?:(?P<y1>\d{4})[/-](?P<m1>\d{1,2})[/-](?P<d1>\d{1,2})"
    r"|(?P<m2>\d{1,2})/(?P<d2>\d{1,2})/(?P<y2>\d{2}|\d{4}))\b"
)
_NAMED_DATE_RE = re.compile(
    rf"\b(?P<month>{MONTH_NAME_RE})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?"
    r"(?:,?\s+(?P<year>\d{4}))?\b",
    re.I,
)
# A flyer is worth keeping when it states a date or talks about when: the
# photographer's watermark ("Tambó Salsa Social") says nothing about a night.
SIGNAL_KEYWORDS = re.compile(
    r"schedule|every|weekly|monthly|tonight|next|monday|tuesday|wednesday|thursday"
    r"|friday|saturday|sunday|\d{1,2}(?::\d{2})?\s?[ap]m",
    re.I,
)
# Facebook's OCR reads "10th" as "1oth" and "0" as "O" inside numbers.
_OCR_ORDINAL_RE = re.compile(r"(?<=\d)[oO](?=(?:st|nd|rd|th)\b)")
_POST_TAIL_RE = re.compile(r"\s+(?:All reactions:|Like Comment|\d+ people responded).*$", re.S)


class CaptureError(RuntimeError):
    """The page did not prove it loaded; nothing may be written."""


class Card(NamedTuple):
    event_id: str
    url: str
    day: date
    time: str
    name_guess: str
    venue_guess: str
    city: str
    host: str


class EventsTab(NamedTuple):
    has_upcoming_tab: bool
    cards: list


# ── Chrome ───────────────────────────────────────────────────────────

def chrome_binary() -> Optional[str]:
    """Path to a usable Chrome/Chromium, or None."""
    configured = os.environ.get("BLD_CHROME", "").strip()
    if configured:
        return configured if shutil.which(configured) or Path(configured).exists() else None
    for name in CHROME_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def browser_capture_enabled() -> bool:
    flag = os.environ.get("BLD_FACEBOOK_BROWSER", "").strip().lower()
    if flag in ("0", "off", "no", "false"):
        return False
    return chrome_binary() is not None


def render(url: str, timeout: int = RENDER_TIMEOUT_SECONDS) -> str:
    """Return the DOM of ``url`` after Chrome ran its scripts."""
    binary = chrome_binary()
    if not binary:
        raise CaptureError("no Chrome/Chromium binary found (set BLD_CHROME)")
    argv = [
        binary, "--headless=new", "--disable-gpu", "--no-sandbox",
        f"--virtual-time-budget={VIRTUAL_TIME_BUDGET_MS}", "--dump-dom", url,
    ]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(f"chrome timed out after {timeout}s rendering {url}") from exc
    except OSError as exc:
        raise CaptureError(f"could not launch chrome: {exc}") from exc
    dom = proc.stdout or ""
    if len(dom) < MIN_RENDER_BYTES:
        raise CaptureError(
            f"chrome returned {len(dom)} bytes for {url} (exit {proc.returncode}); "
            "page did not render"
        )
    return dom


# ── text helpers ─────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
_WS_RE = re.compile(r"\s+")


def page_text(html: str) -> str:
    """Visible-ish text of a rendered page, whitespace collapsed."""
    text = _SCRIPT_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", html_lib.unescape(text)).strip()


def _trim_partial_tags(fragment: str) -> str:
    """Drop the half tags at both ends of a slice taken mid-attribute."""
    first_gt = fragment.find(">")
    if first_gt >= 0 and fragment.find("<") in (-1, *range(first_gt + 1, len(fragment))):
        fragment = fragment[first_gt + 1:]
    last_lt, last_gt = fragment.rfind("<"), fragment.rfind(">")
    if last_lt > last_gt:
        fragment = fragment[:last_lt]
    return fragment


def _meta(html: str, prop: str) -> str:
    m = re.search(
        rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]*)"', html
    )
    if not m:
        m = re.search(
            rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="{re.escape(prop)}"', html
        )
    return html_lib.unescape(m.group(1)).strip() if m else ""


def _today(today: Optional[date] = None) -> date:
    if today is None:
        return datetime.now(NY_TZ).date()
    if isinstance(today, datetime):
        return today.astimezone(NY_TZ).date() if today.tzinfo else today.date()
    return today


def _long_date(d: date) -> str:
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def page_urls(events_url: str) -> dict:
    """The page's root, events, albums and photos URLs from its events URL."""
    parsed = urlparse(events_url)
    base = "https://www.facebook.com"
    if parsed.path.rstrip("/") == "/profile.php":
        page_id = (parse_qs(parsed.query).get("id") or [""])[0]
        if not page_id:
            raise ValueError(f"profile.php URL without id: {events_url}")
        root = f"{base}/profile.php?id={page_id}"
        return {
            "root": root,
            "events": f"{root}&sk=events",
            "albums": f"{root}&sk=photos_albums",
            "photos": f"{root}&sk=photos",
        }
    slug = parsed.path.strip("/").split("/")[0]
    if not slug:
        raise ValueError(f"cannot find a page slug in {events_url}")
    return {
        "root": f"{base}/{slug}",
        "events": f"{base}/{slug}/events",
        "albums": f"{base}/{slug}/photos_albums",
        "photos": f"{base}/{slug}/photos",
    }


# ── dates in free text ───────────────────────────────────────────────

def dates_in_text(text: str, today: Optional[date] = None) -> list[str]:
    """Every calendar date a flyer, album title or post states, as ISO strings.

    Numeric forms (2026/09/11, 09/11/26, 9-11-2026) are exact. A month name
    with a year is exact; without one it is resolved against ``today`` the
    way Facebook readers do: this year unless it is months behind us.
    """
    today = _today(today)
    text = _OCR_ORDINAL_RE.sub("0", text or "")
    found: set[date] = set()
    for m in _NUMERIC_DATE_RE.finditer(text):
        if m.group("y1"):
            y, mo, d = int(m.group("y1")), int(m.group("m1")), int(m.group("d1"))
        else:
            mo, d = int(m.group("m2")), int(m.group("d2"))
            y = int(m.group("y2"))
            if y < 100:
                y += 2000
        try:
            found.add(date(y, mo, d))
        except ValueError:
            continue
    for m in _NAMED_DATE_RE.finditer(text):
        month = month_number(m.group("month"))
        if not month:
            continue
        day = int(m.group("day"))
        if m.group("year"):
            try:
                found.add(date(int(m.group("year")), month, day))
            except ValueError:
                pass
            continue
        resolved = resolve_year(month, day, today, grace_days=NO_YEAR_GRACE_DAYS,
                                max_ahead_days=None)
        if resolved:
            found.add(resolved)
    return [d.isoformat() for d in sorted(found)]


# ── Events tab ───────────────────────────────────────────────────────

def _resolve_card_date(weekday: str, month: str, day: int, today: date) -> Optional[date]:
    """Cards omit the year; the weekday pins it down (Sep 4 is a Friday in 2026 only)."""
    mnum = month_number(month)
    if not mnum:
        return None
    wanted = _WEEKDAY_INDEX.get(weekday)
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidate = date(year, mnum, day)
        except ValueError:
            continue
        if wanted is None or candidate.weekday() == wanted:
            candidates.append(candidate)
    if candidates:
        return min(candidates, key=lambda d: abs((d - today).days))
    return resolve_year(mnum, day, today, grace_days=30, max_ahead_days=None)


def parse_events_tab(html: str, today: Optional[date] = None) -> EventsTab:
    """Cards on a page's Events tab, each pinned to a real date.

    Raises :class:`CaptureError` unless the tab header ("Events Upcoming Past
    More" or "Events Past More") is present: without it nothing proves the
    page rendered past the login wall.
    """
    today = _today(today)
    text = page_text(html)
    header = EVENTS_HEADER_RE.search(text)
    if not header:
        raise CaptureError("Events tab header not found; page did not render or layout changed")
    has_upcoming_tab = "Upcoming" in header.group("tabs")

    ids: list[str] = []
    for m in EVENT_ID_RE.finditer(html):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    cards: list[Card] = []
    for idx, event_id in enumerate(ids):
        start = html.find(f"/events/{event_id}")
        end = html.find(f"/events/{ids[idx + 1]}") if idx + 1 < len(ids) else len(html)
        chunk = page_text(_trim_partial_tags(html[start:end]))
        m = CARD_RE.search(chunk)
        if not m:
            continue
        day = _resolve_card_date(m.group("weekday"), m.group("month"),
                                 int(m.group("day")), today)
        if not day:
            continue
        middle = m.group("middle").strip()
        city = ""
        if " · " in middle:
            middle, city = middle.rsplit(" · ", 1)
        cards.append(Card(
            event_id=event_id,
            url=f"https://www.facebook.com/events/{event_id}/",
            day=day,
            time=(m.group("time") or "").replace("AM", " AM").replace("PM", " PM")
                 .replace("  ", " ").strip(),
            name_guess=middle.strip(),
            venue_guess="",
            city=city.strip(),
            host=m.group("host").strip(),
        ))
    return EventsTab(has_upcoming_tab=has_upcoming_tab, cards=cards)


def parse_event_page(html: str) -> dict:
    """Name, date, time, address and blurb from one event page."""
    text = page_text(html)
    details: dict = {}
    title = _meta(html, "og:title")
    if title:
        details["name"] = re.sub(r"\s*\|\s*Facebook$", "", title).strip()
    preview = facebook_event_details(_meta(html, "og:description"))
    if preview:
        details["date"] = _long_date(date.fromisoformat(preview["date"]))
        details["city"] = preview.get("location") or ""
        details["organizer"] = preview.get("organizer") or ""
    when = EVENT_WHEN_RE.search(text)
    if when:
        details.setdefault("date", when.group("date"))
        if when.group("start"):
            details["time"] = when.group("start")
        if when.group("end"):
            details["end_time"] = when.group("end")
    addr = STREET_ADDRESS_RE.search(text)
    if addr:
        details["street"] = addr.group(0)
        # The venue name sits right before the street line: "... Tambó Salsa 35 Hampshire St"
        before = text[max(0, addr.start() - 120):addr.start()].rstrip()
        venue = re.split(r"(?:United States|Massachusetts|Facebook|See more|Details|responded)\s*", before)[-1]
        venue = venue.strip(" ·,")
        if venue and len(venue) <= 60:
            details["venue"] = venue
    blurb = re.search(r"Anyone on or off Facebook\s+(.+?)(?:\s+See more\b|\s+" +
                      re.escape(details.get("venue", "\0")) + r"\b)", text, re.S)
    if blurb:
        details["description"] = blurb.group(1).strip(" …")[:DESCRIPTION_MAX]
    return details


# ── signals: latest post, albums, flyers ─────────────────────────────

def flyer_texts(html: str) -> list[str]:
    """OCR'd text Facebook attached to the page's images, deduplicated."""
    out: list[str] = []
    for raw in FLYER_RE.findall(html):
        label = html_lib.unescape(raw)
        m = FLYER_TEXT_RE.search(label)
        if not m:
            continue
        said = _WS_RE.sub(" ", m.group(1)).strip()
        if said and said not in out:
            out.append(said)
    return out


def parse_flyers(html: str, today: Optional[date] = None) -> list[dict]:
    flyers = []
    for said in flyer_texts(html):
        dates = dates_in_text(said, today)
        if not dates and not SIGNAL_KEYWORDS.search(said):
            continue
        flyers.append({"text": said[:POST_TEXT_MAX], "dates": dates})
        if len(flyers) >= MAX_FLYERS:
            break
    return flyers


def parse_latest_post(html: str, today: Optional[date] = None) -> Optional[dict]:
    """The one post a logged-out render shows, if any."""
    text = page_text(html)
    marker = text.rfind("Cookies · More")
    if marker < 0:
        return None
    tail = text[marker + len("Cookies · More"):]
    cut = re.search(r"\s+See more from\b", tail)
    body = tail[:cut.start()] if cut else tail
    body = re.sub(r"^\s*(?:Online status indicator\s+)?(?:Active\s+)?", "", body)
    body = _POST_TAIL_RE.sub("", body).strip()
    if not body:
        return None
    return {"text": body[:POST_TEXT_MAX], "dates": dates_in_text(body, today)}


def parse_albums(html: str, today: Optional[date] = None) -> list[dict]:
    """Album titles with their photo counts and any date the title states."""
    text = page_text(html)
    start = text.rfind(" Albums ")
    if start < 0:
        return []
    segment = text[start + len(" Albums "):]
    stop = segment.find("See more on Facebook")
    if stop >= 0:
        segment = segment[:stop]
    parts = ALBUM_SPLIT_RE.split(segment)
    albums = []
    for i in range(0, len(parts) - 1, 2):
        title = parts[i].strip(" ·")
        count = int(parts[i + 1])
        if not title or title.lower() in ALBUM_SKIP:
            continue
        albums.append({"title": title[:120], "items": count, "dates": dates_in_text(title, today)})
        if len(albums) >= MAX_ALBUMS:
            break
    return albums


# ── capture ──────────────────────────────────────────────────────────

def _raw_event(card: Card, details: dict, defaults: dict) -> dict:
    name = details.get("name") or card.name_guess
    venue = details.get("venue") or ""
    if not venue and card.name_guess.startswith(name):
        venue = card.name_guess[len(name):].strip()
    street = details.get("street", "")
    if venue and street:
        location = f"{venue}, {street}"
    elif street:
        location = street
    elif venue and card.city:
        location = f"{venue}, {card.city}, MA"
    else:
        location = defaults.get("location", "")
    raw = {
        "name": name,
        "date": details.get("date") or _long_date(card.day),
        "time": details.get("time") or card.time,
        "location": location,
        "url": card.url,
        "description": details.get("description", ""),
    }
    if details.get("end_time"):
        raw["end_time"] = details["end_time"]
    return raw


def capture_source(
    source: dict,
    *,
    today: Optional[date] = None,
    renderer: Optional[Callable[[str], str]] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Render a source's Facebook page and build its evidence envelope.

    Only the Events tab is load-bearing: if it fails the whole capture fails.
    The post, album and photo renders feed the ``signals`` block and record
    their own errors instead of failing the capture.
    """
    today = _today(today)
    renderer = renderer or render
    urls = page_urls(source["facebook_events_url"])
    defaults = source.get("defaults", {})
    now = now or datetime.now(NY_TZ)

    tab = parse_events_tab(renderer(urls["events"]), today)
    upcoming = [c for c in tab.cards if c.day >= today]
    if tab.has_upcoming_tab and not upcoming:
        raise CaptureError(
            f"Upcoming tab shown but no upcoming card parsed from {len(tab.cards)} cards; "
            "card layout may have changed"
        )
    events: list[dict] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    for card in upcoming[:MAX_EVENT_PAGES]:
        details: dict = {}
        if card.event_id not in seen_ids:
            try:
                details = parse_event_page(renderer(card.url))
            except CaptureError as exc:
                errors.append(f"event {card.event_id}: {exc}")
        seen_ids.add(card.event_id)
        events.append(_raw_event(card, details, defaults))

    signals: dict = {"latest_post": None, "albums": [], "flyers": [], "errors": errors}
    flyers: list[dict] = []
    try:
        root_html = renderer(urls["root"])
        signals["latest_post"] = parse_latest_post(root_html, today)
        flyers.extend(parse_flyers(root_html, today))
    except CaptureError as exc:
        errors.append(f"posts: {exc}")
    try:
        signals["albums"] = parse_albums(renderer(urls["albums"]), today)
    except CaptureError as exc:
        errors.append(f"albums: {exc}")
    try:
        for flyer in parse_flyers(renderer(urls["photos"]), today):
            if flyer not in flyers:
                flyers.append(flyer)
    except CaptureError as exc:
        errors.append(f"photos: {exc}")
    signals["flyers"] = flyers[:MAX_FLYERS]

    return {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "checked_at": now.isoformat(timespec="seconds"),
        "source_url": urls["events"],
        "status": "captured" if events else "no_upcoming",
        "events": events,
        "capture": {
            "method": "headless-chrome",
            "cards_seen": len(tab.cards),
            "upcoming_tab": tab.has_upcoming_tab,
        },
        "signals": signals,
    }


def raw_path(source_id: str) -> Path:
    return SCRAPED_DIR / f"{source_id}-raw.json"


def load_signals(path: Optional[Path] = None) -> dict:
    data = read_json(path or SIGNALS_PATH, default={})
    return data if isinstance(data, dict) else {}


def record_signals(source_id: str, envelope: dict, path: Optional[Path] = None) -> None:
    """Merge one source's dated signals into data/facebook-signals.json."""
    path = path or SIGNALS_PATH
    signals = envelope.get("signals") or {}
    entry = {
        "checked_at": envelope["checked_at"],
        "source_url": envelope["source_url"],
        "status": envelope["status"],
        "upcoming_events": [
            {"name": e.get("name"), "date": e.get("date"), "url": e.get("url")}
            for e in envelope.get("events", [])
        ],
        "latest_post": signals.get("latest_post"),
        "albums": signals.get("albums", []),
        "flyers": signals.get("flyers", []),
        "errors": signals.get("errors", []),
    }
    with locked(path):
        data = load_signals(path)
        data[source_id] = entry
        write_json(path, dict(sorted(data.items())))


def write_capture(source: dict, **kwargs) -> dict:
    """Capture a source and persist both the envelope and its signals."""
    envelope = capture_source(source, **kwargs)
    raw_path(source["id"]).parent.mkdir(parents=True, exist_ok=True)
    write_json(raw_path(source["id"]), envelope)
    record_signals(source["id"], envelope)
    return envelope


def facebook_sources() -> list[dict]:
    return [s for s in load_sources() if s.get("type") == "facebook" and s.get("enabled")]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source_id", nargs="?")
    parser.add_argument("--all", action="store_true", help="capture every enabled Facebook source")
    parser.add_argument("--dry-run", action="store_true", help="print the envelope, write nothing")
    args = parser.parse_args(argv)

    if not browser_capture_enabled():
        print("headless capture disabled or no Chrome binary found", file=sys.stderr)
        return 2
    sources = facebook_sources()
    if not args.all:
        if not args.source_id:
            parser.error("give a source id or --all")
        sources = [s for s in sources if s["id"] == args.source_id]
        if not sources:
            print(f"{args.source_id}: not an enabled Facebook source", file=sys.stderr)
            return 1

    rc = 0
    for source in sources:
        try:
            envelope = capture_source(source) if args.dry_run else write_capture(source)
        except Exception as exc:  # noqa: BLE001 — report every source, then exit non-zero
            print(f"[{source['id']}] capture FAILED: {exc}", file=sys.stderr)
            rc = 1
            continue
        n = len(envelope["events"])
        sig = envelope["signals"]
        print(f"[{source['id']}] {envelope['status']}: {n} upcoming, "
              f"{len(sig['albums'])} albums, {len(sig['flyers'])} flyers, "
              f"post={'yes' if sig['latest_post'] else 'no'}"
              + (f", errors={len(sig['errors'])}" if sig["errors"] else ""),
              file=sys.stderr)
        if args.dry_run:
            print(json.dumps(envelope, indent=2, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
