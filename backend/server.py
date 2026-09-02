"""
Boston Latin Dance – Event Submission API

Minimal FastAPI server that accepts event submissions from organizers
and appends them to a local JSON file for manual review.

Designed to run behind a Cloudflare Tunnel on a VPS (no public ports).

Deployed by copying this single file, so the locked/atomic JSON helpers
below are inlined rather than imported from scripts/atomic_io.py.
"""

import fcntl
import hmac
import json
import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import requests as http_requests
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator, model_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse

log = logging.getLogger("uvicorn.error")

SUBMISSIONS_PATH = Path(
    os.getenv("BLD_SUBMISSIONS_PATH", Path(__file__).parent / "submissions.json")
)
FRONTEND_ORIGIN = os.getenv(
    "BLD_FRONTEND_ORIGIN", "https://bostonsalsa.org"
)
ADMIN_TOKEN = os.getenv("BLD_ADMIN_TOKEN", "")
# We sit behind cloudflared, so the socket peer is always loopback and the
# real client only appears in CF-Connecting-IP / X-Forwarded-For. Set to
# "0"/"false" when running exposed without a proxy so those headers cannot be
# spoofed to dodge the rate limiter.
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "true").strip().lower() not in (
    "0", "false", "no", "off", ""
)
TURNSTILE_ACTION = "turnstile-spin-v2"
TURNSTILE_MAX_TOKEN_LEN = 2048
SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# Whole-request cap. The largest legitimate body (every field at its max
# length plus a Turnstile token) is well under 8 KB.
MAX_BODY_BYTES = 64 * 1024

# Mirrors STYLE_LABELS in lib/constants.ts (the form's checkboxes) and the
# accepted set in scripts/fetch_submissions.py.
ALLOWED_STYLES = frozenset({"bachata", "salsa", "kizomba", "zouk", "merengue", "other"})
MAX_STYLES = 10
ALLOWED_DAYS = frozenset({
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
})
ALLOWED_RECURRENCE_TYPES = frozenset({"weekly", "biweekly", "monthly"})
ALLOWED_WEEKS = frozenset({"1st", "2nd", "3rd", "4th", "Last"})


def _cors_origins_from_env(raw: str) -> list[str]:
    origins: list[str] = []
    for origin in (o.strip().rstrip("/") for o in raw.split(",")):
        if origin and origin not in origins:
            origins.append(origin)
        if origin.startswith("https://www."):
            apex = "https://" + origin.removeprefix("https://www.")
            if apex not in origins:
                origins.append(apex)
        elif origin.startswith("https://"):
            www = "https://www." + origin.removeprefix("https://")
            if www not in origins:
                origins.append(www)
    return origins


_cors_origins = _cors_origins_from_env(FRONTEND_ORIGIN)
if os.getenv("BLD_DEBUG"):
    _cors_origins.append("http://localhost:3000")


# ── Client identity ───────────────────────────────────────────────────


def _client_ip(request: Request) -> str | None:
    """The real client address, seen through cloudflared.

    Prefers CF-Connecting-IP (set by Cloudflare, cannot be forged through
    the tunnel), then the first X-Forwarded-For hop, then the socket peer.
    Header trust is gated on TRUST_PROXY_HEADERS so an instance exposed
    directly cannot be fooled into a per-request rate-limit bucket.
    """
    if TRUST_PROXY_HEADERS:
        cf_ip = request.headers.get("CF-Connecting-IP", "").strip()
        if cf_ip:
            return cf_ip
        xff = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if xff:
            return xff
    return (request.client.host if request.client else None) or None


def _rate_limit_key(request: Request) -> str:
    # get_remote_address would key every request on 127.0.0.1 behind the
    # tunnel, turning "5/minute per client" into "5/minute for the site".
    return _client_ip(request) or "unknown"


