"""
Boston Latin Dance – Event Submission API

Minimal FastAPI server that accepts event submissions from organizers
and appends them to a local JSON file for manual review.

Designed to run behind a Cloudflare Tunnel on a VPS (no public ports).
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests as http_requests
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, model_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

log = logging.getLogger("uvicorn.error")

SUBMISSIONS_PATH = Path(
    os.getenv("BLD_SUBMISSIONS_PATH", Path(__file__).parent / "submissions.json")
)
FRONTEND_ORIGIN = os.getenv(
    "BLD_FRONTEND_ORIGIN", "https://bostonsalsa.org"
)
ADMIN_TOKEN = os.getenv("BLD_ADMIN_TOKEN", "")
TURNSTILE_ACTION = "turnstile-spin-v2"
TURNSTILE_MAX_TOKEN_LEN = 2048
SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

_cors_origins = [FRONTEND_ORIGIN]
if os.getenv("BLD_DEBUG"):
    _cors_origins.append("http://localhost:3000")

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="BLD Event Submissions", docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

_bearer = HTTPBearer()


def _require_admin(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    if not ADMIN_TOKEN or creds.credentials != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token")
    return creds


class EventSubmission(BaseModel):
    email: str = ""
    instagram: str = ""
    event_name: str
    event_url: str
    styles: list[str] = []
    location: str = ""
    is_recurring: bool = False
    date: str = ""
    time: str = ""
    recurrence_type: str = ""
    day_of_week: str = ""
    week_of_month: str = ""
    start_date: str = ""
    notes: str = ""
    cf_turnstile_token: str = ""

    @model_validator(mode="after")
    def require_contact(self):
        if not self.email.strip() and not self.instagram.strip():
            raise ValueError("Provide at least an email or Instagram handle")
        return self


def _load_submissions() -> list[dict]:
    if not SUBMISSIONS_PATH.exists():
        return []
    try:
        return json.loads(SUBMISSIONS_PATH.read_text())
    except (json.JSONDecodeError, ValueError):
        return []


def _save_submissions(entries: list[dict]) -> None:
    SUBMISSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUBMISSIONS_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False))


# ── Turnstile ─────────────────────────────────────────────────────────


def _client_ip(request: Request) -> str | None:
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or (request.client.host if request.client else None)
    ) or None


def _turnstile_secret() -> str:
    return os.getenv("TURNSTILE_SECRET", "").strip()


def _turnstile_hostnames() -> set[str]:
    raw = os.getenv("TURNSTILE_HOSTNAMES", "")
    return {h.strip() for h in raw.split(",") if h.strip()}


def verify_turnstile(token: str, ip: str | None = None) -> bool:
    """Canonical Spin siteverify: success, expected action, and hostname."""
    secret = _turnstile_secret()
    hostnames = _turnstile_hostnames()
    if not secret or not hostnames:
        log.warning("Turnstile is not configured (TURNSTILE_SECRET or TURNSTILE_HOSTNAMES)")
        return False
    if not isinstance(token, str) or not token or len(token) > TURNSTILE_MAX_TOKEN_LEN:
        log.warning("Turnstile token missing or too long")
        return False
    try:
        payload: dict = {"secret": secret, "response": token}
        if ip:
            payload["remoteip"] = ip
        resp = http_requests.post(SITEVERIFY_URL, data=payload, timeout=10)
        if not resp.ok:
            log.warning("Turnstile siteverify HTTP %s", resp.status_code)
            return False
        result = resp.json()
        ok = (
            result.get("success") is True
            and result.get("action") == TURNSTILE_ACTION
            and result.get("hostname") in hostnames
        )
        if not ok:
            log.warning(
                "Turnstile validation failed: success=%s action=%s hostname=%s errors=%s",
                result.get("success"),
                result.get("action"),
                result.get("hostname"),
                result.get("error-codes", []),
            )
        return ok
    except Exception as e:
        log.error("Turnstile siteverify request failed: %s", e)
        return False


@app.post("/api/submit-event")
@limiter.limit("5/minute")
def submit_event(body: EventSubmission, request: Request):
    if not verify_turnstile(body.cf_turnstile_token, _client_ip(request)):
        raise HTTPException(status_code=403, detail="CAPTCHA verification failed")

    entry = body.model_dump(exclude={"cf_turnstile_token"})
    entry["submitted_at"] = datetime.now(timezone.utc).isoformat()
    entry["ip"] = request.client.host if request.client else None

    entries = _load_submissions()
    entries.append(entry)
    _save_submissions(entries)

    return {"status": "ok", "message": "Event submitted for review"}


@app.get("/api/submissions")
def list_submissions(_=Depends(_require_admin)):
    return _load_submissions()


@app.post("/api/submissions/clear")
def clear_submissions(_=Depends(_require_admin)):
    """Archive processed submissions and start fresh."""
    entries = _load_submissions()
    if entries:
        archive = SUBMISSIONS_PATH.with_suffix(
            f".{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.bak"
        )
        archive.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    _save_submissions([])
    return {"status": "ok", "archived": len(entries)}


@app.get("/health")
def health():
    return {"status": "ok"}
