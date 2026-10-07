#!/usr/bin/env python3
"""The weekly review, split so the only thing an agent does is answer
questions. Everything that needs no judgment is code.

  prepare  verify every event against its source, check every link, compare
           each event's sources with each other, fix what has one right answer
           (past queue rows, a dead link with a live alternate), then write the
           worklist: one multiple-choice question per judgment call, with all
           the evidence inline.
  (agent)  answers each question through the MCP review tools (review_next /
           review_answer / review_skip). An answer is checked before anything
           changes: unknown choices, missing details, a link that is not a page
           about the event, an address that will not geocode — all refused with
           the reason, and the question stays open.
  recheck  verify the state the answers left (approvals add events) and turn
           anything finish would block on into follow-up questions. Exits 3
           when it added some, so the runner gives the agent one more pass.
  finish   publish (tripwire-guarded), re-check links, run the doctor, write
           automation/logs/last-agent-summary.md. automation/claude_review.sh
           then commits exactly the pipeline-owned files and pushes.

Usage:
  python3 scripts/weekly_review.py prepare
  python3 scripts/weekly_review.py recheck
  python3 scripts/weekly_review.py status
  python3 scripts/weekly_review.py finish

Exit codes for finish: 0 publish ok (commit), 1 incomplete/blocked, 2 tripwire.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import atomic_io  # noqa: E402
import scraper_utils  # noqa: E402
from event_store import (  # noqa: E402
    approve_pending,
    approve_rejected,
    archive_event,
    block_event,
    dismiss_rejected,
    edit_event,
    load_active,
    load_archive,
    load_blocked,
    load_pending,
    load_rejected,
    load_venue_conflicts,
    publish_guarded,
    reject_pending,
    resolve_venue_conflict,
    store_lock,
)
from event_store import paths, storage  # noqa: E402
from event_store.archive import HOLD_CANCELLED, HOLD_NO_LINK  # noqa: E402
from event_store.classify import derive_special, looks_like_class, special_edition_mismatch  # noqa: E402
from event_store.ingest import add_event  # noqa: E402
from event_store.locations import is_out_of_area, locations_same  # noqa: E402
from event_store.names import content_words, distinctive_words, normalize_name  # noqa: E402
from event_store.occurrences import last_occurrence, occurrence_instants, parse_aware, weekday_of  # noqa: E402
from event_store.storage import append_changelog  # noqa: E402
from event_store.urls import event_url_list, url_key  # noqa: E402
from link_guard import _SHARE_WRAPPER_RE, check_link_for_event  # noqa: E402
from recurrence_utils import NY_TZ  # noqa: E402

WORKLIST_PATH = ROOT / "automation" / "logs" / "worklist.json"
SUMMARY_PATH = ROOT / "automation" / "logs" / "last-agent-summary.md"

STYLES = ("salsa", "bachata", "merengue", "kizomba", "zouk", "other")

# ── What the reviewer is told ─────────────────────────────────────────

DANCE_TEST = (
    "The test is: could you show up and dance? Socials, parties, DJ or live-music "
    "dance nights at bars and clubs (reggaeton, dembow and Latin pop count), "
    "outdoor dancing, festivals and benefits with dancing all belong. A class "
    "followed by a social belongs. A thin one-line listing is not a reason to "
    "reject; when unsure, approve."
)
BIG_EVENT_RULE = (
    "big_event=true for a unique branded one-off the scene plans around, a "
    "benefit/fundraiser/solidarity night, a multi-organizer or stacked lineup, or "
    "anything festival- or citywide-scale. big_event=false for a regular weekly or "
    "monthly night, a single guest DJ, or a holiday-theme bar night."
)
BLOCKS = {
    "class_only": "Instruction only (class, workshop, bootcamp, lesson series) with no "
                  "social dancing. Blocked, so it never comes back.",
    "not_dance": "Nobody dances: a sit-down concert, talk, screening, fitness class or "
                 "drum circle. Blocked.",
    "not_latin": "A dance event, but not Latin (swing, contra, ballroom only...). Blocked.",
    "out_of_area": "Not in greater Boston. Blocked.",
}
TIME_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*$", re.I)


class Refused(Exception):
    """An answer that cannot be applied; the message says what to do instead."""


# ── Presenting events ─────────────────────────────────────────────────

def _when(event: dict) -> str:
    start = parse_aware(event.get("startDate", ""))
    if start is None:
        return "no date"
    start = start.astimezone(NY_TZ)
    text = start.strftime("%a %b %-d %Y, %-I:%M %p")
    end = parse_aware(event.get("endDate", ""))
    if end is not None and end > start:
        text += " – " + end.astimezone(NY_TZ).strftime("%-I:%M %p")
    return text


def _source_names() -> dict[str, str]:
    try:
        return {s["id"]: s.get("name", s["id"]) for s in scraper_utils.load_sources()}
    except Exception:  # noqa: BLE001 - a label, never worth failing a review over
        return {}


def card(event: dict) -> dict:
    """Everything a reviewer needs to know about one event, and nothing else."""
    recurring = bool(event.get("recurring") or event.get("recurrences"))
    out = {
        "id": event.get("id"),
        "name": event.get("name"),
        "when": _when(event),
        "location": event.get("location") or "(none)",
        "on_map": event.get("lat") is not None and event.get("lng") is not None,
        "link": event.get("url"),
        "other_links": event.get("urls") or [],
        "cost": event.get("cost"),
        "styles": event.get("styles") or [],
        "recurring": recurring,
        "source": _source_names().get(event.get("source", ""), event.get("source")),
        "description": (event.get("description") or "")[:1200],
    }
    if recurring:
        out["dates"] = [dt.astimezone(NY_TZ).strftime("%a %b %-d") for dt in occurrence_instants(event)][:8]
    return out


def _is_series(event: dict) -> bool:
    return bool(event.get("recurring") or event.get("recurrences"))


def _item(kind: str, subject: str, title: str, question: str, evidence: dict,
          choices: dict[str, str], details: Optional[dict[str, str]] = None,
          key: Optional[str] = None) -> dict:
    return {
        "id": f"{kind}:{key or subject}",
        "kind": kind,
        "subject": subject,
        "title": title,
        "question": question,
        "evidence": evidence,
        "choices": choices,
        "details": details or {},
        "answer": None,
        "skipped": None,
    }


# ── Deterministic fixes (no question needed) ──────────────────────────

def drop_past_pending(now: Optional[datetime] = None) -> list[dict]:
    """A queued event whose last night has passed is no longer a decision."""
    now = now or datetime.now(timezone.utc)
    done = []
    for event in load_pending():
        last = last_occurrence(event)
        if last is not None and last < now - timedelta(hours=24):
            reject_pending(event["id"], "already over by the time it was reviewed")
            done.append({"action": "dropped past queue entry", "event": event.get("name"),
                         "when": _when(event)})
    return done


STRUCTURED_CANCELLATION = "JSON-LD eventStatus = EventCancelled"


def archive_structured_cancellations(report: list[dict]) -> list[dict]:
    """A one-off whose own page marks it EventCancelled in its structured data
    is cancelled; that is the organizer's ticketing system talking, not a guess
    from the page text, so there is no question to ask."""
    done = []
    by_id = {e["id"]: e for e in load_active()}
    for row in report:
        event = by_id.get(row.get("event_id"))
        if (event is None or _is_series(event) or row.get("status") != "cancelled"
                or row.get("notes") != STRUCTURED_CANCELLATION
                or row.get("source_url") != event.get("url")):
            continue
        archive_event(event["id"], reason="cancelled: the event page says EventCancelled",
                      hold=HOLD_CANCELLED)
        done.append({"action": "archived a cancelled event", "event": event.get("name"),
                     "when": _when(event), "evidence": row.get("source_url")})
    return done


def fix_dead_links(link_report: dict) -> tuple[list[dict], set[str]]:
    """Remove dead alternates; promote only a live, event-matching alternate.
    Returns (actions, still-broken primary urls)."""
    broken = {url_key(r["url"]) for r in link_report.get("broken", [])}
    alive = {url_key(r["url"]) for r in link_report.get("ok", [])}
    actions: list[dict] = []
    for event in load_active():
        links = event_url_list(event)
        dead = [u for u in links if url_key(u) in broken]
        if not dead:
            continue
        live = [u for u in links if url_key(u) not in broken]
        primary = event.get("url")
        if primary and url_key(primary) in broken:
            replacement = next((u for u in live if url_key(u) in alive
                                and check_link_for_event(u, event)["accepted"]), None)
            if replacement is None:
                # Nothing proven live to promote: keep the primary for the
                # question, but still shed dead alternates.
                live = [primary] + [u for u in live if u != primary]
                if len(live) == len(links):
                    continue
                edit_event(event["id"], {"urls": live[1:]})
                actions.append({"action": "removed dead alternate link(s)",
                                "event": event.get("name"), "links": [u for u in dead if u != primary]})
                continue
            rest = [u for u in live if u != replacement]
            edit_event(event["id"], {"url": replacement, "urls": rest})
            actions.append({"action": "replaced dead link with a live alternate",
                            "event": event.get("name"), "dead": primary, "now": replacement})
        else:
            edit_event(event["id"], {"urls": [u for u in live if u != primary]})
            actions.append({"action": "removed dead alternate link(s)",
                            "event": event.get("name"), "links": dead})
    still = {url_key(e["url"]) for e in load_active() if e.get("url") and url_key(e["url"]) in broken}
    return actions, still


# ── Links we already hold ─────────────────────────────────────────────
#
# A calendar copy often arrives with no link while another scraper holds the
# very page it needs: BOBAS's Oct 8 night (2026-10-07) was on the Sensualeros
# calendar with no URL, while the BOBAS Facebook scraper had its Facebook event
# the same morning. The agent then searched the open web, which does not index
# Facebook events, found nothing, and the night came off the map.

def _days(event: dict) -> set:
    return {dt.astimezone(NY_TZ).date() for dt in occurrence_instants(event)}


def _name_words(text: str) -> set[str]:
    return distinctive_words(content_words(normalize_name(text or "")))


def _word_meets(x: str, b: set[str]) -> bool:
    """In b, or the stem of a word in b or stemmed by one ("sabor" / "saborcito")."""
    return x in b or any(len(x) >= 5 and len(y) >= 5 and (x.startswith(y) or y.startswith(x))
                         for y in b)


def _words_meet(a: set[str], b: set[str]) -> bool:
    return any(_word_meets(x, b) for x in a)


def _scraped_events() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(paths.SCRAPED_DIR.glob("*.json")):
        if path.name.endswith("-raw.json"):
            continue
        data = atomic_io.read_json(path, default=[])
        rows += [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
    return rows


def link_leads(event: dict) -> list[dict]:
    """Pages we already hold that may be this event's link, best first:
    same-night copies from any source (scraped files and every store pool),
    the organizer's registered pages, then earlier or later nights of the same
    name (they show where the organizer posts). Each still has to pass the
    link guard; these are leads, not answers."""
    days, words = _days(event), _name_words(event.get("name", ""))
    same_night, organizer, other_nights = [], [], []
    pool = (_scraped_events() + load_active() + load_pending() + load_archive()
            + load_blocked())
    for other in pool:
        if other.get("id") == event.get("id"):
            continue
        urls = [u for u in event_url_list(other)
                if u.startswith("http") and not _SHARE_WRAPPER_RE.search(u)]
        if not urls:
            continue
        other_words = _name_words(other.get("name", ""))
        named = _words_meet(words, other_words)
        try:
            shared = bool(days & _days(other))
        except Exception:  # noqa: BLE001 - a malformed scraped row is just not a lead
            continue
        source = other.get("source") or "?"
        if shared and (named or locations_same(event, other)):
            same_night += [{"url": u, "why": f"{source} lists the same night: {other.get('name')}"}
                           for u in urls]
        elif words and all(_word_meets(w, other_words) for w in words):
            other_nights += [{"url": u, "why": f"another night of {other.get('name')} ({source})"}
                             for u in urls]
    for source in scraper_utils.load_sources():
        if source.get("enabled") is False or source.get("id") == event.get("source"):
            continue
        source_words = _name_words(source.get("name", "")) | _name_words(source.get("id", "").replace("-", " "))
        if not _words_meet(words, source_words):
            continue
        for url in (_organizer_page(source), source.get("website")):
            if url:
                organizer.append({"url": url, "why": f"organizer page ({source.get('name')})"})
    seen, leads = set(), []
    for lead in same_night + organizer + other_nights:
        key = url_key(lead["url"])
        if key not in seen:
            seen.add(key)
            leads.append(lead)
    return leads[:8]


def attach_links_we_hold(report: list[dict]) -> list[dict]:
    """Give a link-less event a page we already hold for it, when one passes
    the link guard. Same-night copies and organizer pages only: another
    night's page is a lead for the agent, never an automatic link."""
    import verify_events

    done = []
    no_source = {r.get("event_id") for r in report if r.get("status") == "no_source"}
    targets = [("active", e) for e in load_active()
               if e["id"] in no_source and not e.get("_needs_manual_check")]
    targets += [("pending", e) for e in load_pending() if not event_url_list(e)]
    for pool, event in targets:
        for lead in link_leads(event):
            if lead["why"].startswith("another night"):
                continue
            if not check_link_for_event(lead["url"], event)["accepted"]:
                continue
            if pool == "active":
                edit_event(event["id"], {"url": lead["url"]})
                verify_events.verify_all(event_id=event["id"])
            else:
                _pool_row(load_pending, storage.save_pending, event["id"], {"url": lead["url"]})
            done.append({"action": "attached a link we already hold", "event": event.get("name"),
                         "when": _when(event), "url": lead["url"], "why": lead["why"]})
            break
    return done


