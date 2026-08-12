"""Venue-hub collisions: fold only the obvious, queue the rest.

A venue hub (generated from venues.json) and a scraped event can land on the
same dot on the same weekday. Folding the scrape into the hub is the right call
for the venue's own weekly night and catastrophic for anything else — a deleted
event is invisible to visitors and to the pipeline's own success reporting,
which is how "Battle of the Beats 2026: BOSTON" stayed missing for a week.

So suppression now needs all of: same place, same weekday, overlapping clock
times, and a name that reads like the venue's night. Everything else stays on
the map and goes to a review queue.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import event_store as es


HAVANA_HUB = {
    "id": "havana-club",
    "name": "Havana Club",
    "location": "288 Green St, Cambridge, MA 02139, USA",
    "lat": 42.3646071,
    "lng": -71.1043523,
    "cost": "$15",
    "url": "https://havanaclubsalsa.com",
    "description": "Boston's hot spot for Bachata and Salsa dancing.",
    "schedule": [
        {"dayOfWeek": "Saturday", "time": "9:00 PM – 2:00 AM", "note": "70% Bachata (21+)"},
        {"dayOfWeek": "Sunday", "time": "7:00 PM – 10:00 PM", "note": "90% Bachata"},
    ],
}


def _event(**overrides):
    """A Saturday event at Havana Club's address. Afternoon by default."""
    ev = {
        "id": "botb",
        "name": "Battle of the Beats 2026: BOSTON",
        "startDate": "2026-08-15T16:30:00-04:00",
        "endDate": "2026-08-15T20:30:00-04:00",
        "location": "288 Green St, Cambridge, MA 02139-3312, United States",
        "lat": 42.3646071,
        "lng": -71.1043523,
        "recurring": False,
        "styles": ["salsa", "bachata"],
    }
    ev.update(overrides)
    return ev


def _run(active, hub=HAVANA_HUB):
    kept, _venues, report = es._suppress_venue_covered_events([hub], active)
    return {e["id"] for e in kept}, report


def _weekly_night(**overrides):
    """The venue's own Saturday night as a source scrape: same name, same hours."""
    base = {"id": "weekly", "name": "Bachata Saturdays @ Havana Club",
            "startDate": "2026-08-15T21:00:00-04:00",
            "endDate": "2026-08-16T02:00:00-04:00"}
    base.update(overrides)
    return _event(**base)


# ── the clock decides, not the name ───────────────────────────────────

def test_weekly_night_at_the_hub_hours_is_folded_in():
    kept, report = _run([_weekly_night()])
    assert "weekly" not in kept
    assert [r["event"]["name"] for r in report["suppressed"]] == ["Bachata Saturdays @ Havana Club"]
    assert report["conflicts"] == []


def test_afternoon_program_is_kept_and_queued():
    # 4:30–8:30 PM against a 9 PM–2 AM hub: no overlap, so this is a distinct
    # event that happens to share a building and a weekday.
    kept, report = _run([_event()])
    assert "botb" in kept
    assert len(report["conflicts"]) == 1
    assert report["conflicts"][0]["times_overlap"] is False
    assert report["suppressed"] == []


def test_touching_windows_do_not_count_as_overlap():
    # Ends exactly when the hub opens — the hand-off shape, not the same party.
    kept, _ = _run([_event(endDate="2026-08-15T21:00:00-04:00")])
    assert "botb" in kept


def test_partial_overlap_counts_as_overlap():
    late = _event(id="late", name="Bachata Saturdays @ Havana Club",
                  startDate="2026-08-15T20:00:00-04:00",
                  endDate="2026-08-15T23:00:00-04:00")
    kept, _ = _run([late])
    assert "late" not in kept


def test_different_weekday_is_never_a_collision():
    friday = _weekly_night(id="fri", startDate="2026-08-14T21:00:00-04:00",
                           endDate="2026-08-15T02:00:00-04:00")
    kept, report = _run([friday])
    assert "fri" in kept
    assert report["conflicts"] == [] and report["suppressed"] == []


# ── ambiguity resolves toward keeping the event ───────────────────────

def test_unparseable_hub_time_queues_instead_of_deleting():
    hub = json.loads(json.dumps(HAVANA_HUB))
    hub["schedule"][0]["time"] = "evenings"
    kept, report = _run([_weekly_night()], hub=hub)
    assert "weekly" in kept
    assert report["conflicts"][0]["times_overlap"] is None
    assert "not parseable" in report["conflicts"][0]["overlap_unknown_because"]


def test_missing_end_time_is_assumed_and_disclosed():
    ev = _weekly_night(endDate=None)
    kept, report = _run([ev])
    assert "weekly" not in kept              # 9 PM start still lands inside the hub window
    assert report["suppressed"][0]["event_end_assumed"] is True


def test_takeover_at_hub_hours_is_queued_not_folded():
    # Overlapping times AND the venue's name in the title — but "Anniversary
    # Takeover" is a branded one-off. Whether it replaces the regular night is
    # exactly the judgment call a regex should not be making.
    takeover = _weekly_night(id="takeover", name="Havana Club 10 Year Anniversary Takeover")
    kept, report = _run([takeover])
    assert "takeover" in kept
    assert len(report["conflicts"]) == 1


