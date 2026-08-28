"""Shared fixtures for the hookclient test suite. Not itself a test module. Builds fixture
snapshot directories by hand (not via atlas.snapshot.publisher) so these tests demonstrate
hookclient works against the DOCUMENTED artifact shape, not merely whatever the sibling
component happens to produce today."""

import json
import os
import tempfile
from pathlib import Path


DEFAULT_INDEX = {
    "schema_version": "atlas-snapshot-2",
    "generated_at": "2026-01-01T00:00:00.000000Z",
    "refresh_cadence_seconds": 300,
    "max_age_seconds": 900,
    "warehouse_run_id": 1,
    "cursors": {"verdicts_stream_id": "abc", "verdicts_byte_offset": 1, "tessera_max_event_id": 1},
    "verdict_window_days": 7,
    "rate_uninterpretable_handlers": [],
    "severity_rubric": {"status": "absent", "version": None, "sha256": None, "blocker": "severity-rubric-pending", "consumer_contract": "status!='present' means CANNOT EVALUATE; never 'threshold not met'"},
    "roots": {
        "/proj/tess": {"prefixes": ["TESS"], "resolution": "unique"},
        "/proj/shared": {"prefixes": ["AREM", "FORE"], "resolution": "ambiguous"},
    },
    "unresolved_scopes": {"<ambiguous>": {"handlers": 1}, "<unregistered>": {"handlers": 0}, "<no-cwd>": {"handlers": 0}},
    "projects": {},
}


class FixtureSnapshot:
    def __init__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name) / "snapshot"
        self.root.mkdir()

    def close(self):
        self._tmpdir.cleanup()

    def write_generation(self, gen_name, index=None, shards=None, make_current=True):
        gen_dir = self.root / gen_name
        (gen_dir / "projects").mkdir(parents=True)
        index_data = index if index is not None else DEFAULT_INDEX
        (gen_dir / "index.json").write_text(json.dumps(index_data))
        for prefix, shard in (shards or {}).items():
            (gen_dir / "projects" / f"{prefix}.json").write_text(json.dumps(shard))
        if make_current:
            link = self.root / "current"
            if link.exists() or link.is_symlink():
                link.unlink()
            os.symlink(gen_name, link)
        return gen_dir
