"""Link-check classification policy.

Every case here is a behaviour measured against the live host, not a guess.
The checker runs unattended, so the expensive failure is a false alarm that
pulls a real event off the map — not a missed dead link. Where a host gives
us no signal we must say UNVERIFIABLE, never OK and never BROKEN.
"""

import json

import check_links as cl

FB_EVENT = "https://www.facebook.com/events/1299596699048505/"
FB_SHARE = "https://facebook.com/events/s/battle-of-the-beats-2026-bosto/1613687936940528/"
FB_PAGE = "https://www.facebook.com/Tambosalsa/events"
IG = "https://www.instagram.com/timbadescontrol/"
EB = "https://www.eventbrite.com/e/saborcito-tickets-1989160742327"
ORG = "https://www.listerevents.com/event-details/battle-of-the-beats-2026"

LIVE_FB = "<html><head><title>Battle of the Beats 2026: BOSTON</title></head></html>"
DEAD_FB = "<html><head></head><body></body></html>"
CHROME_FB = "<html><head><title>Facebook</title></head></html>"


# ── ordinary hosts: trust the status ──────────────────────────────────

def test_200_is_ok():
    assert cl.classify(ORG, 200, "")[0] == cl.OK


def test_404_is_broken():
    assert cl.classify(ORG, 404, "")[0] == cl.BROKEN


def test_410_is_broken():
    assert cl.classify(ORG, 410, "")[0] == cl.BROKEN


def test_eventbrite_404_is_broken():
    # Eventbrite answers honestly, so its 404 is real evidence.
    assert cl.classify(EB, 404, "")[0] == cl.BROKEN


def test_500_is_not_broken():
    # The organizer's server being down today says nothing about the link.
    assert cl.classify(ORG, 500, "")[0] == cl.UNVERIFIABLE


def test_403_is_not_broken():
    # Bot walls are not dead links.
    assert cl.classify(ORG, 403, "")[0] == cl.UNVERIFIABLE


def test_429_is_not_broken():
    assert cl.classify(ORG, 429, "")[0] == cl.UNVERIFIABLE


# ── facebook: status alone is never the answer ────────────────────────

def test_facebook_400_carries_no_signal():
    # Measured: FB returns 400 to browser UAs from datacenter IPs whether or
    # not the event exists. Treating that as broken would have condemned
    # every Facebook link we ship.
    assert cl.classify(FB_EVENT, 400, "")[0] == cl.UNVERIFIABLE


def test_facebook_404_is_broken():
    # With the og-scraper UA a deleted event does return an honest 404.
    assert cl.classify(FB_EVENT, 404, "")[0] == cl.BROKEN


def test_facebook_live_event_with_title_is_ok():
    assert cl.classify(FB_EVENT, 200, LIVE_FB)[0] == cl.OK


def test_facebook_title_is_reported_in_the_note():
    verdict, note = cl.classify(FB_EVENT, 200, LIVE_FB)
    assert verdict == cl.OK and "Battle of the Beats" in note


def test_dead_share_wrapper_is_broken_despite_200():
    # The trap: a share wrapper for a deleted event still returns 200. Only
    # the missing <title> distinguishes it from a live one.
    assert cl.classify(FB_SHARE, 200, DEAD_FB)[0] == cl.BROKEN


def test_live_share_wrapper_is_ok():
    assert cl.classify(FB_SHARE, 200, LIVE_FB)[0] == cl.OK


def test_bare_chrome_title_counts_as_empty():
    assert cl.classify(FB_SHARE, 200, CHROME_FB)[0] == cl.BROKEN


def test_titleless_facebook_page_is_only_unverifiable():
    # A non-wrapper page behind a login wall might be perfectly alive, so it
    # never gets condemned — only wrappers, which have no reason to exist
    # without a resolvable event behind them.
    assert cl.classify(FB_PAGE, 200, DEAD_FB)[0] == cl.UNVERIFIABLE


def test_facebook_500_is_not_broken():
    assert cl.classify(FB_EVENT, 500, "")[0] == cl.UNVERIFIABLE


# ── instagram: the status lies, the og:title does not ─────────────────

LIVE_IG = ('<meta property="og:title" content="Noise | Latin luxury elevated '
           '(&#064;noise.boston) &#x2022; Instagram photos and videos"/>')
DEAD_IG = "<html><head><title>Instagram</title></head></html>"


def test_instagram_profile_with_og_title_is_ok():
    assert cl.classify(IG, 200, LIVE_IG)[0] == cl.OK


def test_instagram_reports_the_account_name():
    # The bullet-separated boilerplate is trimmed so the note reads as a name.
    verdict, note = cl.classify(IG, 200, LIVE_IG)
    assert verdict == cl.OK and "Noise" in note and "Instagram photos" not in note


def test_dead_instagram_handle_is_broken_despite_200():
    # Measured: a nonexistent handle serves the same login wall with a 200 and
    # no profile metadata at all. That absence is the only signal there is.
    assert cl.classify(IG, 200, DEAD_IG)[0] == cl.BROKEN


def test_instagram_rate_limit_is_not_broken():
    for status in (401, 403, 429):
        assert cl.classify(IG, status, "")[0] == cl.UNVERIFIABLE


def test_instagram_500_is_not_broken():
    assert cl.classify(IG, 500, "")[0] == cl.UNVERIFIABLE


# ── a blocked host must not read as a mass extinction ─────────────────

