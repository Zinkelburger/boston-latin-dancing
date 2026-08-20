"""Link quality and big-event visibility — the two bugs that made
"Battle of the Beats 2026: BOSTON" unreachable on the site.

1. A merge picked the primary URL from whichever record won on SOURCE_PRIORITY,
   so beatrice-calendar (rank 10, ships Facebook share wrappers) replaced the
   organizer's canonical listerevents page (rank 12) with a link that does not
   resolve for logged-out visitors.
2. Venue-hub suppression exempted only names matching _SPECIAL_EDITION_RE, so a
   plainly-named marquee one-off at a venue with a weekly night on the same
   weekday was dropped from the published feed entirely.
"""

import event_store as es

FB_SHARE = "https://facebook.com/events/s/battle-of-the-beats-2026-bosto/1613687936940528/"
FB_EVENT = "https://www.facebook.com/events/1299596699048505/"
CANONICAL = "https://www.listerevents.com/event-details/battle-of-the-beats-2026-latin-vs-hip-hop"


def _event(**overrides):
    ev = {
        "id": "botb",
        "name": "Battle of the Beats 2026: BOSTON",
        "startDate": "2026-08-15T20:30:00+00:00",
        "endDate": "2026-08-16T00:30:00+00:00",
        "location": "288 Green St, Cambridge, MA 02139-3312, United States",
        "lat": 42.3646071,
        "lng": -71.1043523,
        "recurring": False,
        "styles": ["salsa", "bachata"],
    }
    ev.update(overrides)
    return ev


# ── url_rank ──────────────────────────────────────────────────────────

def test_share_wrapper_ranks_worst():
    assert es.url_rank(FB_SHARE) > es.url_rank(FB_EVENT) > es.url_rank(CANONICAL)


def test_share_slash_form_also_ranked_as_wrapper():
    assert es.url_rank("https://www.facebook.com/share/1EjEyfCyhA") == es.url_rank(FB_SHARE)


def test_organizer_page_beats_instagram():
    assert es.url_rank(CANONICAL) < es.url_rank("https://www.instagram.com/timbadescontrol/")


def test_missing_url_ranks_last():
    assert es.url_rank("") > es.url_rank(FB_SHARE)


# ── merge never downgrades the primary link ───────────────────────────

def _beatrice():
    return _event(id="75AB", source="beatrice-calendar", url=FB_SHARE)


def _lister():
    return _event(id="lister-botb", name="Battle of the Beats 2026: Latin vs. Hip Hop",
                  source="lister-events", url=CANONICAL)


def test_merge_promotes_canonical_over_share_wrapper():
    merged = es.merge_event(_beatrice(), _lister())
    assert merged["url"] == CANONICAL
    assert FB_SHARE in merged["urls"]


def test_merge_url_choice_is_order_independent():
    a = es.merge_event(_beatrice(), _lister())
    b = es.merge_event(_lister(), _beatrice())
    assert a["url"] == b["url"] == CANONICAL


def test_collapse_prefers_next_occurrence_url_on_same_host():
    """A closed past Lister page must not stay primary after a later date
    of the same series (with a live listing) collapses into it."""
    old = _event(
        id="beatrice-river",
        name="Bachata & Salsa By The River",
        source="beatrice-calendar",
        startDate="2026-07-05T18:00:00-04:00",
        url="https://www.listerevents.com/event-details/bachata-salsa-by-the-river-3",
        recurring=True,
        recurrences=[
            "2026-07-05T18:00:00-04:00",
            "2026-09-06T17:00:00-04:00",
        ],
        location="Magazine Beach Park, 668 Memorial Dr, Cambridge, MA",
    )
    nxt = _event(
        id="lister-sep",
        name="EARLIER TIME: Bachata & Salsa By The River",
        source="lister-events",
        startDate="2026-09-06T17:00:00-04:00",
        url="https://www.listerevents.com/event-details/earlier-time-bachata-salsa-by-the-river",
        location="The Nature Center @ Magazine Beach Park, 668 Memorial Dr, Cambridge, MA",
    )
    result = es.collapse_recurring_series([old, nxt])
    assert len(result) == 1
    assert result[0]["id"] == "beatrice-river"
    assert result[0]["url"] == nxt["url"]


