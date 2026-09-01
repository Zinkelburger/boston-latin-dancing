"""Crash-safe, lock-protected JSON file IO shared by the store, scrapers,
MCP server, and review CLIs.

Three writers touch the same JSON files (the long-lived MCP server, the
cron pipeline, and the review CLIs). Every read/modify/write cycle must
therefore happen under ``locked(path)`` so that concurrent writers cannot
lose each other's updates, and every write must be atomic so that a crash
mid-write can never leave a truncated file behind.

Corruption is loud: ``read_json`` raises ``CorruptJSONError`` instead of
returning an empty list, because an empty list silently becomes "the store
has no events" and gets written back over the real data on the next save.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class CorruptJSONError(RuntimeError):
    """A JSON file exists but cannot be parsed. Refuse to treat it as empty."""

    def __init__(self, path: Path, cause: Exception):
        super().__init__(
            f"{path} is not valid JSON ({cause}). Refusing to treat it as empty; "
            f"restore it from git or fix it by hand before continuing."
        )
        self.path = path
        self.cause = cause


_MISSING = object()


def read_json(path: Path | str, default: Any = _MISSING) -> Any:
    """Read and parse a JSON file.

    A missing file returns ``default`` (or ``[]`` when no default is given).
    A file that exists but does not parse raises ``CorruptJSONError``. Never
    swallow that: the caller must not proceed as if the file were empty.
    """
    path = Path(path)
    if not path.exists():
        return [] if default is _MISSING else default
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        # A zero-length file is how a crashed non-atomic writer used to leave
        # things. Treat it the same as corrupt: it is never a legitimate state
        # for a store file that write_json produced.
        raise CorruptJSONError(path, ValueError("file is empty"))
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise CorruptJSONError(path, exc) from exc


def write_json(path: Path | str, data: Any, *, indent: int = 2) -> None:
    """Atomically write ``data`` as JSON to ``path``.

    Writes to a uniquely named temp file in the same directory, fsyncs it,
    then renames over the target. Readers see either the old file or the
    complete new one, never a partial write, and two concurrent writers can
    never rename each other's half-written temp file into place.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=indent, ensure_ascii=False)
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


def append_line(path: Path | str, line: str) -> None:
    """Append one line (e.g. a JSONL record) with O_APPEND semantics.

    Single ``write`` calls under O_APPEND are atomic on POSIX for the sizes
    we log, so concurrent appenders interleave whole lines, never bytes.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (line.rstrip("\n") + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


@contextmanager
def locked(path: Path | str) -> Iterator[None]:
    """Hold an exclusive lock for a read/modify/write of ``path``.

    Two layers: a per-process ``threading.RLock`` (flock is per process, so
    threads in one server would otherwise pass straight through) and an
    ``fcntl.flock`` on a sidecar ``<name>.lock`` file, which survives the
    rename that ``write_json`` performs. Re-entrant for the same path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    key = str(lock_path)
    with _registry_lock:
        entry = _locks.setdefault(key, _PathLock())
    with entry.rlock:
        if entry.depth:
            entry.depth += 1
            try:
                yield
            finally:
                entry.depth -= 1
            return
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            entry.depth = 1
            try:
                yield
            finally:
                entry.depth = 0
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class _PathLock:
    __slots__ = ("rlock", "depth")

    def __init__(self) -> None:
        self.rlock = threading.RLock()
        self.depth = 0


_registry_lock = threading.Lock()
_locks: dict[str, _PathLock] = {}