def _result(url, verdict=cl.BROKEN):
    return {"url": url, "verdict": verdict, "note": "no profile metadata"}


def test_wholesale_instagram_failure_is_treated_as_a_block():
    # Sixteen accounts do not vanish overnight. If every single one comes back
    # empty, Instagram stopped talking to us — acting on that would pull real
    # events off the map.
    results = [_result(f"https://www.instagram.com/acct{i}/") for i in range(5)]
    cl._guard_against_a_blocked_host(results)
    assert all(r["verdict"] == cl.UNVERIFIABLE for r in results)
    assert "blocked" in results[0]["note"]


def test_one_dead_handle_among_live_ones_stays_broken():
    results = [_result("https://www.instagram.com/dead/")] + [
        _result(f"https://www.instagram.com/live{i}/", cl.OK) for i in range(4)
    ]
    cl._guard_against_a_blocked_host(results)
    assert results[0]["verdict"] == cl.BROKEN


def test_the_guard_needs_a_real_sample_before_it_fires():
    # With one or two links there is no way to tell a block from a dead
    # account, and silently excusing them would hide a genuine break.
    results = [_result("https://www.instagram.com/only/")]
    cl._guard_against_a_blocked_host(results)
    assert results[0]["verdict"] == cl.BROKEN


def test_the_guard_does_not_cross_hosts():
    # Facebook being blocked says nothing about Instagram.
    results = [_result(f"https://www.facebook.com/events/{i}/") for i in range(4)]
    results.append(_result("https://www.instagram.com/dead/"))
    cl._guard_against_a_blocked_host(results)
    assert results[-1]["verdict"] == cl.BROKEN


# ── transport failures ────────────────────────────────────────────────

def test_dns_failure_is_broken():
    # The host itself is gone — that is a real dead link.
    assert cl.classify(ORG, None, "", error="ConnectionError")[0] == cl.BROKEN


def test_timeout_is_not_broken():
    # A slow server is not a dead link, and this runs on a cron.
    assert cl.classify(ORG, None, "", error="ReadTimeout")[0] == cl.UNVERIFIABLE


def test_chunked_encoding_error_is_not_broken():
    # Observed once against uncommoncorner.org, which served 200 on retry.
    assert cl.classify(ORG, None, "", error="ChunkedEncodingError")[0] == cl.UNVERIFIABLE


def test_empty_url_is_unverifiable():
    assert cl.classify("", None, "")[0] == cl.UNVERIFIABLE


# ── target collection ─────────────────────────────────────────────────

def test_collect_targets_records_every_appearance(tmp_path, monkeypatch):
    published = tmp_path / "events-published.json"
    published.write_text(json.dumps([
        {"id": "a", "name": "Live One", "url": "https://example.com/a",
         "urls": ["https://alt.example.com/a"]},
        {"id": "b", "name": "Old One", "url": "https://example.com/b", "archived": True},
    ]))
    monkeypatch.setattr(cl, "PUBLISHED", published)
    monkeypatch.setattr(cl, "VENUES", tmp_path / "missing-venues.json")
    monkeypatch.setattr(cl, "SOURCES", tmp_path / "missing-sources.json")

    targets = cl.collect_targets()
    assert "https://alt.example.com/a" in targets
    assert targets["https://example.com/a"] == ["event: Live One"]

    live_only = cl.collect_targets(only_live=True)
    assert "https://example.com/b" not in live_only


# ── the human's queue ─────────────────────────────────────────────────

def test_manual_check_queue_surfaces_flagged_events(tmp_path, monkeypatch):
    active = tmp_path / "active.json"
    active.write_text(json.dumps([
        {"id": "noise", "name": "NOISE LIVE", "url": "https://www.facebook.com/share/1EjEyfCyhA",
         "startDate": "2026-08-30T01:00:00+00:00",
         "_needs_manual_check": {"reason": "share wrapper points at a photo",
                                 "flagged_at": "2026-08-13T00:00:00+00:00"}},
        {"id": "fine", "name": "Ordinary Social", "url": "https://example.com"},
    ]))
    monkeypatch.setattr(cl, "ACTIVE", active)

    queue = cl.manual_check_queue()
    assert [q["id"] for q in queue] == ["noise"]
    assert queue[0]["reason"] == "share wrapper points at a photo"


def test_manual_check_queue_accepts_a_bare_string_reason(tmp_path, monkeypatch):
    active = tmp_path / "active.json"
    active.write_text(json.dumps([
        {"id": "x", "name": "X", "_needs_manual_check": "just look at it"},
    ]))
    monkeypatch.setattr(cl, "ACTIVE", active)
    assert cl.manual_check_queue()[0]["reason"] == "just look at it"


def test_manual_check_queue_is_empty_without_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "ACTIVE", tmp_path / "nope.json")
    assert cl.manual_check_queue() == []


def test_collect_targets_skips_disabled_sources(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps([
        {"id": "on", "url": "https://example.com/on", "enabled": True},
        {"id": "off", "url": "https://example.com/off", "enabled": False},
    ]))
    monkeypatch.setattr(cl, "PUBLISHED", tmp_path / "missing.json")
    monkeypatch.setattr(cl, "VENUES", tmp_path / "missing-venues.json")
    monkeypatch.setattr(cl, "SOURCES", sources)

    targets = cl.collect_targets()
    assert "https://example.com/on" in targets
    assert "https://example.com/off" not in targets
