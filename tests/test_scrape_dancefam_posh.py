"""DanceFam and Posh scrapers against trimmed copies of their real JSON.

Fixtures are the 2026-10-06 responses: DanceFam's Massachusetts search plus
its per-event detail calls, and one page of Posh's searchForEvents.
"""

import json
from pathlib import Path

import pytest

import scraper_utils

FIXTURES = Path(__file__).parent / "fixtures"


class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(scraper_utils, "geocode", lambda loc: (42.35, -71.05))


def test_dancefam_builds_eastern_times_full_addresses_and_links(monkeypatch):
    import scrape_dancefam as sd
    api = json.loads((FIXTURES / "dancefam_api.json").read_text())

    def fake_fetch(url, params=None, **kw):
        if url.endswith("/search/events"):
            return _Resp(api["search"])
        return _Resp(api["details"][str(params["EventId"])])

    monkeypatch.setattr(sd, "fetch", fake_fetch)
    monkeypatch.setattr(sd.time, "sleep", lambda s: None)
    result = sd.fetch_source({"id": "dancefam"})
    by_id = {e["id"]: e for e in result.events}
    blast = by_id["dancefam-2650"]
    # 02:00Z on Oct 11 is Saturday night, Oct 10, in Boston.
    assert blast["startDate"] == "2026-10-10T22:00:00-04:00"
    assert blast["dayOfWeek"] == "Saturday"
    assert blast["location"] == "Cocomango Speakeasy, 50 Cambridgepark Drive, Cambridge, MA 02140"
    assert (blast["lat"], blast["lng"]) == (42.394493, -71.14364)
    assert blast["url"] == "https://dancefam.org/event/2650"
    assert blast["cost"] == "$25"
    assert "bachata" in blast["styles"]
    assert "BachaTipico Hangout" in blast["description"]
    worcester = next(e for e in result.events if "Worcester" in e["location"])
    assert worcester["location"].startswith("105 Water Street"), "street-named venue is not repeated"


def test_dancefam_empty_search_is_a_healthy_skip(monkeypatch):
    import scrape_dancefam as sd
    monkeypatch.setattr(sd, "fetch", lambda url, params=None, **kw: _Resp([]))
    result = sd.fetch_source({"id": "dancefam"})
    assert result.events == [] and result.skipped


def test_dancefam_non_list_answer_raises(monkeypatch):
    import scrape_dancefam as sd
    monkeypatch.setattr(sd, "fetch", lambda url, params=None, **kw: _Resp({"error": "moved"}))
    with pytest.raises(RuntimeError, match="API changed"):
        sd.fetch_source({"id": "dancefam"})


def test_posh_keeps_massachusetts_latin_events_and_drops_ads_and_other_states(monkeypatch):
    import scrape_posh as sp
    body = json.loads((FIXTURES / "posh_search.json").read_text())
    monkeypatch.setattr(sp, "fetch", lambda url, params=None, **kw: _Resp(body))
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    result = sp.fetch_source({"id": "posh", "queries": ["bachata boston"]})
    (ev,) = result.events
    assert ev["name"].startswith("Bachata Brunch Club")
    assert ev["url"].startswith("https://posh.vip/e/bachata-brunch-club")
    assert ev["startDate"] == "2026-10-11T14:00:00-04:00"
    assert ev["location"].startswith("Grace By Nia, 60 Seaport Blvd")
    assert not ev["location"].endswith("USA")
    assert "bachata" in ev["styles"]


def test_posh_answer_without_events_raises(monkeypatch):
    import scrape_posh as sp
    monkeypatch.setattr(sp, "fetch", lambda url, params=None, **kw: _Resp({"error": {"code": -32004}}))
    with pytest.raises(RuntimeError, match="API changed"):
        sp.fetch_source({"id": "posh", "queries": ["salsa boston"]})
