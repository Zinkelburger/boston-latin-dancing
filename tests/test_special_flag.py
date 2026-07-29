"""The published `special` flag: big one-off events (festivals, annual
editions) get flagged for the frontend's Big-events filter, explicit
overrides win, and manual flags survive dedup merges."""

import event_store as es


def _event(**overrides):
    ev = {
        "id": "test-1",
        "name": "Boston Salsa Festival, 2026",
        "startDate": "2026-08-21T00:00:00-04:00",
        "endDate": "2026-08-22T00:00:00-04:00",
        "location": "70 Third Avenue, Waltham, MA 02451",
        "recurring": False,
        "styles": ["salsa"],
    }
    ev.update(overrides)
    return ev


def test_festival_one_off_gets_flagged():
    ev = _event()
    es._derive_special(ev)
    assert ev["special"] is True


def test_annual_edition_gets_flagged():
    ev = _event(name="12th Annual Salsa Squared")
    es._derive_special(ev)
    assert ev["special"] is True


def test_plain_social_not_flagged():
    ev = _event(name="Salsa & Bachata Social w/ Fiesta Dance Co")
    es._derive_special(ev)
    assert "special" not in ev


def test_guest_dj_night_not_flagged():
    # Special *edition* of a series (ft./takeover) is not a big event.
    ev = _event(name="Salsa Night ft. DJ Mambo")
    es._derive_special(ev)
    assert "special" not in ev


def test_recurring_series_never_flagged_by_heuristic():
    ev = _event(name="Festival Fridays Weekly Social", recurring=True)
    es._derive_special(ev)
    assert "special" not in ev


def test_festival_pre_party_not_flagged():
    # A satellite party carries the festival's name but is a regular social.
    ev = _event(name="Pre-Party: Boston Salsa Festival 2026")
    es._derive_special(ev)
    assert "special" not in ev


def test_after_party_not_flagged():
    ev = _event(name="Boston Salsa Festival Afterparty")
    es._derive_special(ev)
    assert "special" not in ev


def test_explicit_true_wins_on_satellite_party():
    ev = _event(name="Pre-Party: Boston Salsa Festival 2026", special=True)
    es._derive_special(ev)
    assert ev["special"] is True


def test_explicit_true_wins_without_keyword():
    ev = _event(name="Salsa at the Shell", special=True)
    es._derive_special(ev)
    assert ev["special"] is True


def test_explicit_false_suppresses_heuristic_and_ships_absent():
    ev = _event(special=False)
    es._derive_special(ev)
    assert "special" not in ev


def test_strip_internal_fields_derives_special():
    ev = _event()
    es._strip_internal_fields(ev, {})
    assert ev["special"] is True


def test_merge_preserves_manual_flag_from_loser():
    stored = _event(special=True, name="Salsa at the Shell", source="manual")
    scraped = _event(
        id="test-2",
        name="Salsa at the Shell",
        source="golatindance-boston",
        url="https://example.com/shell",
    )
    merged = es.merge_event(scraped, stored)
    assert merged["special"] is True
