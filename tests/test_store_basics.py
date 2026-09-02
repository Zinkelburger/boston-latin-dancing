"""Small public entry points of event_store that nothing else exercised:
slugify, validate_event and dismiss_rejected."""

import event_store as es
import scraper_utils as su


# ── slugify ───────────────────────────────────────────────────────────

def test_slugify_is_ascii_lowercase_with_id_suffix():
    assert es.slugify("Noche de Salsa & Bachata!", "ABCDEFGH-1234") == "noche-de-salsa-bachata-abcdefgh"


def test_slugify_strips_accents_instead_of_dropping_words():
    assert es.slugify("Fiesta Caribeña", "12345678abc").startswith("fiesta-caribena-")


def test_slugify_caps_the_name_part_at_60_chars():
    slug = es.slugify("x" * 200, "12345678")
    name_part, _, suffix = slug.rpartition("-")
    assert len(name_part) == 60
    assert suffix == "12345678"


# ── validate_event ────────────────────────────────────────────────────

def _valid():
    return {"name": "Salsa Social", "startDate": "2099-07-01T20:00:00-04:00",
            "location": "Boston, MA", "lat": 42.36, "lng": -71.06, "styles": ["salsa"]}


def test_validate_event_accepts_a_complete_event():
    assert es.validate_event(_valid()) == []


def test_validate_event_reports_every_missing_required_field():
    issues = es.validate_event({"lat": 1, "lng": 1, "styles": ["salsa"]})
    assert set(issues) == {"missing name", "missing startDate", "missing location"}


def test_validate_event_geocodes_when_coordinates_are_missing(monkeypatch):
    monkeypatch.setattr(es, "geocode", lambda location: (42.37, -71.10))
    ev = {**_valid(), "lat": None, "lng": None}
    assert es.validate_event(ev) == []
    assert (ev["lat"], ev["lng"]) == (42.37, -71.10)


def test_validate_event_flags_an_ungeocodable_location(monkeypatch):
    monkeypatch.setattr(es, "geocode", lambda location: None)
    ev = {**_valid(), "lat": None, "lng": None}
    assert es.validate_event(ev) == ["could not geocode location"]


def test_validate_event_skips_geocoding_for_unknown_venues(monkeypatch):
    def boom(location):
        raise AssertionError("must not geocode a venueUnknown event")
    monkeypatch.setattr(es, "geocode", boom)
    ev = {**_valid(), "lat": None, "lng": None, "venueUnknown": True}
    assert es.validate_event(ev) == []


def test_validate_event_autodetects_styles_from_text():
    ev = {**_valid(), "styles": ["other"], "name": "Bachata Night"}
    assert es.validate_event(ev) == []
    assert ev["styles"] == ["bachata"]


def test_validate_event_flags_undetectable_styles():
    ev = {**_valid(), "styles": [], "name": "Thursday Party"}
    assert es.validate_event(ev) == ["styles=other (could not auto-detect)"]


# ── dismiss_rejected ──────────────────────────────────────────────────

def _rejected(**over):
    base = {"id": "rej-1", "name": "Salsa Social", "startDate": "2099-07-01T20:00:00-04:00",
            "location": "Boston, MA", "lat": 42.36, "lng": -71.06, "styles": ["salsa"],
            "source": "test-source", "_rejected_reason": "no_latin_keywords",
            "_rejected_at": "2026-01-01T00:00:00+00:00"}
    base.update(over)
    return base


def test_dismiss_unknown_id_is_not_found(store):
    assert store.dismiss_rejected("nope")["status"] == "not_found"


def test_dismiss_removes_from_rejected_and_logs(store):
    store.save_rejected([_rejected(), _rejected(id="rej-2")])
    result = store.dismiss_rejected("rej-1", reason="one-off")
    assert result["status"] == "dismissed"
    assert result["event"]["id"] == "rej-1"
    assert not any(k.startswith("_rejected") for k in result["event"])
    assert [e["id"] for e in store.load_rejected()] == ["rej-2"]
    assert store.load_blocked() == []
    assert "dismiss_rejected" in store.CHANGELOG.read_text(encoding="utf-8")


def test_dismiss_with_block_moves_to_blocklist(store):
    store.save_rejected([_rejected()])
    result = store.dismiss_rejected("rej-1", reason="defunct venue", block=True, block_category="defunct")
    assert result["status"] == "blocked"
    assert store.load_rejected() == []
    blocked = store.load_blocked()
    assert [b["id"] for b in blocked] == ["rej-1"]


def test_dismiss_with_invalid_block_category_changes_nothing(store):
    store.save_rejected([_rejected()])
    result = store.dismiss_rejected("rej-1", block=True, block_category="because")
    assert result["status"] == "error"
    assert [e["id"] for e in store.load_rejected()] == ["rej-1"]
    assert store.load_blocked() == []
