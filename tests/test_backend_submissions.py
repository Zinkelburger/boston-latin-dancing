"""Submission API: per-client rate limiting, input caps, and a crash-safe,
lock-protected submissions store.

The Turnstile call is stubbed to succeed so these exercise only the storage
and validation layers (siteverify itself is covered in
test_turnstile_siteverify.py).
"""

import json
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import server as api  # noqa: E402

ADMIN = "test-admin-token-1234"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "SUBMISSIONS_PATH", tmp_path / "submissions.json")
    monkeypatch.setattr(api, "ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr(api, "verify_turnstile", lambda token, ip=None: True)
    # slowapi's in-memory counters outlive the app between tests.
    api.limiter.reset()
    return TestClient(api.app)


def _body(**overrides):
    body = {
        "email": "org@example.com",
        "event_name": "Salsa Social",
        "event_url": "https://example.com/social",
        "styles": ["salsa"],
        "cf_turnstile_token": "tok",
    }
    body.update(overrides)
    return body


def _post(client, ip="203.0.113.5", **overrides):
    return client.post(
        "/api/submit-event",
        json=_body(**overrides),
        headers={"CF-Connecting-IP": ip},
    )


def _stored(tmp_path):
    return json.loads((tmp_path / "submissions.json").read_text())


# ── Client identity / rate limiting ───────────────────────────────────


def test_rate_limit_is_per_client_not_global(client):
    for _ in range(5):
        assert _post(client, ip="198.51.100.1").status_code == 200
    # Sixth from the same client is throttled...
    assert _post(client, ip="198.51.100.1").status_code == 429
    # ...but a different client behind the same tunnel (same socket peer,
    # 127.0.0.1) is untouched.
    assert _post(client, ip="198.51.100.2").status_code == 200


def test_entry_records_real_client_ip(client, tmp_path):
    assert _post(client, ip="198.51.100.9").status_code == 200
    assert _stored(tmp_path)[0]["ip"] == "198.51.100.9"


def test_client_ip_prefers_cf_header_then_xff_then_peer(monkeypatch):
    from starlette.requests import Request

    def req(headers, peer="127.0.0.1"):
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request({"type": "http", "headers": raw, "client": (peer, 1234),
                        "method": "POST", "path": "/", "query_string": b""})

    monkeypatch.setattr(api, "TRUST_PROXY_HEADERS", True)
    assert api._client_ip(req({"CF-Connecting-IP": "1.1.1.1", "X-Forwarded-For": "2.2.2.2"})) == "1.1.1.1"
    assert api._client_ip(req({"X-Forwarded-For": "2.2.2.2, 10.0.0.1"})) == "2.2.2.2"
    assert api._client_ip(req({})) == "127.0.0.1"

    monkeypatch.setattr(api, "TRUST_PROXY_HEADERS", False)
    assert api._client_ip(req({"CF-Connecting-IP": "1.1.1.1"}, peer="9.9.9.9")) == "9.9.9.9"


# ── Input caps ────────────────────────────────────────────────────────


@pytest.mark.parametrize("field,length", [
    ("event_name", 201),
    ("event_url", 501),
    ("location", 301),
    ("notes", 2001),
    ("email", 201),
    ("instagram", 201),
])
def test_string_fields_are_length_capped(client, field, length):
    resp = _post(client, **{field: "x" * length})
    assert resp.status_code == 422
    assert any(field in d["loc"] for d in resp.json()["detail"])


def test_style_list_is_capped(client):
    resp = _post(client, styles=["salsa"] * 11)
    assert resp.status_code == 422


def test_unknown_style_rejected(client):
    resp = _post(client, styles=["salsa", "tango"])
    assert resp.status_code == 422
    assert "tango" in json.dumps(resp.json())


def test_every_frontend_style_accepted(client):
    resp = _post(client, styles=sorted(api.ALLOWED_STYLES))
    assert resp.status_code == 200


def test_unknown_recurrence_values_rejected(client):
    assert _post(client, recurrence_type="yearly").status_code == 422
    assert _post(client, day_of_week="Funday").status_code == 422
    assert _post(client, week_of_month="5th").status_code == 422
    assert _post(client, is_recurring=True, recurrence_type="monthly",
                 day_of_week="Saturday", week_of_month="2nd").status_code == 200


def test_oversized_body_rejected_before_parsing(client):
    huge = "x" * (api.MAX_BODY_BYTES + 1)
    resp = client.post(
        "/api/submit-event",
        content=json.dumps(_body(notes=huge)),
        headers={"Content-Type": "application/json", "CF-Connecting-IP": "203.0.113.7"},
    )
    assert resp.status_code == 413


def test_turnstile_token_length_still_checked(client):
    resp = _post(client, cf_turnstile_token="t" * (api.TURNSTILE_MAX_TOKEN_LEN + 1))
    assert resp.status_code == 422


# ── Storage ───────────────────────────────────────────────────────────


def test_concurrent_posts_all_land(client, tmp_path):
    n = 12
    results = []

    def post(i):
        results.append(_post(client, ip=f"198.51.100.{i}", event_name=f"Event {i}").status_code)

    threads = [threading.Thread(target=post, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [200] * n
    names = {e["event_name"] for e in _stored(tmp_path)}
    assert names == {f"Event {i}" for i in range(n)}


def test_corrupt_store_is_moved_aside_not_overwritten(client, tmp_path):
    store = tmp_path / "submissions.json"
    store.write_text('[{"event_name": "half-written", "event_url": "http')
    assert _post(client, event_name="After crash").status_code == 200

    aside = list(tmp_path.glob("submissions.corrupt-*.json"))
    assert len(aside) == 1
    assert "half-written" in aside[0].read_text()
    assert [e["event_name"] for e in _stored(tmp_path)] == ["After crash"]


def test_empty_store_file_is_treated_as_corrupt(client, tmp_path):
    (tmp_path / "submissions.json").write_text("")
    assert _post(client).status_code == 200
    assert len(list(tmp_path.glob("submissions.corrupt-*.json"))) == 1


def test_write_is_atomic_no_temp_left_behind(client, tmp_path):
    assert _post(client).status_code == 200
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    assert (tmp_path / "submissions.json.lock").exists()


def test_clear_archives_rather_than_discards(client, tmp_path):
    _post(client, event_name="One")
    _post(client, event_name="Two")
    resp = client.post("/api/submissions/clear", headers={"Authorization": f"Bearer {ADMIN}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["archived"] == 2
    assert body["archive_file"].startswith("submissions-archive-")

    archives = list(tmp_path.glob("submissions-archive-*.json"))
    assert len(archives) == 1
    assert [e["event_name"] for e in json.loads(archives[0].read_text())] == ["One", "Two"]
    assert _stored(tmp_path) == []


def test_clear_on_empty_store_writes_no_archive(client, tmp_path):
    resp = client.post("/api/submissions/clear", headers={"Authorization": f"Bearer {ADMIN}"})
    assert resp.json() == {"status": "ok", "archived": 0, "archive_file": None}
    assert list(tmp_path.glob("submissions-archive-*.json")) == []


def test_post_during_clear_is_never_lost(client, tmp_path, monkeypatch):
    """A post that arrives while clear holds the lock waits, then lands in
    the fresh list rather than in the archive-then-overwritten window."""
    _post(client, event_name="Before")
    clear_entered = threading.Event()
    release_clear = threading.Event()
    real_archive_path = api._archive_path

    def slow_archive_path():
        clear_entered.set()
        release_clear.wait(timeout=5)
        return real_archive_path()

    monkeypatch.setattr(api, "_archive_path", slow_archive_path)

    clear_result = {}

    def do_clear():
        r = client.post("/api/submissions/clear", headers={"Authorization": f"Bearer {ADMIN}"})
        clear_result["archived"] = r.json()["archived"]

    post_status = {}

    def do_post():
        post_status["code"] = _post(client, event_name="During").status_code

    t_clear = threading.Thread(target=do_clear)
    t_clear.start()
    assert clear_entered.wait(timeout=5)
    t_post = threading.Thread(target=do_post)
    t_post.start()
    release_clear.set()
    t_clear.join(timeout=10)
    t_post.join(timeout=10)

    assert clear_result["archived"] == 1
    assert post_status["code"] == 200
    assert [e["event_name"] for e in _stored(tmp_path)] == ["During"]


# ── Admin auth ────────────────────────────────────────────────────────


def test_admin_token_compare_is_constant_time(client, monkeypatch):
    calls = []
    real = api.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(api.hmac, "compare_digest", spy)
    assert client.get("/api/submissions", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.get("/api/submissions", headers={"Authorization": f"Bearer {ADMIN}"}).status_code == 200
    assert len(calls) == 2


def test_admin_rejected_when_token_unset(client, monkeypatch):
    monkeypatch.setattr(api, "ADMIN_TOKEN", "")
    assert client.get("/api/submissions", headers={"Authorization": "Bearer "}).status_code in (401, 403)
    assert client.get("/api/submissions", headers={"Authorization": "Bearer x"}).status_code == 403
