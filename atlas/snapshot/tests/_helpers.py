"""Shared fixtures for the snapshot test suite. Not itself a test module."""

import tempfile
from pathlib import Path

from atlas.warehouse import migrate


class TempWarehouse:
    def __init__(self, clean=True):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "test.db")
        self.conn = migrate.connect(self.db_path)
        if clean:
            self.make_clean()

    def make_clean(self):
        run_id = migrate.new_ingest_run(self.conn, status="ok")
        check_names = [r[0] for r in self.conn.execute("SELECT check_name FROM dq_check")]
        for name in check_names:
            self.conn.execute(
                "INSERT INTO dq_check_run (run_id, check_name, run_at, passed, detail) "
                "VALUES (?, ?, '2026-01-01T00:00:00Z', 1, 'synthetic-pass')",
                (run_id, name),
            )
        self.conn.commit()
        return run_id

    def fail_check(self, check_name):
        """Flips one already-passing dq_check_run row to failed, in place -- for the negative
        control that a contract failure on a source the snapshot does NOT read (source_table
        != 'hook_verdict', not in snapshot_source) must not block publication. Requires
        make_clean() (or clean=True at construction) to have already seeded a row for it."""
        self.conn.execute(
            "UPDATE dq_check_run SET passed = 0 WHERE check_name = ? "
            "AND run_id = (SELECT MAX(run_id) FROM ingest_run)",
            (check_name,),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
        self._tmpdir.cleanup()


def default_dim_projects():
    return [
        {"prefix": "TESS", "codename": "TESSERA", "source_root": "/proj/tess", "root_state": "git-repo"},
        {"prefix": "FORE", "codename": "FOREMAN", "source_root": "/proj/shared", "root_state": "git-repo"},
        {"prefix": "AREM", "codename": "AGENT-REMEDIATION", "source_root": "/proj/shared", "root_state": "git-repo"},
    ]


def default_ticket_rollup():
    return {
        "TESS": {"open_total": 17, "open_by_severity": {"S3": 1}, "open_null_severity": 16, "open_null_tier": 15},
        "FORE": {"open_total": 47, "open_by_severity": {"S1": 1}, "open_null_severity": 46, "open_null_tier": 43},
        "AREM": {"open_total": 1, "open_by_severity": {}, "open_null_severity": 1, "open_null_tier": 0},
    }


def default_verdict_rollups():
    return {
        "TESS": {"architecture_gate.py": {"": {"silent": 100, "fire": 5}}},
        "FORE": {"guard_destructive.py": {"": {"silent": 50}, "deny": {"fire": 2}}},
        "AREM": {"guard_destructive.py": {"": {"silent": 50}, "deny": {"fire": 2}}},
    }


def publish_kwargs(**overrides):
    kwargs = dict(
        dim_projects=default_dim_projects(),
        ticket_rollup=default_ticket_rollup(),
        verdict_rollups=default_verdict_rollups(),
        cursors={"verdicts_stream_id": "abc123", "verdicts_byte_offset": 1000, "tessera_max_event_id": 1},
        unresolved_scopes={"<ambiguous>": {"handlers": 1}, "<unregistered>": {"handlers": 0}, "<no-cwd>": {"handlers": 0}},
        rate_uninterpretable_handlers=["guard_untrusted_web.py"],
        severity_rubric={"status": "absent", "version": None, "sha256": None, "blocker": "FORE-32", "consumer_contract": "status!='present' means CANNOT EVALUATE; never 'threshold not met'"},
        refresh_cadence_seconds=300,
        warehouse_run_id=1,
        verdict_window_days=7,
    )
    kwargs.update(overrides)
    return kwargs