# ── Questions ─────────────────────────────────────────────────────────

def _nearby_same_night(event: dict, pool: list[dict]) -> list[dict]:
    days = {dt.astimezone(NY_TZ).date() for dt in occurrence_instants(event)}
    words = distinctive_words(content_words(normalize_name(event.get("name", ""))))
    out = []
    for other in pool:
        if other.get("id") == event.get("id"):
            continue
        if not days & {dt.astimezone(NY_TZ).date() for dt in occurrence_instants(other)}:
            continue
        other_words = distinctive_words(content_words(normalize_name(other.get("name", ""))))
        if locations_same(event, other) or (words & other_words):
            out.append(card(other))
    return out[:5]


def _approve_details(event: dict, approve: str = "approve") -> dict[str, str]:
    details = {"big_event": "true or false — required. " + BIG_EVENT_RULE,
               "styles": "optional, comma-separated from " + ", ".join(STYLES)
                         + ". Replace 'other' with the real styles when the night is salsa/bachata/etc."}
    if event.get("lat") is None or event.get("lng") is None:
        details["location"] = ("REQUIRED: this event has no map position. Give the full street "
                               "address with town, e.g. '45 Danforth St, Jamaica Plain, MA'.")
    if not event_url_list(event):
        details["url"] = ("REQUIRED: this listing has no link. Search for the organizer's page for "
                          "this night and give it here (try review_link_check first). If there is "
                          f"none, answer {_no_link_choice(event, approve)} instead.")
    return details


