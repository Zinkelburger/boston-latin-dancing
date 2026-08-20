"""Slug registry — a URL we have published must resolve forever.

Slugs are minted from the event name (`<name>-<id[:8]>`), so a merge, a
rename or a re-scrape retires the old URL while Google keeps serving it. The
registry's job is to make every retired URL land somewhere honest: the event's
new page when we can identify it, an "ended" page when we cannot. Never a 404,
and never a redirect to the wrong night.
"""

import json

import pytest

import event_store as es
import slug_registry as sr


def _event(**over):
    ev = {
        "id": "75AB4ED9-33C0-4242-BF08-8606C7B94665",
        "slug": "battle-of-the-beats-2026-boston-75ab4ed9",
        "name": "Battle of the Beats 2026: BOSTON",
        "location": "288 Green St, Cambridge, MA 02139-3312, United States",
        "startDate": "2026-08-15T20:30:00+00:00",
        "lat": 42.3646071,
        "lng": -71.1043523,
    }
    ev.update(over)
    return ev


def _entry(**over):
    e = {
        "id": "lister-battle-of-the-beats-2026-latin-vs-hip-hop",
        "name": "Battle of the Beats 2026: Latin vs. Hip Hop",
        "location": "Havana Club, 288 Green St, Cambridge, MA 02139, USA",
        "startDate": "2026-08-15T16:30:00-04:00",
        "lat": 42.3646071,
        "lng": -71.1043523,
        "status": sr.LIVE,
        "target": None,
    }
    e.update(over)
    return e


def _registry(**entries):
    return {"updated_at": None, "entries": dict(entries)}


@pytest.fixture(autouse=True)
def _no_known_dupes(tmp_path, monkeypatch):
    """Isolate from the repo's real duplicate map unless a test sets one."""
    monkeypatch.setattr(sr, "KNOWN_DUPES", tmp_path / "none.json")


# ── the case that started this ────────────────────────────────────────

def test_retitled_merge_redirects_to_the_survivor():
    # The merge kept beatrice's record, so the name flipped from "Latin vs.
    # Hip Hop" to "BOSTON" and lister's indexed URL stopped existing. Neither
    # id nor exact name connects them — only venue + date + title overlap.
    reg = _registry(**{"battle-of-the-beats-2026-latin-vs-hip-hop-lister-b": _entry()})
    sr.resolve(reg, [_event()])
    entry = reg["entries"]["battle-of-the-beats-2026-latin-vs-hip-hop-lister-b"]
    assert entry["status"] == sr.ALIAS
    assert entry["target"] == "battle-of-the-beats-2026-boston-75ab4ed9"
    assert entry["reason"] == "retitled"


def test_venue_matches_on_coordinates_not_address_text():
    # "Havana Club, 288 Green St, …" vs "288 Green St, …" is the same door.
    # Comparing the leading comma-segment says otherwise, which is what let
    # the Battle of the Beats URL fall through to an "ended" page.
    assert sr._same_place(_entry(), _event())


def test_live_slug_stays_live():
    reg = _registry(**{"battle-of-the-beats-2026-boston-75ab4ed9": _entry()})
    counts = sr.resolve(reg, [_event()])
    assert counts[sr.LIVE] == 1
    assert reg["entries"]["battle-of-the-beats-2026-boston-75ab4ed9"]["target"] is None


# ── resolution paths ──────────────────────────────────────────────────

def test_rename_keeping_the_id_redirects():
    reg = _registry(**{"old-name-75ab4ed9": _entry(name="Old Name", id=_event()["id"])})
    sr.resolve(reg, [_event()])
    assert reg["entries"]["old-name-75ab4ed9"]["reason"] == "renamed"


def test_known_duplicate_merge_redirects(tmp_path, monkeypatch):
    dupes = tmp_path / "known_duplicates.json"
    dupes.write_text(json.dumps([
        {"id_a": "dead-id", "id_b": _event()["id"], "verdict": "same"},
    ]))
    monkeypatch.setattr(sr, "KNOWN_DUPES", dupes)

    reg = _registry(**{"whatever-deadid12": _entry(id="dead-id", name="Something Else",
                                                  lat=None, lng=None, location="Elsewhere")})
    sr.resolve(reg, [_event()])
    assert reg["entries"]["whatever-deadid12"]["reason"] == "merged"


def test_different_verdict_does_not_merge(tmp_path, monkeypatch):
    dupes = tmp_path / "known_duplicates.json"
    dupes.write_text(json.dumps([
        {"id_a": "dead-id", "id_b": _event()["id"], "verdict": "different"},
    ]))
    monkeypatch.setattr(sr, "KNOWN_DUPES", dupes)

    reg = _registry(**{"whatever-deadid12": _entry(id="dead-id", name="Something Else",
                                                  lat=None, lng=None, location="Elsewhere")})
    sr.resolve(reg, [_event()])
    assert reg["entries"]["whatever-deadid12"]["status"] == sr.ENDED


def test_genuinely_gone_event_ends_rather_than_redirecting():
    reg = _registry(**{"picante-weekender-4f3c0539": _entry(
        id="4F3C0539", name="Picante Weekender", location="Someplace Else",
        lat=42.0, lng=-71.9, startDate="2026-06-01T20:00:00+00:00")})
    sr.resolve(reg, [_event()])
    assert reg["entries"]["picante-weekender-4f3c0539"]["status"] == sr.ENDED


# ── guards against a wrong redirect ───────────────────────────────────

