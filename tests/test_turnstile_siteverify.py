"""Canonical Turnstile Spin siteverify: success + action + hostname."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import server as api  # noqa: E402


def _ok_result(**overrides):
    result = {
        "success": True,
        "action": api.TURNSTILE_ACTION,
        "hostname": "bostonsalsa.org",
    }
    result.update(overrides)
    return result


def _mock_post(monkeypatch, payload=None, *, ok=True, status_code=200, side_effect=None):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.json.return_value = payload if payload is not None else _ok_result()
    mock = MagicMock(return_value=resp)
    if side_effect is not None:
        mock.side_effect = side_effect
    monkeypatch.setattr(api.http_requests, "post", mock)
    return mock


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "test-secret")
    monkeypatch.setenv("TURNSTILE_HOSTNAMES", "bostonsalsa.org")


def test_fails_closed_without_secret(monkeypatch):
    monkeypatch.delenv("TURNSTILE_SECRET", raising=False)
    monkeypatch.setenv("TURNSTILE_HOSTNAMES", "bostonsalsa.org")
    assert api.verify_turnstile("token-value") is False


def test_fails_closed_without_hostnames(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "test-secret")
    monkeypatch.delenv("TURNSTILE_HOSTNAMES", raising=False)
    assert api.verify_turnstile("token-value") is False


def test_rejects_empty_token(configured, monkeypatch):
    mock = _mock_post(monkeypatch)
    assert api.verify_turnstile("") is False
    mock.assert_not_called()


def test_rejects_oversized_token(configured, monkeypatch):
    mock = _mock_post(monkeypatch)
    assert api.verify_turnstile("x" * (api.TURNSTILE_MAX_TOKEN_LEN + 1)) is False
    mock.assert_not_called()


def test_rejects_siteverify_http_error(configured, monkeypatch):
    _mock_post(monkeypatch, ok=False, status_code=500)
    assert api.verify_turnstile("ok-token") is False


def test_rejects_success_false(configured, monkeypatch):
    _mock_post(monkeypatch, {"success": False, "error-codes": ["invalid-input-response"]})
    assert api.verify_turnstile("ok-token") is False


def test_rejects_wrong_action(configured, monkeypatch):
    _mock_post(monkeypatch, _ok_result(action="signup"))
    assert api.verify_turnstile("ok-token") is False


def test_rejects_wrong_hostname(configured, monkeypatch):
    _mock_post(monkeypatch, _ok_result(hostname="evil.example"))
    assert api.verify_turnstile("ok-token") is False


def test_rejects_localhost_even_if_returned(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "test-secret")
    monkeypatch.setenv("TURNSTILE_HOSTNAMES", "bostonsalsa.org")
    _mock_post(monkeypatch, _ok_result(hostname="localhost"))
    assert api.verify_turnstile("ok-token") is False


def test_accepts_matching_action_and_hostname(configured, monkeypatch):
    mock = _mock_post(monkeypatch, _ok_result())
    assert api.verify_turnstile("ok-token", ip="1.2.3.4") is True
    mock.assert_called_once()
    kwargs = mock.call_args
    assert kwargs.args[0] == api.SITEVERIFY_URL
    assert kwargs.kwargs["data"]["response"] == "ok-token"
    assert kwargs.kwargs["data"]["remoteip"] == "1.2.3.4"
    assert kwargs.kwargs["data"]["secret"] == "test-secret"


def test_network_error_fails_closed(configured, monkeypatch):
    _mock_post(monkeypatch, side_effect=RuntimeError("timeout"))
    assert api.verify_turnstile("ok-token") is False