def _no_link_choice(event: dict, approve: str) -> str:
    return (f"{approve}_without_link" if scraper_utils.publishes_without_link(event.get("source"))
            else "no_link_yet")


def _link_choices(event: dict, approve: str) -> tuple[dict[str, str], dict[str, dict]]:
    """The way out for a new listing with no link, asked with the approval
    rather than after it: an approved event without a link fails verification,
    and finish refuses to publish (2026-10-07: BOBAS and Saborcito, approved
    from the Sensualeros calendar, blocked the whole week's publish)."""
    if event_url_list(event):
        return {}, {}
    choice = _no_link_choice(event, approve)
    if choice == "no_link_yet":
        return {choice: "It belongs, but a search found no organizer page and this source may "
                        "not publish without one. It stays off the map this week; the question "
                        "comes back if the source lists it again."}, {}
    return ({choice: "It belongs, and a search found no organizer page. This calendar is trusted, "
                     "so it goes on the map without a link. " + DANCE_TEST},
            {choice: {k: v for k, v in _approve_details(event, approve).items() if k != "url"}})


def new_event_items(pending: list[dict], active: list[dict]) -> list[dict]:
    items = []
    # Two sources can list the same new night in one week, so a new event's
    # twin may still be in this queue rather than on the map.
    new = [e for e in pending if not e.get("_dedup_candidate_of")]
    for event in new:
        nearby = _nearby_same_night(event, active + new)
        choices = {"approve": "Put it on the map. " + DANCE_TEST, **BLOCKS}
        details = {"approve": _approve_details(event)}
        link_choices, link_details = _link_choices(event, "approve")
        choices.update(link_choices)
        details.update(link_details)
        if nearby:
            choices["already_listed"] = ("It is the same night as one of evidence.same_night "
                                         "(same organizer, same event). Merged into that one; if that one "
                                         "is also new this week, this copy is dropped.")
            details["already_listed"] = {"same_as": "the id of the matching event in evidence.same_night"}
        evidence = {"event": card(event), "same_night": nearby}
        if not event_url_list(event):
            evidence["link_leads"] = link_leads(event)
        if looks_like_class(event):
            evidence["warning"] = "The listing reads like a class. Check the description for social dancing."
        items.append(_item(
            "new_event", event["id"], event.get("name", ""),
            "A source listed this event for the first time. Does it belong on the map?",
            evidence, choices, details))
    return items


def possible_duplicate_items(pending: list[dict]) -> list[dict]:
    items = []
    by_id = {e["id"]: e for e in load_active() + load_archive()}
    for event in pending:
        cand_id = event.get("_dedup_candidate_of")
        if not cand_id:
            continue
        existing = by_id.get(cand_id)
        if existing is None:
            continue
        evidence = {"new_listing": card(event), "already_listed": card(existing),
                    "why_they_look_alike": event.get("_dedup_reason", "")}
        if not event_url_list(event):
            evidence["link_leads"] = link_leads(event)
        choices = {
            "same": "The same night of the same event. Merged, and future copies merge automatically.",
            "different": "Two different events (another night, another organizer, or one is a "
                         "special edition such as an anniversary). Both stay on the map.",
            **BLOCKS,
        }
        if special_edition_mismatch(event, existing):
            evidence["warning"] = ("One of these is a special edition (anniversary, festival, guest "
                                   "night) and the other is the regular series. Those stay separate: "
                                   "'same' will be refused.")
        details = {"different": _approve_details(event, "different")}
        link_choices, link_details = _link_choices(event, "different")
        choices.update(link_choices)
        details.update(link_details)
        items.append(_item(
            "possible_duplicate", event["id"], event.get("name", ""),
            "Is the new listing the same event as the one already on the map?",
            evidence, choices, details))
    return items


def rejected_items(rejected: list[dict]) -> list[dict]:
    return [_item(
        "rejected", e["id"], e.get("name", ""),
        "This was taken off the map or filtered out as not Latin dance. Should it come back?",
        {"event": card(e), "why_it_was_removed": e.get("_rejected_reason", "")},
        {"restore": "It is a Latin social dance that was filtered by mistake. Back on the map.",
         "duplicate": "It repeats an event already on the map. Blocked for this source only.",
         **BLOCKS},
    ) for e in rejected]


def venue_conflict_items() -> list[dict]:
    items = []
    for row in load_venue_conflicts().get("conflicts", []):
        items.append(_item(
            "venue_conflict", row["id"], row.get("event", {}).get("name", row["id"]),
            "This event falls on a night the venue already has a regular event. How do they relate?",
            row,
            {"distinct": "Both are real and both keep a pin (e.g. an afternoon program that "
                         "ends as the venue's night begins).",
             "replaces": "This event takes over the venue that night; the regular night's pin "
                         "is skipped for that date.",
             "duplicate": "It is just the venue's regular night under another name. Folded in."}))
    return items


def facebook_signal_items(today=None) -> list[dict]:
    import event_doctor
    from fetch_facebook import SIGNALS_PATH, load_signals

    today = today or datetime.now(NY_TZ).date()
    sources = scraper_utils.load_sources()
    by_id = {s["id"]: s for s in sources}
    issues, _ = event_doctor.facebook_signal_issues(
        load_signals(SIGNALS_PATH), sources, load_active() + load_pending(), today,
        event_doctor.load_signal_dismissals())
    items = []
    for issue in issues:
        if not issue.get("date"):
            continue
        source = by_id.get(issue["source_id"], {})
        day = issue["date"]
        items.append(_item(
            "facebook_signal", issue["source_id"], f"{source.get('name', issue['source_id'])} — {day}",
            f"The organizer's Facebook page mentions {weekday_of(day + 'T12:00:00')} {day}, but "
            "nothing on the map matches. Is it a dance night?",
            {"organizer": source.get("name"), "date": day, "what_the_page_says": issue.get("signal"),
             "organizer_page": _organizer_page(source),
             "venue_it_would_use": (source.get("defaults") or {}).get("location")},
            {"add_event": "Yes: the page states this date as a dance night. Added at the "
                          "organizer's usual venue, linked to their page.",
             "not_an_event": "No: it is the post's own date, a past album, or not a dance night. "
                             "Not asked again."},
            {"add_event": {"start_time": "REQUIRED, e.g. '8:30 PM' — from the flyer or post",
                           "end_time": "optional, e.g. '1:00 AM'",
                           "name": "optional; defaults to the organizer's name"}},
            key=f"{issue['source_id']}:{day}"))
    return items


def _organizer_page(source: dict) -> Optional[str]:
    url = source.get("facebook_events_url") or ""
    url = re.sub(r"[&?]sk=events$", "", url)
    return re.sub(r"/events/?$", "", url) or None