limiter = Limiter(key_func=_rate_limit_key)
app = FastAPI(title="BLD Event Submissions", docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class BodySizeLimitMiddleware:
    """Reject request bodies over MAX_BODY_BYTES before they are parsed.

    Pure ASGI so it runs ahead of routing and validation. A body-bearing
    request without Content-Length (chunked) is refused outright rather
    than streamed and counted: nothing legitimate posts to this API that way.
    """

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") in ("POST", "PUT", "PATCH"):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                       for k, v in scope.get("headers", [])}
            raw_len = headers.get("content-length")
            if raw_len is None:
                if headers.get("transfer-encoding", "").lower() == "chunked":
                    response = JSONResponse({"detail": "Content-Length required"}, status_code=411)
                    await response(scope, receive, send)
                    return
                raw_len = "0"
            try:
                length = int(raw_len)
            except ValueError:
                response = JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
                await response(scope, receive, send)
                return
            if length > self.max_bytes:
                response = JSONResponse(
                    {"detail": f"Request body too large (max {self.max_bytes} bytes)"},
                    status_code=413,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# Added before CORS so CORS (added last, hence outermost) wraps it: an
# oversized POST is still answered with the CORS headers the browser needs
# to show the 413 to the form instead of a generic network error.
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

_bearer = HTTPBearer()


def _require_admin(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    # Constant-time compare: a plain != leaks the length of the matching prefix
    # through response timing.
    if not ADMIN_TOKEN or not hmac.compare_digest(
        creds.credentials.encode("utf-8"), ADMIN_TOKEN.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Invalid admin token")
    return creds


# ── Submission schema ─────────────────────────────────────────────────


class EventSubmission(BaseModel):
    email: str = Field("", max_length=200)
    instagram: str = Field("", max_length=200)
    event_name: str = Field(..., min_length=1, max_length=200)
    event_url: str = Field(..., min_length=1, max_length=500)
    styles: list[str] = Field(default_factory=list, max_length=MAX_STYLES)
    location: str = Field("", max_length=300)
    is_recurring: bool = False
    date: str = Field("", max_length=20)
    time: str = Field("", max_length=20)
    recurrence_type: str = Field("", max_length=20)
    day_of_week: str = Field("", max_length=20)
    week_of_month: str = Field("", max_length=20)
    start_date: str = Field("", max_length=20)
    notes: str = Field("", max_length=2000)
    cf_turnstile_token: str = Field("", max_length=TURNSTILE_MAX_TOKEN_LEN)

    @field_validator("styles")
    @classmethod
    def known_styles(cls, styles: list[str]) -> list[str]:
        unknown = sorted({s for s in styles if s not in ALLOWED_STYLES})
        if unknown:
            raise ValueError(
                f"Unknown style(s): {', '.join(unknown)}. "
                f"Allowed: {', '.join(sorted(ALLOWED_STYLES))}"
            )
        return styles

    @field_validator("recurrence_type")
    @classmethod
    def known_recurrence_type(cls, value: str) -> str:
        if value and value not in ALLOWED_RECURRENCE_TYPES:
            raise ValueError(f"Unknown recurrence_type '{value}'")
        return value

    @field_validator("day_of_week")
    @classmethod
    def known_day(cls, value: str) -> str:
        if value and value not in ALLOWED_DAYS:
            raise ValueError(f"Unknown day_of_week '{value}'")
        return value

    @field_validator("week_of_month")
    @classmethod
    def known_week(cls, value: str) -> str:
        if value and value not in ALLOWED_WEEKS:
            raise ValueError(f"Unknown week_of_month '{value}'")
        return value

    @model_validator(mode="after")
    def require_contact(self):
        if not self.email.strip() and not self.instagram.strip():
            raise ValueError("Provide at least an email or Instagram handle")
        return self


# ── Submissions storage ───────────────────────────────────────────────
#
# Handlers are sync, so uvicorn runs them on a threadpool: two posts can be
# in flight at once. Every read/modify/write of the file happens under
# _store_lock (a sidecar flock so a second process — a manual repair, say —
# is excluded too, plus a threading lock for the in-process case), and every
# write goes temp+fsync+rename so a crash mid-write cannot truncate the file.

_store_thread_lock = threading.Lock()


@contextmanager
def _store_lock() -> Iterator[None]:
    SUBMISSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = SUBMISSIONS_PATH.with_name(SUBMISSIONS_PATH.name + ".lock")
    with _store_thread_lock:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _load_submissions() -> list[dict]:
    """Read the store. Caller must hold _store_lock.

    A file that exists but does not parse is NEVER treated as empty — that
    is how a truncated store used to get overwritten by the next post. It is
    moved aside to submissions.corrupt-<timestamp>.json for recovery and a
    fresh list starts.
    """
    if not SUBMISSIONS_PATH.exists():
        return []
    text = SUBMISSIONS_PATH.read_text(encoding="utf-8")
    try:
        if not text.strip():
            raise ValueError("file is empty")
        entries = json.loads(text)
        if not isinstance(entries, list):
            raise ValueError("top level is not a list")
        return entries
    except (json.JSONDecodeError, ValueError) as exc:
        aside = SUBMISSIONS_PATH.with_name(
            f"{SUBMISSIONS_PATH.stem}.corrupt-{_timestamp()}{SUBMISSIONS_PATH.suffix}"
        )
        os.replace(SUBMISSIONS_PATH, aside)
        log.error(
            "Submissions store %s is corrupt (%s); moved aside to %s and starting fresh",
            SUBMISSIONS_PATH, exc, aside,
        )
        return []


def _save_submissions(entries: list[dict]) -> None:
    """Atomically replace the store. Caller must hold _store_lock."""
    _write_json_atomic(SUBMISSIONS_PATH, entries)


def _archive_path() -> Path:
    return SUBMISSIONS_PATH.with_name(
        f"{SUBMISSIONS_PATH.stem}-archive-{_timestamp()}{SUBMISSIONS_PATH.suffix}"
    )


# ── Turnstile ─────────────────────────────────────────────────────────


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


# ── Routes ────────────────────────────────────────────────────────────


@app.post("/api/submit-event")
@limiter.limit("5/minute")
def submit_event(body: EventSubmission, request: Request):
    ip = _client_ip(request)
    if not verify_turnstile(body.cf_turnstile_token, ip):
        raise HTTPException(status_code=403, detail="CAPTCHA verification failed")

    entry = body.model_dump(exclude={"cf_turnstile_token"})
    entry["submitted_at"] = datetime.now(timezone.utc).isoformat()
    entry["ip"] = ip

    with _store_lock():
        entries = _load_submissions()
        entries.append(entry)
        _save_submissions(entries)

    return {"status": "ok", "message": "Event submitted for review"}


@app.get("/api/submissions")
def list_submissions(_=Depends(_require_admin)):
    with _store_lock():
        return _load_submissions()


@app.post("/api/submissions/clear")
def clear_submissions(_=Depends(_require_admin)):
    """Archive processed submissions and start fresh.

    Runs under the store lock so a post landing mid-clear either precedes
    the archive (and is in it) or follows the reset (and is in the fresh
    list) — never lost between the two.
    """
    with _store_lock():
        entries = _load_submissions()
        archived_to = None
        if entries:
            archive = _archive_path()
            _write_json_atomic(archive, entries)
            archived_to = archive.name
        _save_submissions([])
    return {"status": "ok", "archived": len(entries), "archive_file": archived_to}


@app.get("/health")
def health():
    return {"status": "ok"}
