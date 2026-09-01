import json
import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import atomic_io  # noqa: E402


def test_missing_file_returns_default(tmp_path):
    assert atomic_io.read_json(tmp_path / "nope.json") == []
    assert atomic_io.read_json(tmp_path / "nope.json", default={}) == {}


def test_corrupt_file_raises_instead_of_returning_empty(tmp_path):
    p = tmp_path / "active.json"
    p.write_text('[{"id": "a"', encoding="utf-8")
    with pytest.raises(atomic_io.CorruptJSONError):
        atomic_io.read_json(p)
    p.write_text("", encoding="utf-8")
    with pytest.raises(atomic_io.CorruptJSONError):
        atomic_io.read_json(p)


def test_write_is_atomic_and_leaves_no_temp_files(tmp_path):
    p = tmp_path / "sub" / "data.json"
    atomic_io.write_json(p, [{"id": "a", "name": "Ñandú"}])
    assert json.loads(p.read_text(encoding="utf-8")) == [{"id": "a", "name": "Ñandú"}]
    assert [f.name for f in p.parent.iterdir()] == ["data.json"]


def test_write_failure_does_not_touch_existing_file(tmp_path):
    p = tmp_path / "data.json"
    atomic_io.write_json(p, [1])
    with pytest.raises(TypeError):
        atomic_io.write_json(p, [object()])
    assert atomic_io.read_json(p) == [1]
    assert [f.name for f in tmp_path.iterdir()] == ["data.json"]


def test_append_line_adds_whole_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    atomic_io.append_line(p, '{"a": 1}')
    atomic_io.append_line(p, '{"b": 2}\n')
    assert p.read_text(encoding="utf-8") == '{"a": 1}\n{"b": 2}\n'


def test_locked_serialises_read_modify_write_across_threads(tmp_path):
    p = tmp_path / "counter.json"
    atomic_io.write_json(p, {"n": 0})

    def bump():
        for _ in range(50):
            with atomic_io.locked(p):
                d = atomic_io.read_json(p)
                d["n"] += 1
                atomic_io.write_json(p, d)

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert atomic_io.read_json(p)["n"] == 200


def test_locked_is_reentrant_for_same_path(tmp_path):
    p = tmp_path / "x.json"
    with atomic_io.locked(p):
        with atomic_io.locked(p):
            atomic_io.write_json(p, [])
    assert atomic_io.read_json(p) == []


def test_locked_blocks_other_process(tmp_path):
    """A second process must wait for the lock, not proceed."""
    import subprocess
    import time

    p = tmp_path / "x.json"
    script = (
        "import sys, time; sys.path.insert(0, %r); import atomic_io\n"
        "with atomic_io.locked(%r):\n"
        "    print('acquired', flush=True); time.sleep(0.5)\n"
    ) % (str(Path(atomic_io.__file__).parent), str(p))
    with atomic_io.locked(p):
        proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        time.sleep(0.3)
        assert proc.poll() is None  # still waiting on the lock
    out, _ = proc.communicate(timeout=5)
    assert "acquired" in out