def verification_items(report: list[dict], active_by_id: dict) -> tuple[list[dict], list[dict]]:
    items, notes = [], []
    for row in report:
        event = active_by_id.get(row.get("event_id"))
        status = row.get("status")
        if event is None or status in ("confirmed", "reachable_only"):
            continue
        evidence = {"event": card(event), "source_link": row.get("source_url"),
                    "what_the_check_found": row.get("notes", "")}
        if status == "date_mismatch" and not _is_series(event):
            evidence.update(our_date=row.get("our_date"), source_date=row.get("source_date"))
            items.append(_item(
                "date_mismatch", event["id"], event.get("name", ""),
                f"The source page says {row.get('source_date')}, we list {row.get('our_date')}. Which is right?",
                evidence,
                {"use_source_date": "The source is right. Moved to the source's date, same time of day.",
                 "keep_ours": "Our date is right (the source is stale or wrong). Flagged for a human "
                              "to confirm; explain in note.",
                 "not_happening": "The event is not happening. Taken off the map."}))
        elif status == "location_mismatch":
            evidence.update(our_location=row.get("our_location"), source_location=row.get("source_location"))
            items.append(_item(
                "location_mismatch", event["id"], event.get("name", ""),
                "The source page gives a different place. Which is right?", evidence,
                {"use_source_location": "The source is right. Moved to the source's address.",
                 "keep_ours": "Our address is right. Explain in note."}))
        elif status in ("cancelled", "page_gone") and not _is_series(event):
            items.append(_item(
                "cancelled", event["id"], event.get("name", ""),
                "The source page says this is cancelled, or the page is gone. Take it off the map?",
                evidence,
                {"archive": "Yes, it is cancelled or no longer happening.",
                 "keep": "No, it is still on (the page moved, or the notice is about something "
                         "else). Flagged for a human; explain in note."}))
        elif status == "no_source":
            none_found = (
                "Searched and found nothing trustworthy. This calendar is trusted, so it stays "
                "on the map without a link."
                if scraper_utils.publishes_without_link(event.get("source")) else
                "Searched and found nothing trustworthy. It comes off the map until its source "
                "lists it with a link.")
            evidence["link_leads"] = link_leads(event)
            items.append(_item(
                "no_link", event["id"], event.get("name", ""),
                "This event has no link. Find the organizer's page for it. Start with "
                "evidence.link_leads.", evidence,
                {"set_link": "Found it: give the URL in `url`. It is fetched and must be a page "
                             "about this event on this date, or it is refused.",
                 "none_found": none_found},
                {"set_link": {"url": "the page's URL (use review_link_check first)"}}))
        else:
            notes.append({"event": event.get("name"), "when": _when(event),
                          "status": status, "notes": row.get("notes", "")})
    return items, notes


def broken_link_items(still_broken: set[str], active: list[dict]) -> list[dict]:
    items = []
    for event in active:
        url = event.get("url")
        if not url or url_key(url) not in still_broken:
            continue
        choices = {
            "set_link": "Found a working page for this event: give it in `url` (checked before use).",
            "remove_link": "No working page exists. The event stays, without a link.",
            "flag_manual": "Cannot tell. A human checks it; explain in note.",
        }
        if not _is_series(event):
            choices["archive"] = "The event itself is gone (the page died because it was cancelled)."
        items.append(_item(
            "broken_link", event["id"], event.get("name", ""),
            "This event's link is dead and it has no working alternate.",
            {"event": card(event), "dead_link": url}, choices,
            {"set_link": {"url": "the replacement URL (use review_link_check first)"}}))
    return items


def source_conflict_items(cross_report: dict, active_by_id: dict) -> list[dict]:
    items = []
    for row in cross_report.get("disagreements", []):
        event = active_by_id.get(row.get("event_id"))
        if event is None:
            continue
        claims = [{"url": c["url"], "says_date": c.get("date"), "says_location": c.get("location"),
                   "read_from": c.get("via"), "city_only": c.get("city_level", False)}
                  for c in row.get("claims", [])]
        if row.get("date", {}).get("verdict") == "disagree":
            items.append(_item(
                "link_date_conflict", event["id"], event.get("name", ""),
                f"This event's links disagree about the date (we list {row.get('our_date')}). "
                "Usually one link is for a different night of the same series.",
                {"event": card(event), "links": claims},
                {"drop_link": "One link is for a different night. Give it in `url`; it is removed "
                              "from this event.",
                 "use_date_from": "Our date is wrong. Give the link that is right in `url`; the event "
                                  "moves to that link's date (one-off events only).",
                 "flag_manual": "Cannot tell which is right. A human checks it; explain in note."},
                {"drop_link": {"url": "one of evidence.links[].url"},
                 "use_date_from": {"url": "one of evidence.links[].url"}},
                key=f"{event['id']}:date"))
        if row.get("location", {}).get("verdict") == "disagree":
            items.append(_item(
                "link_location_conflict", event["id"], event.get("name", ""),
                "This event's links disagree about where it is. City-only readings (Facebook) "
                "can only catch the wrong town, never the wrong venue.",
                {"event": card(event), "links": claims},
                {"use_location_from": "Our address is wrong. Give the right link in `url` (not a "
                                      "city-only one); the event moves to its address.",
                 "keep_ours": "Our address is right. Explain in note."},
                {"use_location_from": {"url": "one of evidence.links[].url with city_only false"}},
                key=f"{event['id']}:location"))
    return items


def _series_names(events: list[dict]) -> set[str]:
    """Names carried by more than one record: a series stored one date per
    record (Fiesta's socials, Sabor Latino at El Barco). A regular night."""
    seen: dict[str, int] = {}
    for e in events:
        name = normalize_name(e.get("name", ""))
        seen[name] = seen.get(name, 0) + 1
    return {n for n, count in seen.items() if count > 1}


def big_event_items(active: list[dict], now: Optional[datetime] = None,
                    series_names: frozenset | set = frozenset()) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    items = []
    for event in active:
        if _is_series(event) or event.get("schedule") or event.get("searchOnly"):
            continue
        if normalize_name(event.get("name", "")) in series_names:
            continue
        if event.get("special") is not None or event.get("_big_event_reviewed"):
            continue
        last = last_occurrence(event)
        if last is None or last < now:
            continue
        probe = dict(event)
        derive_special(probe)
        if probe.get("special"):
            continue  # the name already flags it
        items.append(_item(
            "big_event", event["id"], event.get("name", ""),
            "Is this a big event (gold pin, 'Big Events' filter)? " + BIG_EVENT_RULE,
            {"event": card(event)},
            {"yes": "A big event.", "no": "A regular night."},
            {"yes": {"styles": "optional: real styles if the event says 'other'"}}))
    return items


# ── Worklist file ─────────────────────────────────────────────────────

def load_worklist() -> dict:
    return atomic_io.read_json(WORKLIST_PATH, default={"items": []})


def save_worklist(worklist: dict) -> None:
    WORKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_io.write_json(WORKLIST_PATH, worklist)


def build_worklist(verification: list[dict], link_report: dict, cross_report: dict,
                   auto_actions: list[dict], still_broken: set[str]) -> dict:
    active = load_active()
    # An event already flagged for a human is the human's: asking the agent
    # about it too invites a second, conflicting answer.
    reviewable = [e for e in active if not e.get("_needs_manual_check")]
    active_by_id = {e["id"]: e for e in reviewable}
    pending = load_pending()
    verify_items, verify_notes = verification_items(verification, active_by_id)
    items = (
        rejected_items(load_rejected())
        + new_event_items(pending, active)
        + possible_duplicate_items(pending)
        + venue_conflict_items()
        + facebook_signal_items()
        + verify_items
        + broken_link_items(still_broken, reviewable)
        + source_conflict_items(cross_report, active_by_id)
        + big_event_items(reviewable, series_names=_series_names(active + load_archive()))
    )
    health = scraper_utils.load_scrape_health()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
        "auto_actions": auto_actions,
        "for_the_human": {
            "manual_checks": link_report.get("needs_manual_check", []),
            "unverified": verify_notes,
            "scrapers_need_redesign": sorted(s for s, h in health.items()
                                             if h.get("status") == "structure_missing"),
            "scrapers_unreachable": sorted(s for s, h in health.items()
                                           if h.get("status") == "fetch_error"),
        },
    }


