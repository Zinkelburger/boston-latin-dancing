"""Store files: locking, strict JSON I/O, the changelog, and the queue
primitives every lifecycle module shares.

One lock covers the whole store. Every read/modify/write of any store file —
active, archive, pending, rejected, blocked, known duplicates, venues,
sources — runs under it, so the long-lived MCP server, the cron pipeline and
the review CLIs serialise instead of overwriting each other's saves. A single
store-wide lock (rather than one per file) means a multi-file move can never
deadlock on lock ordering. atomic_io.locked is re-entrant, so lifecycle
functions may call each other freely.
"""

import functools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import atomic_io

from . import paths


def log(msg: str) -> None:
    """Operator-facing progress and warnings. Always stderr: the MCP server
    speaks JSON-RPC on stdout, and a stray print there corrupts the stream."""
    print(msg, file=sys.stderr, flush=True)


def store_lock():
    """Context manager holding the store-wide lock (re-entrant).

    CLIs and the MCP server wrap any direct load_*/modify/save_* sequence in
    it so their write cannot race a lifecycle function in another process.
    """
    return atomic_io.locked(paths.STORE_LOCK)


def locked(fn):
    """Run a lifecycle function under the store-wide lock."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with atomic_io.locked(paths.STORE_LOCK):
            return fn(*args, **kwargs)
    return wrapper


def _read_json(path: Path) -> list[dict]:
    """Strict read: a missing store file is empty, a corrupt one raises.

    Returning [] on a parse error is how a truncated active.json used to read
    as "no events" and get written straight back over the real data.
    """
    return atomic_io.read_json(path, default=[])


def _write_json(path: Path, data) -> None:
    """Atomic write (unique temp file + fsync + rename)."""
    atomic_io.write_json(path, data)


def append_changelog(action: str, event_id: str, details: str = "") -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "event_id": event_id,
        "details": details,
    }
    atomic_io.append_line(paths.CHANGELOG, json.dumps(entry))


def load_active() -> list[dict]:
    return _read_json(paths.ACTIVE_JSON)


def load_archive() -> list[dict]:
    return _read_json(paths.ARCHIVE_JSON)


def load_pending() -> list[dict]:
    return _read_json(paths.PENDING_JSON)


def load_rejected() -> list[dict]:
    return _read_json(paths.REJECTED_JSON)


def load_blocked() -> list[dict]:
    return _read_json(paths.BLOCKED_JSON)


def save_active(events: list[dict]) -> None:
    _write_json(paths.ACTIVE_JSON, events)


def save_archive(events: list[dict]) -> None:
    _write_json(paths.ARCHIVE_JSON, events)


def save_pending(events: list[dict]) -> None:
    _write_json(paths.PENDING_JSON, events)


def save_rejected(events: list[dict]) -> None:
    _write_json(paths.REJECTED_JSON, events)


def save_blocked(events: list[dict]) -> None:
    _write_json(paths.BLOCKED_JSON, events)


@locked
def queue_rejected(event: dict, reason: str, review_type: str = "non_latin") -> dict:
    """Append or update an event in the rejected review queue."""
    rejected = load_rejected()
    now = datetime.now(timezone.utc).isoformat()
    record = dict(event)
    record["_rejected_reason"] = reason
    record["_review_type"] = review_type

    for i, existing in enumerate(rejected):
        if existing.get("id") == event.get("id"):
            record["_rejected_at"] = existing.get("_rejected_at", now)
            rejected[i] = record
            save_rejected(rejected)
            return record

    record["_rejected_at"] = now
    rejected.append(record)
    save_rejected(rejected)
    return record


def clear_stale_rejected(event_id: str) -> None:
    """Remove an event from rejected.json if it exists (prevents dual-store state)."""
    rejected = load_rejected()
    idx = next((i for i, ev in enumerate(rejected) if ev["id"] == event_id), None)
    if idx is not None:
        rejected.pop(idx)
        save_rejected(rejected)


def remove_from_active(event_id: str) -> None:
    """Drop an event from active.json if present (no-op otherwise)."""
    active = load_active()
    idx = next((i for i, ev in enumerate(active) if ev["id"] == event_id), None)
    if idx is not None:
        active.pop(idx)
        save_active(active)
