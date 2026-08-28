"""Shared fixtures for the resolve test suite. Not itself a test module."""

import tempfile
from pathlib import Path

from atlas.warehouse import migrate


class TempDb:
    def __init__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self._tmpdir.name) / "test.db"))

    def close(self):
        self.conn.close()
        self._tmpdir.cleanup()


def register_project(conn, prefix, source_root, root_state="git-repo"):
    conn.execute(
        "INSERT INTO dim_project (project_prefix, project_codename, source_root, root_state, "
        "refreshed_at) VALUES (?, ?, ?, ?, '2026-01-01T00:00:00Z')",
        (prefix, prefix, source_root, root_state),
    )
    conn.commit()