def test_flagged_big_event_is_never_folded_even_at_hub_hours():
    ev = _weekly_night(id="big", name="Bachata Saturdays @ Havana Club", special=True)
    kept, report = _run([ev])
    assert "big" in kept
    assert len(report["conflicts"]) == 1


# ── recorded decisions stick ──────────────────────────────────────────

def test_recorded_distinct_keeps_event_and_clears_the_queue():
    ev = _event(_venue_conflict_decision={"hub": "havana-club", "decision": "distinct"})
    kept, report = _run([ev])
    assert "botb" in kept
    assert report["conflicts"] == []


def test_recorded_duplicate_folds_even_without_time_overlap():
    ev = _event(_venue_conflict_decision={"hub": "havana-club", "decision": "duplicate"})
    kept, report = _run([ev])
    assert "botb" not in kept
    assert report["conflicts"] == []
    assert report["suppressed"][0]["resolved"]["decision"] == "duplicate"


def test_decision_for_a_different_hub_does_not_apply():
    ev = _event(_venue_conflict_decision={"hub": "some-other-venue", "decision": "duplicate"})
    kept, report = _run([ev])
    assert "botb" in kept
    assert len(report["conflicts"]) == 1


def test_decision_survives_a_rescrape_merge():
    stored = _event(source="lister-events",
                    _venue_conflict_decision={"hub": "havana-club", "decision": "distinct"})
    fresh = _event(source="beatrice-calendar")
    for merged in (es.merge_event(fresh, stored), es.merge_event(stored, fresh)):
        assert merged["_venue_conflict_decision"]["decision"] == "distinct"


# ── the row carries enough to decide on its own ───────────────────────

def test_review_row_carries_both_sides():
    _kept, report = _run([_event(cost="$20", source="beatrice-calendar",
                                description="4:30 PM: Workshops\n7:00 PM: Social Dance Party")])
    row = report["conflicts"][0]
    assert row["event"]["window"] == "Sat Aug 15, 4:30 PM – 8:30 PM"
    assert row["hub"]["window"] == "Saturdays, 9:00 PM – 2:00 AM"
    assert row["hub"]["note"] == "70% Bachata (21+)"
    assert row["hub"]["also_runs"] == ["Sun"]
    assert row["event"]["cost"] == "$20" and row["hub"]["cost"] == "$15"
    assert row["currently"].startswith("published")
    assert row["if_you_do_nothing"]


def test_review_row_surfaces_the_run_of_show():
    # The decisive evidence usually sits past any truncation point.
    desc = ("blurb " * 200) + "\n4:30 PM: Choose from one of TWO Workshops\n5:30 PM: The Battle Begins!"
    _kept, report = _run([_event(description=desc)])
    assert report["conflicts"][0]["event"]["schedule_in_description"] == [
        "4:30 PM: Choose from one of TWO Workshops",
        "5:30 PM: The Battle Begins!",
    ]


def test_review_row_carries_no_recommendation():
    # Facts only. A precomputed verdict is the old name-regex in disguise.
    _kept, report = _run([_event()])
    row = json.dumps(report["conflicts"][0]).lower()
    for banned in ("recommend", "suggest", "probably", "likely", "verdict"):
        assert banned not in row


# ── resolution writes through ─────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    monkeypatch.setattr(es, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(es, "ACTIVE_JSON", events_dir / "active.json")
    monkeypatch.setattr(es, "CHANGELOG", events_dir / "changelog.jsonl")
    monkeypatch.setattr(es, "VENUE_CONFLICTS_JSON", events_dir / "venue-conflicts.json")
    venues = tmp_path / "venues.json"
    venues.write_text(json.dumps([{"id": "havana-club", "name": "Havana Club",
                                   "schedule": HAVANA_HUB["schedule"]}]))
    monkeypatch.setattr(es, "VENUES_JSON", venues)
    es.save_active([_event()])
    es._write_venue_conflicts({"conflicts": [{"id": "botb", "hub": {"id": "havana-club"},
                                              "event": {"name": "Battle of the Beats"}}],
                               "suppressed": []})
    return es


def test_resolve_records_the_decision_on_the_event(store):
    result = store.resolve_venue_conflict("botb", "distinct", note="afternoon program")
    assert result["status"] == "resolved"
    decision = store.load_active()[0]["_venue_conflict_decision"]
    assert decision == {"hub": "havana-club", "decision": "distinct",
                        "at": decision["at"], "note": "afternoon program"}


def test_resolve_replaces_tells_the_hub_to_skip_that_date(store):
    result = store.resolve_venue_conflict("botb", "replaces")
    assert result["hub_date_excluded"] == "2026-08-15"
    venues = json.loads(store.VENUES_JSON.read_text())
    assert venues[0]["excludeDates"] == ["2026-08-15"]


def test_resolve_rejects_an_unknown_decision(store):
    assert store.resolve_venue_conflict("botb", "ignore")["status"] == "error"


def test_resolve_reports_an_unknown_event(store):
    assert store.resolve_venue_conflict("nope", "distinct")["status"] == "error"