def test_same_venue_different_night_does_not_redirect():
    # A week later at the same club is a different event, however similar
    # the title. Sending someone to the wrong night is worse than "ended".
    reg = _registry(**{"botb-lister-b": _entry(startDate="2026-08-22T16:30:00-04:00")})
    sr.resolve(reg, [_event()])
    assert reg["entries"]["botb-lister-b"]["status"] == sr.ENDED


def test_unrelated_title_at_same_venue_and_time_does_not_redirect():
    reg = _registry(**{"salsa-social-abcd1234": _entry(
        id="abcd1234", name="Merengue Marathon Fundraiser")})
    sr.resolve(reg, [_event()])
    assert reg["entries"]["salsa-social-abcd1234"]["status"] == sr.ENDED


def test_ambiguous_tie_refuses_to_guess():
    # Two live events at the same venue and time score identically, so there
    # is no defensible target. Stay silent rather than pick one.
    twin_a = _event(slug="twin-a-11111111", id="11111111")
    twin_b = _event(slug="twin-b-22222222", id="22222222")
    reg = _registry(**{"botb-lister-b": _entry()})
    sr.resolve(reg, [twin_a, twin_b])
    assert reg["entries"]["botb-lister-b"]["status"] == sr.ENDED


def test_missing_date_never_matches():
    # The date gate only tightens: without one we cannot claim two records
    # at a venue are the same event.
    reg = _registry(**{"botb-lister-b": _entry(startDate=None)})
    sr.resolve(reg, [_event()])
    assert reg["entries"]["botb-lister-b"]["status"] == sr.ENDED


def test_alias_never_points_at_another_alias():
    # A chain must collapse to a live page or become "ended" — a redirect to
    # a redirect is a broken link with extra steps.
    reg = _registry(
        **{
            "first-aaaa1111": _entry(id="aaaa1111", name="First", lat=None, lng=None),
            "second-bbbb2222": _entry(id="bbbb2222", name="Second", lat=None, lng=None),
        }
    )
    reg["entries"]["first-aaaa1111"].update(status=sr.ALIAS, target="second-bbbb2222")
    reg["entries"]["second-bbbb2222"].update(status=sr.ALIAS, target="nowhere-cccc3333")
    sr.resolve(reg, [_event()])
    for entry in reg["entries"].values():
        assert entry["status"] != sr.ALIAS or entry["target"] == _event()["slug"]


# ── recording is append-only ──────────────────────────────────────────

def test_record_preserves_first_seen():
    reg = _registry(**{_event()["slug"]: _entry(first_seen="git:abc123")})
    sr.record(reg, [_event()])
    assert reg["entries"][_event()["slug"]]["first_seen"] == "git:abc123"


def test_record_adds_new_slugs():
    reg = _registry()
    assert sr.record(reg, [_event()]) == 1
    assert _event()["slug"] in reg["entries"]


def test_recording_never_drops_a_retired_slug():
    # The registry only ever grows: a slug that stops being published keeps
    # its entry, which is the whole point.
    reg = _registry(**{"gone-deadbeef": _entry(id="deadbeef", name="Gone")})
    sr.record(reg, [_event()])
    assert "gone-deadbeef" in reg["entries"]


def test_events_without_slugs_are_skipped():
    reg = _registry()
    assert sr.record(reg, [{"id": "x", "name": "No Slug"}]) == 0


# ── title overlap ─────────────────────────────────────────────────────

def test_generic_dance_words_do_not_create_a_match():
    # Nearly every title here contains "salsa", "bachata", "social", "night".
    # If those counted, every event at a venue would match every other.
    assert sr._title_overlap("Salsa Social Night", "Bachata Dance Party Boston") == 0.0


class TestSlugCollisions:
    """`<name>-<id[:8]>` collides whenever a scraper mints ids from a shared
    prefix ("fiesta-2026...", "bobas-2026..."). The site's findBySlug() takes
    the first match, so before this every Fiesta night but one was unreachable
    and the shipped URL rendered the wrong venue."""

    def _events(self):
        return [
            {"id": "fiesta-20260828-sol-de-mexico", "name": "Salsa Social", "slug": "salsa-social-fiesta-2"},
            {"id": "fiesta-20260807-agave", "name": "Salsa Social", "slug": "salsa-social-fiesta-2"},
            {"id": "unique-event", "name": "Other Night", "slug": "other-night-unique-e"},
        ]

    def test_every_event_keeps_its_own_url(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sr, "REGISTRY_PATH", tmp_path / "missing.json")
        events = self._events()
        es._resolve_slug_collisions(events)
        slugs = [e["slug"] for e in events]
        assert len(set(slugs)) == len(slugs)

    def test_untouched_slug_is_left_alone(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sr, "REGISTRY_PATH", tmp_path / "missing.json")
        events = self._events()
        es._resolve_slug_collisions(events)
        assert events[2]["slug"] == "other-night-unique-e"

    def test_registered_id_keeps_the_public_url(self, monkeypatch, tmp_path):
        registry = tmp_path / "slug-registry.json"
        registry.write_text(json.dumps({"entries": {
            "salsa-social-fiesta-2": {"id": "fiesta-20260807-agave"},
        }}))
        monkeypatch.setattr(sr, "REGISTRY_PATH", registry)
        events = self._events()
        es._resolve_slug_collisions(events)
        keeper = next(e for e in events if e["id"] == "fiesta-20260807-agave")
        assert keeper["slug"] == "salsa-social-fiesta-2"

    def test_new_slugs_are_stable_across_runs(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sr, "REGISTRY_PATH", tmp_path / "missing.json")
        first, second = self._events(), self._events()
        es._resolve_slug_collisions(first)
        es._resolve_slug_collisions(second)
        assert [e["slug"] for e in first] == [e["slug"] for e in second]
