"""Shared test fixtures: a throwaway TESSERA store and synthetic transcript files, real
enough to exercise tessguard's real code paths without touching the real
/Users/m5/dev/ticket-system/data/tessera.db.
"""

import json
import tempfile
from pathlib import Path

from tessera.store.store import Store


def make_store(tmpdir, prefix="TEST", source_root="/tmp/fake-repo"):
    db_path = Path(tmpdir) / "test.db"
    # Deliberately NOT passing codename/prefix to Store() -- that path auto-registers the
    # project with source_root=None, and register_project() is idempotent (a second call
    # with source_root doesn't overwrite it). Register explicitly, once, with source_root.
    store = Store(str(db_path))
    store.register_project(prefix, prefix, source_root=source_root)
    return store, db_path


def make_ticket(store, prefix, actor="tester"):
    return store.create_ticket(
        ticket_type="Task", reporter=actor, actor=actor, project=prefix,
        summary="fixture ticket",
    )


def write_transcript(path, records):
    """records: list of dicts, each a full transcript-line record. Written as JSONL."""
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def edit_record(cwd, timestamp, file_path, tool_name="Edit"):
    return {
        "type": "assistant",
        "cwd": cwd,
        "timestamp": timestamp,
        "message": {
            "content": [
                {"type": "tool_use", "name": tool_name, "input": {"file_path": file_path}}
            ]
        },
    }


def bash_record(cwd, timestamp, command):
    return {
        "type": "assistant",
        "cwd": cwd,
        "timestamp": timestamp,
        "message": {
            "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": command}}
            ]
        },
    }


def plain_record(cwd, timestamp):
    return {"type": "user", "cwd": cwd, "timestamp": timestamp, "message": {"content": []}}
