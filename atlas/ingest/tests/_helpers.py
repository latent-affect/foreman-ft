"""Shared test fixtures for the ingest test suite. Not itself a test module."""

import functools
import tempfile
from pathlib import Path

from atlas.ingest import stream, verdicts
from atlas.warehouse import migrate


class TempDb:
    def __init__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self._tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")

    def close(self):
        self.conn.close()
        self._tmpdir.cleanup()


def tail_verdicts(conn, source_name, path, run_id):
    insert = functools.partial(verdicts.insert_hook_verdict_row, ingest_run_id=run_id)
    return stream.tail(conn, source_name, path, verdicts.map_verdict_row, insert)


def verdict_line(handler_id, ts, epoch_ms, verdict="fire", session_id="sessA", cwd="/proj",
                  tool_use_id=None, **extra):
    import json
    obj = {
        "ts": ts, "epoch_ms": epoch_ms, "handler_id": handler_id, "verdict": verdict,
        "session_id": session_id, "cwd": cwd,
    }
    if tool_use_id is not None:
        obj["tool_use_id"] = tool_use_id
    obj.update(extra)
    return json.dumps(obj)


def padding_lines(n=60, prefix="pad"):
    """A block of real, valid verdict lines whose only purpose is pushing a fixture file past
    stream.STREAM_ID_HEAD_BYTES (4096) before the behavior under test happens -- stream_id is
    sha256 of the first 4096 bytes, so a fixture smaller than that has an unstable head (every
    append changes it, since the whole file IS the head), which is not what these tests are
    about. 60 lines is comfortably over 4096 bytes at this line format's real length."""
    return [
        verdict_line(f"{prefix}{i}", f"2020-01-01T00:{i:02d}:00.000001Z", 100000 + i)
        for i in range(n)
    ]