def prepare(run_checks: bool = True) -> dict:
    """Run every deterministic check and fix, then write the worklist."""
    import check_links
    import cross_check
    import verify_events

    auto = drop_past_pending()

    verification: list[dict] = []
    link_report: dict = {}
    cross_report: dict = {}
    if run_checks:
        verify_events.verify_all(stale_days=7)
        verification = atomic_io.read_json(verify_events.REPORT_PATH, default=[])
        auto += archive_structured_cancellations(verification)
        auto += attach_links_we_hold(verification)
        verification = atomic_io.read_json(verify_events.REPORT_PATH, default=[])
        link_report = check_links.check_all(only_live=True)
        atomic_io.write_json(check_links.REPORT_PATH, link_report)
    fixes, still_broken = fix_dead_links(link_report)
    auto += fixes
    if run_checks:
        cross_report = cross_check.run(load_active())
        atomic_io.write_json(cross_check.REPORT_PATH, cross_report)

    worklist = build_worklist(verification, link_report, cross_report, auto, still_broken)
    save_worklist(worklist)
    return worklist


# ── Answering ─────────────────────────────────────────────────────────

def _bool(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "yes", "1"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", "no", "0"):
        return False
    raise Refused(f"`{name}` must be true or false.")


def _styles(value) -> Optional[list[str]]:
    if value in (None, "", []):
        return None
    parts = value if isinstance(value, list) else str(value).split(",")
    if any(not isinstance(p, str) for p in parts):
        raise Refused("`styles` must be a list of style names or comma-separated text.")
    styles = [p.strip().lower() for p in parts if p.strip()]
    bad = [s for s in styles if s not in STYLES]
    if bad:
        raise Refused(f"unknown style(s) {bad}; use: {', '.join(STYLES)}")
    return styles


def _time_of_day(value: str, name: str) -> tuple[int, int]:
    m = TIME_RE.match(value or "")
    if not m:
        raise Refused(f"`{name}` must look like '8:30 PM' or '20:30'.")
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ampm.startswith("p") and hour < 12:
        hour += 12
    if ampm.startswith("a") and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        raise Refused(f"`{name}` is not a time of day.")
    return hour, minute


def _pool_row(loader, saver, event_id: str, updates: dict) -> None:
    with store_lock():
        rows = loader()
        for row in rows:
            if row.get("id") == event_id:
                row.update(updates)
        saver(rows)


def _locate(event: dict, location: str) -> dict:
    """Geocode a reviewer-supplied address; refuse one that cannot be placed."""
    location = (location or "").strip()
    if len(location) < 8 or not re.search(r"\d|,", location):
        raise Refused("`location` must be a full street address with town, e.g. "
                      "'45 Danforth St, Jamaica Plain, MA'.")
    coords = scraper_utils.geocode(location)
    if not coords:
        raise Refused(f"could not place '{location}' on the map. Give the street address "
                      "and town exactly as the organizer lists it.")
    probe = {**event, "location": location, "lat": coords[0], "lng": coords[1]}
    if is_out_of_area(probe):
        raise Refused(f"'{location}' is outside greater Boston. If the event is, answer out_of_area.")
    return {"location": location, "_location_override": location, "lat": coords[0], "lng": coords[1]}


def _check_link(url: str, event: dict) -> None:
    verdict = check_link_for_event(url, event)
    if not verdict["accepted"]:
        raise Refused(f"link refused: {verdict['reason']}")


def _landed_id(result: dict) -> Optional[str]:
    landed = result.get("event") or result.get("existing") or {}
    return landed.get("id")


def _finish_approval(result: dict, params: dict, event: dict) -> dict:
    if result.get("status") not in ("added", "duplicate", "merged", "reactivated", "merged_into_archive"):
        raise Refused(result.get("message") or f"approval failed: {result.get('status')}")
    landed = _landed_id(result)
    updates: dict = {"_big_event_reviewed": True}
    if not _is_series(event) and _bool(params.get("big_event"), "big_event"):
        updates["special"] = True
    styles = _styles(params.get("styles"))
    if styles:
        updates["styles"] = styles
    if landed and any(e["id"] == landed for e in load_active()):
        edit_event(landed, updates)
    return {"status": "on the map", "event_id": landed}


def _require_big_event(event: dict, params: dict) -> None:
    if not _is_series(event) and params.get("big_event") is None:
        raise Refused("`big_event` is required (true or false). " + BIG_EVENT_RULE)
    if params.get("big_event") is not None:
        _bool(params["big_event"], "big_event")
    _styles(params.get("styles"))


def _approval_updates(event: dict, params: dict, choice: str) -> dict:
    """Validate the whole approval before moving a record out of its queue."""
    _require_big_event(event, params)
    updates = {}
    if params.get("location"):
        updates.update(_locate(event, params["location"]))
    elif event.get("lat") is None or event.get("lng") is None:
        raise Refused("this event has no map position. Answer again with "
                      "location='<street address, town>'.")
    if params.get("url"):
        _check_link(params["url"], {**event, **updates})
        updates["urls"] = [u for u in event_url_list(event) if u != params["url"]]
        updates["url"] = params["url"]
    elif not event_url_list(event):
        if not choice.endswith("_without_link"):
            raise Refused("this listing has no link. Give the organizer's page for this night in "
                          "`url` (try review_link_check first), or if a search finds none, answer "
                          f"{_no_link_choice(event, choice)}.")
        updates["no_link_searched_at"] = datetime.now(timezone.utc).isoformat()
    return updates


def _approve_with_updates(event: dict, updates: dict) -> dict:
    """Restore queue edits when the store refuses an approval."""
    with store_lock():
        if updates:
            _pool_row(load_pending, storage.save_pending, event["id"], updates)
        result = approve_pending(event["id"])
        if result.get("status") not in ("added", "duplicate", "merged", "reactivated", "merged_into_archive"):
            rows = load_pending()
            storage.save_pending([event if row.get("id") == event["id"] else row for row in rows])
            raise Refused(result.get("message") or f"approval failed: {result.get('status')}")
        return result


def _find(pool: list[dict], event_id: str) -> dict:
    event = next((e for e in pool if e.get("id") == event_id), None)
    if event is None:
        raise Refused("this item was already handled (the event is no longer there).")
    return event


def _apply_new_event(item: dict, choice: str, params: dict) -> dict:
    event = _find(load_pending(), item["subject"])
    if choice in BLOCKS:
        return block_event(event["id"], choice, params.get("note") or BLOCKS[choice])
    if choice == "already_listed":
        same_as = params.get("same_as")
        allowed = [c["id"] for c in item["evidence"].get("same_night", [])]
        if same_as not in allowed:
            raise Refused(f"`same_as` must be one of {allowed}.")
        if any(e["id"] == same_as for e in load_pending()):
            # Its twin is new this week too and gets its own answer; this copy
            # goes, and this source's later copies with it.
            return block_event(event["id"], "duplicate_source",
                               params.get("note") or f"same night as {same_as}")
        _approve_with_updates(event, {"_dedup_candidate_of": same_as})
        return {"status": "merged", "into": same_as}
    if choice == "no_link_yet":
        return reject_pending(event["id"], "no link found; asked again if its source lists it again")
    # approve / approve_without_link
    updates = _approval_updates(event, params, choice)
    return _finish_approval(_approve_with_updates(event, updates), params, event)


def _apply_possible_duplicate(item: dict, choice: str, params: dict) -> dict:
    event = _find(load_pending(), item["subject"])
    if choice in BLOCKS:
        return block_event(event["id"], choice, params.get("note") or BLOCKS[choice])
    if choice == "same":
        result = approve_pending(event["id"])
        if result.get("status") == "blocked_special_edition":
            raise Refused("one is a special edition and the other the regular series; they stay "
                          "separate. Answer different.")
        if result.get("status") not in ("added", "duplicate", "merged", "reactivated", "merged_into_archive"):
            raise Refused(result.get("message") or result.get("status"))
        return {"status": "merged", "into": event.get("_dedup_candidate_of")}
    if choice == "no_link_yet":
        return reject_pending(event["id"], "no link found; asked again if its source lists it again")
    # different / different_without_link
    updates = _approval_updates(event, params, choice)
    candidate = event["_dedup_candidate_of"]
    clean = {k: v for k, v in event.items() if not k.startswith(("_dedup", "_quarantined"))}
    clean.update(updates)
    result = add_event(clean, distinct_from=[candidate])
    if result.get("status") not in ("added", "duplicate", "merged", "reactivated"):
        raise Refused(f"could not add it: {result.get('message') or result.get('status')}")
    # Destination first: a failed add must leave the reviewable source intact.
    reject_pending(event["id"], "distinct event")
    return _finish_approval(result, params, event)


def _apply_rejected(item: dict, choice: str, params: dict) -> dict:
    event = _find(load_rejected(), item["subject"])
    if choice == "restore":
        result = approve_rejected(event["id"])
        if result.get("status") in ("not_found", "not_approved"):
            raise Refused(result.get("message") or result.get("status"))
        return {"status": "restored", "event_id": _landed_id(result)}
    category = "duplicate_source" if choice == "duplicate" else choice
    return dismiss_rejected(event["id"], params.get("note") or choice, block=True, block_category=category)


def _apply_venue_conflict(item: dict, choice: str, params: dict) -> dict:
    return resolve_venue_conflict(item["subject"], choice, note=params.get("note", ""))


def _apply_facebook_signal(item: dict, choice: str, params: dict) -> dict:
    source_id, day = item["subject"], item["evidence"]["date"]
    if choice == "not_an_event":
        with store_lock():
            rows = atomic_io.read_json(paths.SIGNAL_DISMISSALS_JSON, default=[])
            rows.append({"source_id": source_id, "date": day,
                         "signal": item["evidence"].get("what_the_page_says"),
                         "note": params.get("note", ""),
                         "at": datetime.now(timezone.utc).isoformat()})
            atomic_io.write_json(paths.SIGNAL_DISMISSALS_JSON, rows)
        return {"status": "dismissed"}
    source = next((s for s in scraper_utils.load_sources() if s.get("id") == source_id), None)
    if source is None:
        raise Refused(f"source {source_id} is no longer registered.")
    defaults = source.get("defaults") or {}
    if not defaults.get("location"):
        raise Refused("this organizer has no default venue; use review_skip and note the "
                      "address a human needs to check.")
    if not params.get("start_time"):
        raise Refused("`start_time` is required, e.g. '8:30 PM', from the flyer or post.")
    hour, minute = _time_of_day(params["start_time"], "start_time")
    start = datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=NY_TZ)
    if params.get("end_time"):
        eh, em = _time_of_day(params["end_time"], "end_time")
        end = start.replace(hour=eh, minute=em)
        if end <= start:
            end += timedelta(days=1)
    else:
        end = start + timedelta(hours=3)
    event = scraper_utils.make_event(
        id=f"{source_id}-signal-{day}", name=params.get("name") or source.get("name", source_id),
        start=start, end=end, location=defaults["location"],
        description=f"{defaults.get('description', '')}\n\nFrom the organizer's page: "
                    f"{item['evidence'].get('what_the_page_says', '')}".strip(),
        url=_organizer_page(source), styles=defaults.get("styles"), cost=defaults.get("cost"),
        source=source_id)
    if event.get("lat") is None:
        raise Refused(f"the organizer's venue '{defaults['location']}' does not geocode; use "
                      "review_skip so a human can fix the address.")
    event["_big_event_reviewed"] = True
    result = add_event(event, skip_latin_check=True)
    if result.get("status") not in ("added", "duplicate", "merged", "reactivated"):
        raise Refused(f"could not add it: {result.get('message') or result.get('status')}")
    return {"status": result["status"], "event_id": _landed_id(result)}


