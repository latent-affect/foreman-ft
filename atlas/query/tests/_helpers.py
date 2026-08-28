"""Shared fixtures for the query test suite. Not itself a test module."""

import tempfile
from pathlib import Path

from atlas.warehouse import migrate


class TempWarehouse:
    """Read-only facades need a real file path (SQLite's ro URI mode can't target :memory:),
    so this holds both a writable setup connection and the path a facade opens separately."""

    def __init__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "test.db")
        self.setup_conn = migrate.connect(self.db_path)

    def close(self):
        self.setup_conn.close()
        self._tmpdir.cleanup()


def make_clean_run(conn, with_verdicts=True):
    run_id = migrate.new_ingest_run(conn, status="ok")
    check_names = [r[0] for r in conn.execute("SELECT check_name FROM dq_check")]
    for name in check_names:
        conn.execute(
            "INSERT INTO dq_check_run (run_id, check_name, run_at, passed, detail) "
            "VALUES (?, ?, '2026-01-01T00:00:00Z', 1, 'synthetic-pass')",
            (run_id, name),
        )
    if with_verdicts:
        conn.execute(
            "INSERT INTO hook_verdict (stream_id, byte_offset, ingest_run_id, ts, epoch_ms, "
            "ts_resolution, handler_id, hook_event, verdict, session_id, cwd, tool_use_id, decision, rule_id) "
            "VALUES ('s1', 0, ?, '2026-01-01T00:00:00.000001Z', 1000, 'microsecond', "
            "'architecture_gate.py', 'PreToolUse', 'fire', 'sess1', '/proj', 'tu1', 'deny', 'R1')",
            (run_id,),
        )
    conn.commit()
    return run_id
