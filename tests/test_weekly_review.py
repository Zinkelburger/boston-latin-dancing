"""The weekly review as multiple-choice questions: the worklist is built
deterministically, every answer is checked before anything changes, and a
link is only attached when the page is really about the event.

Store paths are isolated by conftest; the worklist, the summary, the link
fetcher and the geocoder are redirected here.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import link_guard
import scraper_utils
import weekly_review as wr

NY = ZoneInfo("America/New_York")
COORDS = (42.3654, -71.1030)


def _at(days: int, hour: int = 20) -> datetime:
    return (datetime.now(NY) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


def _event(**overrides) -> dict:
    start = _at(10)
    base = {
        "id": "evt-1", "name": "Tropical Fiesta Social", "startDate": start.isoformat(),
        "endDate": (start + timedelta(hours=3)).isoformat(),
        "location": "Havana Club, 288 Green St, Cambridge, MA", "lat": COORDS[0], "lng": COORDS[1],
        "description": "Salsa and bachata social", "url": "https://example.com/fiesta",
        "styles": ["salsa"], "recurring": False, "source": "test",
    }
    base.update(overrides)
    return base


def _page(title="Tropical Fiesta Social", date=None, status=200):
    ld = [{"@type": "Event", "name": title, "startDate": date}] if date else []
    return {"status": status, "title": title, "og_title": title, "og_description": "",
            "canonical": "", "jsonld_events": ld, "final_url": None, "error": None}


@pytest.fixture(autouse=True)
def _review_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "WORKLIST_PATH", tmp_path / "worklist.json")
    monkeypatch.setattr(wr, "SUMMARY_PATH", tmp_path / "summary.md")
    monkeypatch.setattr(scraper_utils, "geocode", lambda loc: COORDS)
    monkeypatch.setattr(wr, "facebook_signal_items", lambda today=None: [])


def _worklist(**reports):
    worklist = wr.build_worklist(reports.get("verification", []), reports.get("links", {}),
                                 reports.get("cross", {}), [], reports.get("still_broken", set()))
    wr.save_worklist(worklist)
    return worklist


def _item_for(worklist, kind):
    return next(i for i in worklist["items"] if i["kind"] == kind)


# ── link guard ────────────────────────────────────────────────────────

def test_a_page_for_another_night_is_refused():
    # BOSTON BACHATA PARTY (2026-09-23): a Facebook event dated Oct 10 was
    # attached to the Oct 17 party as an alternate link.
    event = _event(name="Boston Bachata Party", startDate="2099-10-17T21:00:00-04:00",
                   endDate="2099-10-18T02:00:00-04:00")
    verdict = link_guard.check_link_for_event(
        "https://www.facebook.com/events/1252515136999918/", event,
        fetch=lambda u: _page("BOSTON BACHATA PARTY @ Cocomango", date="2099-10-10T21:00:00-04:00"))
    assert verdict["accepted"] is False
    assert "different night" in verdict["reason"]


def test_share_wrappers_are_refused_without_fetching():
    verdict = link_guard.check_link_for_event(
        "https://facebook.com/events/s/boston-bachata-party/123/", _event(),
        fetch=lambda u: pytest.fail("must not fetch"))
    assert verdict["accepted"] is False and "share" in verdict["reason"]


def test_a_page_that_never_names_the_event_is_refused():
    verdict = link_guard.check_link_for_event(
        "https://example.com/x", _event(), fetch=lambda u: _page("Brunch Menu | Cafe"))
    assert verdict["accepted"] is False and "never names" in verdict["reason"]


def test_a_login_wall_is_refused():
    verdict = link_guard.check_link_for_event(
        "https://instagram.com/p/x", _event(), fetch=lambda u: _page(title=""))
    assert verdict["accepted"] is False


def test_the_right_page_is_accepted():
    event = _event()
    day = datetime.fromisoformat(event["startDate"]).isoformat()
    verdict = link_guard.check_link_for_event("https://example.com/x", event,
                                              fetch=lambda u: _page(date=day))
    assert verdict["accepted"] is True


def test_a_series_page_for_a_later_recorded_week_is_accepted():
    event = _event(recurring=True, recurrences=[_at(7).isoformat(), _at(14).isoformat()],
                   startDate=_at(7).isoformat())
    verdict = link_guard.check_link_for_event(
        "https://example.com/x", event, fetch=lambda u: _page(date=_at(14).isoformat()))
    assert verdict["accepted"] is True


# ── deterministic fixes ───────────────────────────────────────────────

def test_past_queue_rows_are_dropped_without_a_question(store):
    store.save_pending([_event(id="old", startDate=_at(-5).isoformat(), endDate=_at(-5).isoformat(),
                               _quarantined_new=True)])
    done = wr.drop_past_pending()
    assert store.load_pending() == []
    assert done[0]["event"] == "Tropical Fiesta Social"


def test_a_dead_link_is_replaced_by_a_live_alternate(store, monkeypatch):
    monkeypatch.setattr(link_guard, "link_meta", lambda u: _page())
    store.save_active([_event(url="https://dead.example/a", urls=["https://live.example/b"])])
    actions, still = wr.fix_dead_links({"broken": [{"url": "https://dead.example/a"}],
                                        "ok": [{"url": "https://live.example/b"}]})
    event = store.load_active()[0]
    assert event["url"] == "https://live.example/b"
    assert "https://dead.example/a" in event["_dropped_urls"]
    assert still == set() and actions


def test_a_dead_alternate_is_removed_and_a_dead_primary_becomes_a_question(store):
    store.save_active([_event(url="https://dead.example/a", urls=["https://dead.example/c"])])
    _, still = wr.fix_dead_links({"broken": [{"url": "https://dead.example/a"},
                                             {"url": "https://dead.example/c"}], "ok": []})
    assert store.load_active()[0].get("urls") in (None, [])
    worklist = _worklist(still_broken=still)
    assert _item_for(worklist, "broken_link")["subject"] == "evt-1"


# ── answering ─────────────────────────────────────────────────────────

def test_unknown_choice_is_refused_and_the_question_stays_open(store):
    store.save_pending([_event(_quarantined_new=True)])
    item = _item_for(_worklist(), "new_event")
    result = wr.answer(item["id"], "maybe")
    assert result["ok"] is False and "approve" in result["choices"]
    assert wr.next_item()["item"]["id"] == item["id"]


def test_approve_requires_the_big_event_call(store):
    store.save_pending([_event(_quarantined_new=True)])
    item = _item_for(_worklist(), "new_event")
    assert wr.answer(item["id"], "approve")["ok"] is False
    result = wr.answer(item["id"], "approve", big_event=True, note="benefit night")
    assert result["ok"] is True
    [event] = store.load_active()
    assert event["special"] is True and event["_big_event_reviewed"] is True
    assert wr.next_item().get("done") is True


def test_approve_without_coordinates_requires_an_address(store, monkeypatch):
    store.save_pending([_event(lat=None, lng=None, location="TBA", _quarantined_new=True)])
    item = _item_for(_worklist(), "new_event")
    assert "location" in item["details"]["approve"]
    refused = wr.answer(item["id"], "approve", big_event=False)
    assert refused["ok"] is False and "location" in refused["error"]
    assert wr.answer(item["id"], "approve", big_event=False,
                     location="45 Danforth St, Jamaica Plain, MA")["ok"] is True
    [event] = store.load_active()
    assert event["lat"] == COORDS[0] and event["_location_override"].startswith("45 Danforth")


def test_approve_with_a_wrong_link_changes_nothing(store, monkeypatch):
    store.save_pending([_event(_quarantined_new=True)])
    monkeypatch.setattr(link_guard, "link_meta", lambda u: _page("Other Party", date="2099-01-01T20:00:00"))
    item = _item_for(_worklist(), "new_event")
    result = wr.answer(item["id"], "approve", big_event=False, url="https://example.com/other")
    assert result["ok"] is False and "link refused" in result["error"]
    assert store.load_active() == [] and len(store.load_pending()) == 1


def test_block_choices_block(store):
    store.save_pending([_event(_quarantined_new=True, name="Bachata Fundamentals Class")])
    item = _item_for(_worklist(), "new_event")
    assert wr.answer(item["id"], "class_only")["ok"] is True
    assert store.load_blocked()[0]["blocked_category"] == "class_only"


def test_already_listed_only_accepts_an_offered_event(store):
    store.save_active([_event(id="on-map", source="beatrice-calendar")])
    store.save_pending([_event(id="new", name="Tropical Fiesta Social!", url="https://x.example/n",
                               _quarantined_new=True)])
    item = _item_for(_worklist(), "new_event")
    assert [c["id"] for c in item["evidence"]["same_night"]] == ["on-map"]
    assert wr.answer(item["id"], "already_listed", same_as="made-up")["ok"] is False
    assert wr.answer(item["id"], "already_listed", same_as="on-map")["ok"] is True
    assert [e["id"] for e in store.load_active()] == ["on-map"]
    assert store.load_pending() == []


def test_possible_duplicate_different_keeps_both(store):
    store.save_active([_event(id="series")])
    store.save_pending([_event(id="other", name="Tropical Fiesta Anniversary",
                               url="https://x.example/a", _dedup_candidate_of="series")])
    item = _item_for(_worklist(), "possible_duplicate")
    assert wr.answer(item["id"], "different", big_event=True)["ok"] is True
    assert sorted(e["id"] for e in store.load_active()) == ["other", "series"]


def test_date_conflict_drop_link_removes_only_that_link(store):
    store.save_active([_event(url="https://eb.example/e", urls=["https://www.facebook.com/events/9/"])])
    cross = {"disagreements": [{
        "event_id": "evt-1", "our_date": "x",
        "claims": [{"url": "https://eb.example/e", "date": "a", "location": None, "via": "json-ld"},
                   {"url": "https://www.facebook.com/events/9/", "date": "b", "location": None,
                    "via": "facebook-preview", "city_level": True}],
        "date": {"verdict": "disagree"}, "location": {"verdict": "agree"}}]}
    item = _item_for(_worklist(cross=cross), "link_date_conflict")
    assert wr.answer(item["id"], "drop_link", url="https://not-a-claim.example")["ok"] is False
    assert wr.answer(item["id"], "drop_link", url="https://www.facebook.com/events/9/")["ok"] is True
    event = store.load_active()[0]
    assert event["url"] == "https://eb.example/e" and not event.get("urls")
    assert "https://www.facebook.com/events/9/" in event["_dropped_urls"]


def test_use_source_date_moves_a_one_off_and_keeps_the_time(store):
    event = _event()
    store.save_active([event])
    new_day = _at(12).date().isoformat()
    report = [{"event_id": "evt-1", "status": "date_mismatch", "our_date": "x",
               "source_date": new_day, "source_url": "https://example.com/fiesta"}]
    item = _item_for(_worklist(verification=report), "date_mismatch")
    assert wr.answer(item["id"], "use_source_date")["ok"] is True
    moved = store.load_active()[0]
    assert moved["startDate"].startswith(new_day + "T20:00")
    assert moved["_time_override"]["startDate"] == moved["startDate"]


def test_keep_ours_needs_a_note_and_flags_a_human(store):
    store.save_active([_event()])
    report = [{"event_id": "evt-1", "status": "date_mismatch", "our_date": "a", "source_date": "b"}]
    item = _item_for(_worklist(verification=report), "date_mismatch")
    assert wr.answer(item["id"], "keep_ours")["ok"] is False
    assert wr.answer(item["id"], "keep_ours", note="Eventbrite still shows last year's date")["ok"] is True
    assert "_needs_manual_check" in store.load_active()[0]


def test_big_event_question_asked_once(store):
    store.save_active([_event()])
    item = _item_for(_worklist(), "big_event")
    assert wr.answer(item["id"], "no")["ok"] is True
    assert not [i for i in _worklist()["items"] if i["kind"] == "big_event"]


def test_facebook_signal_adds_at_the_organizers_venue(store, monkeypatch):
    monkeypatch.setattr(scraper_utils, "load_sources", lambda: [{
        "id": "tambo-salsa-fb", "type": "facebook", "enabled": True, "name": "Tambó Salsa Social",
        "facebook_events_url": "https://www.facebook.com/Tambosalsa/events",
        "defaults": {"location": "Dante Alighieri Society, 41 Hampshire St, Cambridge, MA 02139",
                     "styles": ["salsa"], "cost": "$20"}}])
    day = _at(9).date().isoformat()
    item = wr._item("facebook_signal", "tambo-salsa-fb", "Tambó", "?",
                    {"date": day, "what_the_page_says": f"album: Tambo Salsa Social {day}"},
                    {"add_event": "", "not_an_event": ""}, key=f"tambo-salsa-fb:{day}")
    wr.save_worklist({"items": [item]})
    assert wr.answer(item["id"], "add_event")["ok"] is False  # start_time required
    assert wr.answer(item["id"], "add_event", start_time="8:30 PM", end_time="1 AM")["ok"] is True
    [event] = store.load_active()
    assert event["url"] == "https://www.facebook.com/Tambosalsa"
    assert event["startDate"].startswith(f"{day}T20:30")
    assert event["location"].startswith("Dante Alighieri")


def test_not_an_event_is_not_asked_again(store):
    import event_doctor

    day = _at(9).date().isoformat()
    item = wr._item("facebook_signal", "bobas", "BOBAS", "?", {"date": day},
                    {"add_event": "", "not_an_event": ""}, key=f"bobas:{day}")
    wr.save_worklist({"items": [item]})
    assert wr.answer(item["id"], "not_an_event", note="post timestamp")["ok"] is True
    assert ("bobas", day) in event_doctor.load_signal_dismissals()


def test_summary_puts_what_needs_a_human_first(store):
    store.save_pending([_event(_quarantined_new=True)])
    worklist = _worklist()
    wr.skip(worklist["items"][0]["id"], "the flyer is unreadable, check with organizer")
    text = wr.render_summary(wr.load_worklist(), {"tripped": False, "published_live_events": 40,
                                                   "previous_live_events": 39, "retired_urls": 3}, None)
    needs = text.index("## Needs you")
    assert "the flyer is unreadable" in text[needs:text.index("## Decisions")]
    assert "40 live events" in text


def test_a_structured_cancellation_is_archived_without_a_question(store):
    store.save_active([_event(url="https://www.eventbrite.com/e/1")])
    report = [{"event_id": "evt-1", "status": "cancelled", "source_url": "https://www.eventbrite.com/e/1",
               "notes": wr.STRUCTURED_CANCELLATION}]
    done = wr.archive_structured_cancellations(report)
    assert store.load_active() == [] and done[0]["event"] == "Tropical Fiesta Social"


def test_a_text_cancellation_is_still_a_question(store):
    store.save_active([_event()])
    report = [{"event_id": "evt-1", "status": "cancelled", "source_url": "https://example.com/fiesta",
               "notes": "page mentions 'cancelled'"}]
    assert wr.archive_structured_cancellations(report) == []
    assert _item_for(_worklist(verification=report), "cancelled")


def test_events_flagged_for_a_human_get_no_agent_questions(store):
    store.save_active([_event(_needs_manual_check={"reason": "call the organizer"})])
    report = [{"event_id": "evt-1", "status": "date_mismatch", "our_date": "a", "source_date": "b"}]
    assert _worklist(verification=report)["items"] == []


def test_per_date_copies_of_a_series_are_not_big_event_questions(store):
    store.save_active([_event(id="a"), _event(id="b", startDate=_at(17).isoformat(),
                                              endDate=_at(17, 23).isoformat())])
    assert not [i for i in _worklist()["items"] if i["kind"] == "big_event"]


@pytest.mark.parametrize('params', [{'big_event': 'perhaps'}, {'big_event': False, 'styles': [123]}])
def test_invalid_approval_does_not_move_or_modify_event(store, params):
    pending = [_event(_quarantined_new=True)]
    store.save_pending(pending)
    item = _item_for(_worklist(), 'new_event')
    result = wr.answer(item['id'], 'approve', **params)
    assert result['ok'] is False
    assert store.load_active() == []
    assert store.load_pending() == pending


def test_distinct_approval_uses_the_requested_address(store):
    store.save_active([_event(id='series')])
    store.save_pending([_event(id='other', name='Tropical Fiesta Anniversary',
                              lat=None, lng=None, location='TBA',
                              url='https://example.com/other', _dedup_candidate_of='series')])
    item = _item_for(_worklist(), 'possible_duplicate')
    assert wr.answer(item['id'], 'different', big_event=True)['ok'] is False
    assert len(store.load_pending()) == 1
    result = wr.answer(item['id'], 'different', big_event=True,
                       location='45 Danforth St, Jamaica Plain, MA')
    assert result['ok'] is True
    added = next(e for e in store.load_active() if e['id'] == 'other')
    assert added['location'].startswith('45 Danforth')
    assert added['lat'] == COORDS[0]
    assert store.load_pending() == []


def test_failed_distinct_approval_keeps_the_pending_record(store, monkeypatch):
    store.save_active([_event(id='series')])
    pending = [_event(id='other', _dedup_candidate_of='series')]
    store.save_pending(pending)
    item = _item_for(_worklist(), 'possible_duplicate')
    monkeypatch.setattr(wr, 'add_event', lambda *a, **kw: {'status': 'rejected', 'message': 'cannot add'})
    assert wr.answer(item['id'], 'different', big_event=False)['ok'] is False
    assert store.load_pending() == pending


def test_same_venue_is_not_the_same_event():
    verdict = link_guard.check_link_for_event('https://example.com/other', _event(),
        fetch=lambda u: _page('Havana Club Brunch', date=_at(10).isoformat()))
    assert verdict['accepted'] is False


def test_calendar_cannot_mix_one_events_name_with_another_events_date():
    meta = _page('Calendar')
    meta['jsonld_events'] = [
        {'name': 'Tropical Fiesta Social', 'startDate': _at(11).isoformat()},
        {'name': 'Unrelated Brunch', 'startDate': _at(10).isoformat()},
    ]
    verdict = link_guard.check_link_for_event('https://example.com/calendar', _event(), fetch=lambda u: meta)
    assert verdict['accepted'] is False


def test_event_without_identifying_words_cannot_accept_an_arbitrary_page():
    verdict = link_guard.check_link_for_event('https://example.com/other',
        _event(name='Salsa', location=''), fetch=lambda u: _page('Unrelated Brunch'))
    assert verdict['accepted'] is False


def test_series_link_must_match_an_actual_occurrence():
    event = _event(recurring=True, recurrences=[_at(7).isoformat(), _at(14).isoformat()],
                   startDate=_at(7).isoformat())
    verdict = link_guard.check_link_for_event('https://example.com/other', event,
        fetch=lambda u: _page(date=_at(63).isoformat()))
    assert verdict['accepted'] is False


def test_finish_refuses_unanswered_work_without_publishing(store, monkeypatch):
    store.save_pending([_event(_quarantined_new=True)])
    _worklist()
    monkeypatch.setattr(wr, 'publish_guarded', lambda: pytest.fail('must not publish'))
    assert wr.finish(run_checks=False) == 1
    assert 'Not published' in wr.SUMMARY_PATH.read_text()


def test_finish_checks_current_state_before_publish(store, monkeypatch, tmp_path):
    import check_links
    import event_doctor
    import verify_events

    wr.save_worklist({'items': []})
    calls = []
    monkeypatch.setattr(verify_events, 'verify_all', lambda **kw: calls.append('verify'))
    monkeypatch.setattr(check_links, 'REPORT_PATH', tmp_path / 'links.json')
    monkeypatch.setattr(check_links, 'check_all', lambda **kw: {})
    monkeypatch.setattr(event_doctor, 'run_doctor', lambda **kw: {
        'ok': False, 'status': 'blocked', 'checks': {'verification': {
            'status': 'blocker', 'count': 1, 'message': 'Newly approved event has no evidence'}}})
    monkeypatch.setattr(wr, 'publish_guarded', lambda: pytest.fail('must not publish'))
    assert wr.finish() == 1
    assert calls == ['verify']
    assert 'Newly approved event' in wr.SUMMARY_PATH.read_text()


def test_rejected_approval_restores_address_edits(store, monkeypatch):
    pending = [_event(lat=None, lng=None, location='TBA', _quarantined_new=True)]
    store.save_pending(pending)
    item = _item_for(_worklist(), 'new_event')
    monkeypatch.setattr(wr, 'approve_pending', lambda *a: {'status': 'not_approved', 'message': 'not allowed'})
    result = wr.answer(item['id'], 'approve', big_event=False, location='45 Danforth St, Jamaica Plain, MA')
    assert result['ok'] is False
    assert store.load_pending() == pending


def test_working_alternate_for_another_event_is_not_promoted(store, monkeypatch):
    store.save_active([_event(url='https://dead.example/a', urls=['https://live.example/brunch'])])
    monkeypatch.setattr(link_guard, 'link_meta', lambda u: _page('Havana Club Brunch'))
    _, still = wr.fix_dead_links({'broken': [{'url': 'https://dead.example/a'}],
                                  'ok': [{'url': 'https://live.example/brunch'}]})
    assert store.load_active()[0]['url'] == 'https://dead.example/a'
    assert still


def test_matching_structured_event_on_a_calendar_is_accepted():
    meta = _page('Calendar')
    meta['jsonld_events'] = [
        {'name': 'Tropical Fiesta Social', 'startDate': _at(10).isoformat()},
        {'name': 'Unrelated Brunch', 'startDate': _at(11).isoformat()},
    ]
    assert link_guard.check_link_for_event('https://example.com/calendar', _event(),
                                           fetch=lambda u: meta)['accepted'] is True


@pytest.mark.parametrize('tripped', [False, True])
def test_finish_publishes_only_after_checks_and_preserves_tripwire(store, monkeypatch, tmp_path, tripped):
    import check_links
    import event_doctor
    import verify_events

    wr.save_worklist({'items': []})
    calls = []
    monkeypatch.setattr(verify_events, 'verify_all', lambda **kw: calls.append('verify'))
    monkeypatch.setattr(check_links, 'REPORT_PATH', tmp_path / 'links.json')
    monkeypatch.setattr(check_links, 'check_all', lambda **kw: calls.append('links') or {})
    monkeypatch.setattr(event_doctor, 'run_doctor', lambda **kw: calls.append('doctor') or {'ok': True})
    monkeypatch.setattr(wr, 'publish_guarded', lambda: calls.append('publish') or {'tripped': tripped})
    assert wr.finish() == (2 if tripped else 0)
    assert calls == ['verify', 'links', 'doctor', 'publish']


def test_venue_word_in_event_name_does_not_identify_a_different_event():
    verdict = link_guard.check_link_for_event('https://example.com/brunch',
        _event(name='Salsa at Havana'), fetch=lambda u: _page('Havana Club Brunch', date=_at(10).isoformat()))
    assert verdict['accepted'] is False


def test_summary_lists_verification_blockers_as_action_needed():
    text = wr.render_summary({'items': []}, {'blocked': True, 'message': 'Doctor blocked'}, {
        'status': 'blocked', 'checks': {'verification': {'status': 'blocker', 'message': 'Needs evidence',
        'items': [{'name': 'Party with no link', 'problem': 'no_source'}]}}})
    needs = text.split('## Needs you')[1].split('## Decisions')[0]
    assert 'Party with no link' in needs and 'no_source' in needs
    assert '- Nothing.' not in needs


def test_finish_returns_tripwire_exit_without_touching_published_files(store, monkeypatch, tmp_path):
    import check_links
    import event_doctor
    import verify_events

    wr.save_worklist({'items': []})
    monkeypatch.setattr(verify_events, 'verify_all', lambda **kw: None)
    monkeypatch.setattr(check_links, 'REPORT_PATH', tmp_path / 'links.json')
    monkeypatch.setattr(check_links, 'check_all', lambda **kw: {})
    monkeypatch.setattr(event_doctor, 'run_doctor', lambda **kw: {
        'ok': False, 'status': 'blocked', 'checks': {'publish_tripwire': {
            'status': 'blocker', 'message': 'live count collapsed'}}})
    monkeypatch.setattr(wr, 'publish_guarded', lambda: pytest.fail('must not publish'))
    assert wr.finish() == 2
    assert 'live count collapsed' in wr.SUMMARY_PATH.read_text()