def _flag_manual(event_id: str, reason: str) -> None:
    edit_event(event_id, {"_needs_manual_check": {
        "reason": reason, "flagged_at": datetime.now(timezone.utc).isoformat()}})


def _require_note(params: dict) -> str:
    note = (params.get("note") or "").strip()
    if len(note) < 10:
        raise Refused("explain in `note` what you saw, so the human can act on it.")
    return note


def _move_to_day(event: dict, day: str) -> dict:
    start = parse_aware(event["startDate"]).astimezone(NY_TZ)
    target = datetime.fromisoformat(day).date()
    shift = target - start.date()
    updates = {"startDate": (start + shift).isoformat()}
    end = parse_aware(event.get("endDate", ""))
    if end is not None:
        updates["endDate"] = (end.astimezone(NY_TZ) + shift).isoformat()
    updates["dayOfWeek"] = weekday_of(updates["startDate"])
    return updates


def _apply_date_mismatch(item: dict, choice: str, params: dict) -> dict:
    event = _find(load_active(), item["subject"])
    if choice == "use_source_date":
        day = item["evidence"].get("source_date")
        if not day:
            raise Refused("the source gave no date to use.")
        edit_event(event["id"], _move_to_day(event, day))
        return {"status": "moved", "to": day}
    if choice == "not_happening":
        return archive_event(event["id"], reason=params.get("note") or "not happening per source",
                             hold=HOLD_CANCELLED)
    note = _require_note(params)
    _flag_manual(event["id"], f"source says {item['evidence'].get('source_date')}, we list "
                              f"{item['evidence'].get('our_date')}: {note}")
    return {"status": "kept; flagged for a human"}


def _apply_location_mismatch(item: dict, choice: str, params: dict) -> dict:
    event = _find(load_active(), item["subject"])
    if choice == "use_source_location":
        edit_event(event["id"], _locate(event, item["evidence"].get("source_location") or ""))
        return {"status": "moved"}
    _require_note(params)
    return {"status": "kept"}


def _apply_cancelled(item: dict, choice: str, params: dict) -> dict:
    event = _find(load_active(), item["subject"])
    if choice == "archive":
        return archive_event(event["id"], reason="cancelled per source", hold=HOLD_CANCELLED)
    _flag_manual(event["id"], f"source looked cancelled or gone, reviewer kept it: {_require_note(params)}")
    return {"status": "kept; flagged for a human"}


def _set_primary_link(event: dict, url: str) -> dict:
    _check_link(url, event)
    rest = [u for u in event_url_list(event) if u != url]
    return edit_event(event["id"], {"url": url, "urls": rest})


def _drop_link(event: dict, url: str) -> dict:
    links = event_url_list(event)
    if url not in links:
        raise Refused(f"{url} is not one of this event's links: {links}")
    rest = [u for u in links if u != url]
    return edit_event(event["id"], {"url": rest[0] if rest else None, "urls": rest[1:]})