def test_collapse_next_occurrence_url_wins_even_when_the_winner_carries_it():
    """The winning record usually already carries the next night's URL in its
    own urls[] (a previous merge put it there). Attributing "belongs to the
    next occurrence" to only the first record that mentions a URL left the
    closed July page primary."""
    old = _event(
        id="beatrice-river",
        name="Bachata & Salsa By The River",
        source="beatrice-calendar",
        startDate="2026-07-05T18:00:00-04:00",
        endDate="2026-07-05T21:00:00-04:00",
        url="https://www.listerevents.com/event-details/by-the-river-july",
        urls=["https://www.listerevents.com/event-details/by-the-river-sept"],
        recurring=True,
        recurrences=[
            "2026-07-05T18:00:00-04:00",
            "2026-09-06T17:00:00-04:00",
        ],
        location="Magazine Beach Park, 668 Memorial Dr, Cambridge, MA",
    )
    nxt = _event(
        id="lister-sep",
        name="Bachata & Salsa By The River",
        source="lister-events",
        startDate="2026-09-06T17:00:00-04:00",
        endDate="2026-09-06T20:00:00-04:00",
        url="https://www.listerevents.com/event-details/by-the-river-sept",
        location="Magazine Beach Park, 668 Memorial Dr, Cambridge, MA",
    )
    result = es.collapse_recurring_series([old, nxt])
    assert len(result) == 1
    assert result[0]["url"] == nxt["url"]


def test_merge_still_respects_source_precedence_for_the_record():
    # beatrice-calendar (10) outranks lister-events (12): it must still win the
    # *record*. Only the link selection was ever meant to change.
    for merged in (es.merge_event(_beatrice(), _lister()),
                   es.merge_event(_lister(), _beatrice())):
        assert merged["id"] == "75AB"


def test_merge_keeps_sole_url_when_nothing_better_exists():
    a = _event(id="a", source="beatrice-calendar", url=FB_SHARE)
    b = _event(id="b", source="lister-events", url=None)
    assert es.merge_event(a, b)["url"] == FB_SHARE


# ── venue hubs must not swallow big events ────────────────────────────

HAVANA_HUB = {
    "id": "havana-club",
    "name": "Havana Club",
    "location": "288 Green St, Cambridge, MA 02139, USA",
    "lat": 42.3646071,
    "lng": -71.1043523,
    "schedule": [{"dayOfWeek": "Saturday", "time": "9:00 PM – 2:00 AM", "note": "70% Bachata"}],
}


def _kept_ids(active):
    kept, _venues, _report = es._suppress_venue_covered_events([HAVANA_HUB], active)
    return {e["id"] for e in kept}


def test_explicit_special_survives_venue_suppression():
    # Saturday one-off at a venue whose hub also runs Saturday nights.
    assert "botb" in _kept_ids([_event(special=True)])


def test_afternoon_event_survives_without_any_flag():
    # The flag was only ever a patch. The real signal is the clock: 4:30–8:30 PM
    # against a 9 PM–2 AM hub is not the venue's night, whatever it is called.
    assert "botb" in _kept_ids([_event()])


def test_plain_weekly_night_is_still_suppressed():
    # The exemption must not become a blanket pass — an ordinary Saturday
    # social running the venue's own hours still collapses into the hub.
    weekly = _event(id="weekly", name="Bachata Saturdays @ Havana Club",
                    startDate="2026-08-16T01:00:00+00:00",   # 9:00 PM EDT Sat
                    endDate="2026-08-16T06:00:00+00:00")     # 2:00 AM EDT Sun
    assert "weekly" not in _kept_ids([weekly])
