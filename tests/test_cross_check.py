"""Cross-source agreement.

The bug this exists for: "BachaTipico Hangout" sat at Suegra's Molino Lounge
while its own Partiful page said Gran Peñol, 2.3km away. Nothing compared the
two, so the map was wrong for as long as nobody clicked through.

The hard part is not finding disagreements, it is not inventing them. Sources
phrase addresses completely differently and Facebook only ever names a town,
so a naive comparison flags almost everything. This queues work for a human,
and a queue full of false alarms is a queue nobody reads — so anything
unresolved is "unknown", never "disagree".
"""

import pytest

import cross_check as cc

HAVANA_A = "Havana Club, 288 Green St, Cambridge, MA 02139, USA"
HAVANA_B = "288 Green St, Cambridge, MA 02139-3312, United States"
GRAN_PENOL = "Gran Peñol Restaurant & Bar, 151 Central Ave, Lynn, MA 01901"
SUEGRAS = "Suegra's Molino Lounge, 937 Western Ave, Lynn, MA 01905, United States"

COORDS = {
    GRAN_PENOL: (42.4644805, -70.949976),
    SUEGRAS: (42.4568295, -70.9725536),
    "Distillery Gallery": (42.34, -71.055),
    "Boston, MA": (42.3601, -71.0589),
    "Portland, ME": (43.6591, -70.2568),
}


@pytest.fixture
def geocoded(monkeypatch):
    """Geocode from a fixed table — no network, no Nominatim."""
    monkeypatch.setattr(cc, "geocode", lambda s: COORDS.get(s))


# ── comparing addresses ───────────────────────────────────────────────

def test_identical_text_agrees_without_geocoding():
    assert cc.locations_agree(HAVANA_A, HAVANA_A, allow_geocode=False)[0] == cc.AGREE


def test_same_street_number_agrees():
    # The two sources write this venue completely differently, but "288 Green"
    # is shared and that is strong enough without a round trip.
    assert cc.locations_agree(HAVANA_A, HAVANA_B, allow_geocode=False)[0] == cc.AGREE


def test_missing_location_is_unknown_not_disagreement():
    assert cc.locations_agree("", HAVANA_A)[0] == cc.UNKNOWN
    assert cc.locations_agree(HAVANA_A, None)[0] == cc.UNKNOWN


def test_text_difference_alone_is_never_a_disagreement():
    # Without coordinates we have no basis to condemn two different strings.
    assert cc.locations_agree(GRAN_PENOL, SUEGRAS, allow_geocode=False)[0] == cc.UNKNOWN


def test_the_real_bug_is_caught(geocoded):
    verdict, why = cc.locations_agree(SUEGRAS, GRAN_PENOL)
    assert verdict == cc.DISAGREE
    assert "km apart" in why


def test_ungeocodable_pair_is_unknown(geocoded):
    assert cc.locations_agree("Nowhere In Particular", GRAN_PENOL)[0] == cc.UNKNOWN


def test_zip_code_is_not_read_as_a_street_number():
    # Two Lynn venues share the "01901"-style zip shape; matching on five
    # digits would call every address in a zip code the same place.
    assert "01901" not in cc._street_numbers(GRAN_PENOL)
    assert "151" in cc._street_numbers(GRAN_PENOL)


# ── city-level claims ─────────────────────────────────────────────────

def test_a_city_claim_does_not_condemn_a_venue(geocoded):
    # Facebook says only "Boston, MA", which geocodes to the city centroid
    # 2.1km from the Distillery Gallery. At venue precision every
    # Facebook-linked event in Boston reads as a disagreement.
    venue_precision, _ = cc.locations_agree("Distillery Gallery", "Boston, MA")
    town_precision, _ = cc.locations_agree(
        "Distillery Gallery", "Boston, MA", radius_km=cc.SAME_TOWN_KM)
    assert venue_precision == cc.DISAGREE
    assert town_precision == cc.AGREE


def test_the_wrong_town_still_fails_at_city_precision(geocoded):
    # The town radius must stay narrow enough to catch a genuine mix-up.
    verdict, _ = cc.locations_agree(
        "Distillery Gallery", "Portland, ME", radius_km=cc.SAME_TOWN_KM)
    assert verdict == cc.DISAGREE


# ── combining verdicts ────────────────────────────────────────────────

def test_one_disagreement_outweighs_agreement():
    assert cc._combine([cc.AGREE, cc.AGREE, cc.DISAGREE]) == cc.DISAGREE


def test_no_evidence_is_unknown():
    assert cc._combine([]) == cc.UNKNOWN
    assert cc._combine([cc.UNKNOWN, cc.UNKNOWN]) == cc.UNKNOWN


def test_agreement_needs_at_least_one_positive():
    assert cc._combine([cc.UNKNOWN, cc.AGREE]) == cc.AGREE


# ── whole-event checks ────────────────────────────────────────────────

def _event(**over):
    ev = {
        "id": "e1", "name": "Test Social", "location": SUEGRAS,
        "startDate": "2026-08-13T23:00:00+00:00", "url": "https://partiful.com/e/x",
    }
    ev.update(over)
    return ev


def _claim(**over):
    c = {"url": "https://partiful.com/e/x", "location": None, "date": None,
         "via": "json-ld", "city_level": False, "error": None}
    c.update(over)
    return c


def test_event_with_no_urls_is_unknown_not_disagreement():
    result = cc.cross_check_event(_event(url=None))
    assert result["location"]["verdict"] == cc.UNKNOWN
    assert result["source_count"] == 0


def test_source_contradicting_us_is_flagged(monkeypatch, geocoded):
    monkeypatch.setattr(cc, "source_claim", lambda u: _claim(location=GRAN_PENOL))
    result = cc.cross_check_event(_event())
    assert result["location"]["verdict"] == cc.DISAGREE


def test_source_confirming_us_agrees(monkeypatch, geocoded):
    monkeypatch.setattr(cc, "source_claim", lambda u: _claim(location=SUEGRAS))
    assert cc.cross_check_event(_event())["location"]["verdict"] == cc.AGREE


def test_sources_disagreeing_with_each_other_is_flagged(monkeypatch, geocoded):
    # Both could differ from us while also contradicting each other; that
    # still means an upstream listing is wrong and someone should look.
    claims = {"https://a.example/x": _claim(url="https://a.example/x", location=GRAN_PENOL),
              "https://b.example/x": _claim(url="https://b.example/x", location=SUEGRAS)}
    monkeypatch.setattr(cc, "source_claim", lambda u: claims[u])
    ev = _event(location="", url="https://a.example/x", urls=["https://b.example/x"])
    result = cc.cross_check_event(ev)
    assert result["location"]["verdict"] == cc.DISAGREE
    assert any("disagree with each other" in n for n in result["location"]["notes"])


def test_recurring_series_dates_are_not_compared(monkeypatch, geocoded):
    # One upstream occurrence against many of ours is not a contradiction.
    monkeypatch.setattr(cc, "source_claim",
                        lambda u: _claim(location=SUEGRAS, date="2026-09-01"))
    result = cc.cross_check_event(_event(recurring=True))
    assert result["date"]["verdict"] == cc.UNKNOWN


def test_one_off_date_contradiction_is_flagged(monkeypatch, geocoded):
    monkeypatch.setattr(cc, "source_claim",
                        lambda u: _claim(location=SUEGRAS, date="2026-09-01"))
    assert cc.cross_check_event(_event())["date"]["verdict"] == cc.DISAGREE