def _apply_no_link(item: dict, choice: str, params: dict) -> dict:
    event = _find(load_active(), item["subject"])
    if choice == "none_found":
        if scraper_utils.publishes_without_link(event.get("source")):
            edit_event(event["id"], {"no_link_searched_at": datetime.now(timezone.utc).isoformat()})
            return {"status": "left without a link (trusted calendar)"}
        # An unverifiable event must not hold up the whole week's publish.
        archive_event(event["id"], reason="no link found online", hold=HOLD_NO_LINK)
        return {"status": "off the map until its source lists a link"}
    if not params.get("url"):
        raise Refused("`url` is required for set_link.")
    _set_primary_link(event, params["url"])
    return {"status": "linked", "url": params["url"]}


def _apply_broken_link(item: dict, choice: str, params: dict) -> dict:
    event = _find(load_active(), item["subject"])
    if choice == "set_link":
        if not params.get("url"):
            raise Refused("`url` is required for set_link.")
        _set_primary_link(event, params["url"])
        return {"status": "linked", "url": params["url"]}
    if choice == "remove_link":
        _drop_link(event, item["evidence"]["dead_link"])
        return {"status": "link removed"}
    if choice == "archive":
        return archive_event(event["id"], reason="link dead, event gone", hold=HOLD_CANCELLED)
    _flag_manual(event["id"], f"dead link {item['evidence']['dead_link']}: {_require_note(params)}")
    return {"status": "flagged for a human"}


def _claim(item: dict, url: Optional[str]) -> dict:
    claims = item["evidence"].get("links", [])
    claim = next((c for c in claims if c["url"] == url), None)
    if claim is None:
        raise Refused(f"`url` must be one of {[c['url'] for c in claims]}.")
    return claim


def _apply_link_date_conflict(item: dict, choice: str, params: dict) -> dict:
    event = _find(load_active(), item["subject"])
    if choice == "flag_manual":
        _flag_manual(event["id"], f"links disagree on the date: {_require_note(params)}")
        return {"status": "flagged for a human"}
    claim = _claim(item, params.get("url"))
    if choice == "drop_link":
        if len(event_url_list(event)) < 2:
            raise Refused("that is the event's only link; answer flag_manual instead.")
        _drop_link(event, claim["url"])
        return {"status": "link removed", "url": claim["url"]}
    if _is_series(event):
        raise Refused("a recurring series keeps its schedule; answer drop_link or flag_manual.")
    if not claim.get("says_date"):
        raise Refused("that link states no date.")
    edit_event(event["id"], _move_to_day(event, claim["says_date"]))
    return {"status": "moved", "to": claim["says_date"]}


def _apply_link_location_conflict(item: dict, choice: str, params: dict) -> dict:
    event = _find(load_active(), item["subject"])
    if choice == "keep_ours":
        _require_note(params)
        return {"status": "kept"}
    claim = _claim(item, params.get("url"))
    if claim.get("city_only") or not claim.get("says_location"):
        raise Refused("that link gives only a city (or nothing), which cannot place a venue.")
    edit_event(event["id"], _locate(event, claim["says_location"]))
    return {"status": "moved"}


def _apply_big_event(item: dict, choice: str, params: dict) -> dict:
    _find(load_active(), item["subject"])
    updates: dict = {"_big_event_reviewed": True}
    if choice == "yes":
        updates["special"] = True
        styles = _styles(params.get("styles"))
        if styles:
            updates["styles"] = styles
    edit_event(item["subject"], updates)
    return {"status": "big event" if choice == "yes" else "regular night"}


APPLY: dict[str, Callable[[dict, str, dict], dict]] = {
    "new_event": _apply_new_event,
    "possible_duplicate": _apply_possible_duplicate,
    "rejected": _apply_rejected,
    "venue_conflict": _apply_venue_conflict,
    "facebook_signal": _apply_facebook_signal,
    "date_mismatch": _apply_date_mismatch,
    "location_mismatch": _apply_location_mismatch,
    "cancelled": _apply_cancelled,
    "no_link": _apply_no_link,
    "broken_link": _apply_broken_link,
    "link_date_conflict": _apply_link_date_conflict,
    "link_location_conflict": _apply_link_location_conflict,
    "big_event": _apply_big_event,
}


def _progress(worklist: dict) -> str:
    items = worklist.get("items", [])
    done = sum(1 for i in items if i.get("answer") or i.get("skipped"))
    return f"{done} of {len(items)} done"


def next_item() -> dict:
    worklist = load_worklist()
    for item in worklist.get("items", []):
        if not item.get("answer") and not item.get("skipped"):
            return {
                "progress": _progress(worklist),
                "item": {k: item[k] for k in ("id", "title", "question", "evidence", "choices", "details")},
                "how_to_answer": "review_answer(item_id=item.id, choice=<one key of item.choices>, "
                                 "plus any fields item.details lists for that choice, and a short note). "
                                 "If you truly cannot decide, review_skip(item_id, note).",
            }
    return {"progress": _progress(worklist), "done": True,
            "message": "Every question is answered. Stop here; publishing and committing happen automatically."}


def answer(item_id: str, choice: str, **params) -> dict:
    worklist = load_worklist()
    item = next((i for i in worklist.get("items", []) if i["id"] == item_id), None)
    if item is None:
        return {"ok": False, "error": f"no question with id {item_id!r}. Call review_next()."}
    if item.get("answer"):
        return {"ok": False, "error": "already answered.", "answer": item["answer"]}
    if choice not in item["choices"]:
        return {"ok": False, "error": f"{choice!r} is not a choice here.", "choices": list(item["choices"])}
    params = {k: v for k, v in params.items() if v not in (None, "")}
    try:
        result = APPLY[item["kind"]](item, choice, params)
    except Refused as exc:
        return {"ok": False, "error": str(exc), "question_still_open": item_id}
    if isinstance(result, dict) and result.get("status") in ("not_found", "error", "invalid"):
        return {"ok": False, "error": result.get("message") or str(result), "question_still_open": item_id}
    item["answer"] = {"choice": choice, "params": params,
                      "result": {k: v for k, v in (result or {}).items() if k != "event"},
                      "at": datetime.now(timezone.utc).isoformat()}
    item["skipped"] = None
    save_worklist(worklist)
    append_changelog("review_answer", item["subject"], f"{item['kind']}: {choice} {params.get('note', '')}".strip())
    return {"ok": True, "applied": item["answer"]["result"], "progress": _progress(worklist)}


def skip(item_id: str, note: str) -> dict:
    if len((note or "").strip()) < 10:
        return {"ok": False, "error": "say in `note` what a human needs to check."}
    worklist = load_worklist()
    item = next((i for i in worklist.get("items", []) if i["id"] == item_id), None)
    if item is None:
        return {"ok": False, "error": f"no question with id {item_id!r}."}
    item["skipped"] = {"note": note.strip(), "at": datetime.now(timezone.utc).isoformat()}
    save_worklist(worklist)
    return {"ok": True, "progress": _progress(worklist)}


# ── Finish ────────────────────────────────────────────────────────────

def _answer_line(item: dict) -> str:
    a = item["answer"]
    extra = []
    for key in ("url", "location", "same_as", "start_time", "styles"):
        if a["params"].get(key):
            extra.append(f"{key} {a['params'][key]}")
    if a["params"].get("big_event") in (True, "true", "yes"):
        extra.append("big event")
    note = a["params"].get("note")
    line = f"- **{item['title']}** — {a['choice'].replace('_', ' ')}"
    if extra:
        line += f" ({'; '.join(extra)})"
    if note:
        line += f": {note}"
    return line


_KIND_HEADINGS = [
    ("rejected", "Rejected queue"), ("new_event", "New events"),
    ("possible_duplicate", "Possible duplicates"), ("venue_conflict", "Venue conflicts"),
    ("facebook_signal", "Facebook signals"), ("date_mismatch", "Date mismatches"),
    ("location_mismatch", "Location mismatches"), ("cancelled", "Cancelled or gone"),
    ("no_link", "Events without a link"), ("broken_link", "Dead links"),
    ("link_date_conflict", "Links disagreeing on the date"),
    ("link_location_conflict", "Links disagreeing on the place"), ("big_event", "Big events"),
]


def render_summary(worklist: dict, publish: dict, doctor: Optional[dict]) -> str:
    today = datetime.now(NY_TZ).strftime("%Y-%m-%d")
    items = worklist.get("items", [])
    human = worklist.get("for_the_human", {})
    lines = [f"# Weekly review — {today}", ""]

    alarms = []
    for sid in human.get("scrapers_need_redesign", []):
        alarms.append(f"⚠️ SCRAPER NEEDS REDESIGN: {sid} (reached its page, parsed nothing)")
    if publish.get("tripped"):
        alarms.append(f"🚨 TRIPWIRE: {publish.get('message')} Nothing was committed.")
    open_items = [i for i in items if not i.get("answer") and not i.get("skipped")]
    if open_items:
        alarms.append(f"⚠️ {len(open_items)} question(s) were never answered; they come back next week.")
    lines += alarms + ([""] if alarms else [])

    needs = []
    for item in items:
        if item.get("skipped"):
            needs.append(f"- **{item['title']}** ({item['kind'].replace('_', ' ')}): {item['skipped']['note']}")
    for item in open_items:
        needs.append(f"- **{item['title']}** ({item['kind'].replace('_', ' ')}): not answered")
    for m in human.get("manual_checks", []):
        needs.append(f"- **{m.get('name')}**: {m.get('reason')}")
    for u in human.get("unverified", []):
        needs.append(f"- **{u['event']}** ({u['status']}): {u['notes']}")
    if doctor:
        for check in doctor.get("checks", {}).values():
            if check.get("status") != "blocker":
                continue
            needs.append(f"- {check.get('message')}")
            for row in check.get("items", []):
                needs.append(f"- **{row.get('name') or row.get('id') or row.get('source_id', 'Check')}**: "
                             f"{row.get('problem') or row.get('notes') or row}")
    for sid in human.get("scrapers_unreachable", []):
        needs.append(f"- Scraper `{sid}` could not reach its page last run (usually transient).")
    lines += ["## Needs you", *(needs or ["- Nothing."]), ""]

    lines.append("## Decisions")
    answered = [i for i in items if i.get("answer")]
    if not answered:
        lines.append("- None.")
    for kind, heading in _KIND_HEADINGS:
        group = [i for i in answered if i["kind"] == kind]
        if group:
            lines += [f"### {heading} ({len(group)})", *(_answer_line(i) for i in group)]
    lines.append("")

    auto = worklist.get("auto_actions", [])
    if auto:
        lines.append("## Done automatically")
        for a in auto:
            detail = "; ".join(f"{k} {v}" for k, v in a.items() if k != "action")
            lines.append(f"- {a['action']}: {detail}")
        lines.append("")

    lines.append("## Published")
    if publish.get("tripped") or publish.get("blocked"):
        lines.append(f"- Not published: {publish.get('message')}")
    else:
        lines.append(f"- {publish.get('published_live_events')} live events "
                     f"(previously {publish.get('previous_live_events', '?')}); "
                     f"retired URLs {publish.get('retired_urls', '?')}.")
    if doctor:
        lines.append(f"- Doctor: {doctor.get('status')}.")
        for name, check in doctor.get("checks", {}).items():
            if check.get("status") == "blocker":
                lines.append(f"  - {name}: {check.get('message')} ({check.get('count', 0)})")
    return "\n".join(lines) + "\n"


def recheck(run_checks: bool = True) -> dict:
    """After the agent: verify the state its answers left, and ask about
    anything finish would otherwise block on.

    Answers change the map (an approval adds an event nobody has verified yet),
    and finish refuses to publish unless every event verifies. On 2026-10-07
    every question was answered, then finish found two approved events with no
    link and a cancelled event ingest had let back in, and the week went
    unpublished. A problem found after the questions is a follow-up question,
    not a failed run.
    """
    import verify_events

    worklist = load_worklist()
    if run_checks:
        verify_events.verify_all()
    report = atomic_io.read_json(verify_events.REPORT_PATH, default=[])
    auto = archive_structured_cancellations(report)
    if run_checks:
        auto += attach_links_we_hold(report)
        report = atomic_io.read_json(verify_events.REPORT_PATH, default=[])
    active_by_id = {e["id"]: e for e in load_active() if not e.get("_needs_manual_check")}
    items, notes = verification_items(report, active_by_id)
    asked = {i["id"] for i in worklist.get("items", [])}
    new = [i for i in items if i["id"] not in asked]
    worklist.setdefault("items", []).extend(new)
    worklist.setdefault("auto_actions", []).extend(auto)
    worklist.setdefault("for_the_human", {})["unverified"] = notes
    save_worklist(worklist)
    return {"follow_up_questions": len(new), "by_id": [i["id"] for i in new],
            "done_automatically": len(auto)}


def finish(run_checks: bool = True) -> int:
    worklist = load_worklist()
    doctor = None
    reason = None
    if not WORKLIST_PATH.exists():
        reason = "No prepared worklist; run prepare first."
    elif any(not i.get("answer") and not i.get("skipped") for i in worklist.get("items", [])):
        reason = "Review has unanswered questions. Resume the review before publishing."
    if reason is None and run_checks:
        import check_links
        import verify_events
        from event_doctor import run_doctor

        # Approvals and edits happened after prepare. Verify that final state,
        # then gate publication; a post-publish warning cannot protect the map.
        verify_events.verify_all()
        links = check_links.check_all(only_live=True)
        atomic_io.write_json(check_links.REPORT_PATH, links)
        doctor = run_doctor()
        if not doctor.get("ok"):
            reason = "Doctor blockers remain; resolve them before publishing."
        elif links.get("broken"):
            reason = "Broken links remain; resolve them before publishing."
    publish = {"blocked": True, "message": reason} if reason else publish_guarded()
    if reason and doctor and doctor.get("checks", {}).get("publish_tripwire", {}).get("status") == "blocker":
        publish["tripped"] = True
        publish["message"] = doctor["checks"]["publish_tripwire"]["message"]
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(render_summary(worklist, publish, doctor), encoding="utf-8")
    print(SUMMARY_PATH.read_text(encoding="utf-8"))
    return 2 if publish.get("tripped") else 1 if publish.get("blocked") else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["prepare", "recheck", "status", "finish"])
    args = ap.parse_args()
    if args.step == "prepare":
        worklist = prepare()
        kinds: dict[str, int] = {}
        for item in worklist["items"]:
            kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
        print(json.dumps({"questions": len(worklist["items"]), "by_kind": kinds,
                          "done_automatically": len(worklist["auto_actions"])}, indent=2))
        return 0
    if args.step == "recheck":
        result = recheck()
        print(json.dumps(result, indent=2))
        return 3 if result["follow_up_questions"] else 0
    if args.step == "status":
        worklist = load_worklist()
        print(_progress(worklist))
        for item in worklist.get("items", []):
            state = "answered" if item.get("answer") else "skipped" if item.get("skipped") else "open"
            print(f"  [{state:8}] {item['id']}")
        return 0
    return finish()


if __name__ == "__main__":
    sys.exit(main())
