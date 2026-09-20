"""QA pilot ingester -- NOT a formal ATLAS migration.

Operator-authorized pilot (2026-09-02, dev-harness Run 2 build): wire tonight's real
token-bloat/code-quality report JSON into the ATLAS warehouse for an early, queryable read,
as supporting evidence for Marcus Webb's ship-readiness review. Deliberately lightweight:
a single clearly-named pilot table (`qa_pilot_snapshot`), not a section in ARCHITECTURE.md, not
run through migrate.py's apply_additive()/schema_migration drift-detection discipline the way
every other table in this warehouse is. Reuses migrate.connect() for the connection itself (WAL,
foreign keys) since that's just correct SQLite hygiene, not a claim to be a real migration.

If this pilot proves useful, promoting it to a real migration (ARCHITECTURE.md section, ddl.py
extraction function, apply_migrationN, a real ATLASSN ticket, design-and-scope) is separate,
larger work -- not done here. Says so in the table's own name and in every row's `pilot=1` marker
so a later reader never mistakes this for an officially versioned table.
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from atlas.warehouse import migrate  # noqa: E402

DEFAULT_WAREHOUSE_DB = Path(__file__).resolve().parents[2] / "atlas" / "warehouse" / "atlas.db"

CREATE_PILOT_TABLE = """
CREATE TABLE IF NOT EXISTS qa_pilot_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ingested_at TEXT NOT NULL,
    pilot INTEGER NOT NULL DEFAULT 1,
    source_project TEXT NOT NULL,
    source_file TEXT NOT NULL,
    report_kind TEXT NOT NULL,
    tool_git_sha TEXT,
    tree_git_sha TEXT,
    formula_version TEXT,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    metric_text TEXT,
    raw_meta_json TEXT
)
"""


def _rows_from_quality_baseline(project, path, obj):
    meta = obj.get("meta", {})
    agg = obj.get("aggregate", {})
    common = {
        "source_project": project,
        "source_file": str(path),
        "report_kind": "quality_baseline",
        "tool_git_sha": obj.get("tool_git_sha"),
        "tree_git_sha": obj.get("tree_git_sha"),
        "formula_version": obj.get("formula_version"),
        "raw_meta_json": json.dumps(meta),
    }
    rows = []
    for name, value in [
        ("python_files_analyzed", meta.get("python_files_analyzed")),
        ("total_functions_analyzed", meta.get("total_functions_analyzed")),
        ("hotspot_signal_degenerate_churn", 1 if meta.get("hotspot_signal_degenerate_churn") else 0),
        ("simple_average_health_1_to_10", agg.get("simple_average_health_1_to_10")),
        ("sloc_weighted_average_health_1_to_10", agg.get("sloc_weighted_average_health_1_to_10")),
    ]:
        if value is None:
            continue
        rows.append({**common, "metric_name": name, "metric_value": float(value) if isinstance(value, (int, float)) else None,
                     "metric_text": None if isinstance(value, (int, float)) else str(value)})
    return rows


def _rows_from_token_bloat(project, path, obj):
    meta = obj.get("meta", obj)
    overall = obj.get("overall", {})
    common = {
        "source_project": project,
        "source_file": str(path),
        "report_kind": "token_bloat_diagnostic",
        "tool_git_sha": obj.get("tool_git_sha"),
        "tree_git_sha": obj.get("tree_git_sha"),
        "formula_version": obj.get("formula_version"),
        "raw_meta_json": json.dumps(meta),
    }
    rows = []
    for name, value in [
        ("latest_vs_day_one_ratio", overall.get("latest_vs_day_one_ratio")),
        ("cache_hit_pct", overall.get("cache_hit_pct")),
        ("spike_count", obj.get("spike_count")),
    ]:
        if value is None:
            continue
        rows.append({**common, "metric_name": name, "metric_value": float(value) if isinstance(value, (int, float)) else None,
                     "metric_text": None if isinstance(value, (int, float)) else str(value)})
    return rows


def _rows_from_qa_verdict_record(project, path, obj):
    """FORE-393 (FORE-329-FABLE-D): a real Alice/Bob dispatch by-session record, one JSON file
    per dispatch, written by that rebuild's dispatch_writer.py (FORE-398) with a `qa_verdict`
    key holding FABLE-H qa_scorer's own compute_verdict() output verbatim -- verified directly
    against dispatch_writer.py's real code (not the ledger the scorer module's own persist_verdict
    would produce, which is never actually called anywhere in that rebuild's real wiring, checked
    this session). `qa_verdict` is unconditionally present on any record dispatch_writer actually
    writes (the scorer-verdict-failing/-absent paths raise before a record is constructed), but
    this stays defensive against a malformed or hand-edited file rather than assuming it."""
    verdict = obj.get("qa_verdict")
    if not isinstance(verdict, dict):
        return []
    kpi_results = verdict.get("kpi_results")
    if not isinstance(kpi_results, dict):
        kpi_results = {}
    common = {
        "source_project": project,
        "source_file": str(path),
        "report_kind": "qa_scorer_verdict",
        "tool_git_sha": verdict.get("tool_git_sha"),
        "tree_git_sha": verdict.get("tree_git_sha"),
        "formula_version": verdict.get("formula_version"),
        "raw_meta_json": json.dumps({
            "dispatch_id": obj.get("dispatch_id"),
            "kpi_detail": verdict.get("kpi_detail"),
        }),
    }
    rows = []
    for name, passed in kpi_results.items():
        rows.append({**common, "metric_name": f"qa_kpi_{name}",
                     "metric_value": 1.0 if passed else 0.0, "metric_text": None})
    return rows


SOURCES = [
    ("dev-harness", Path("/Users/m5/dev/dev-harness/quality_baseline_report.json"), _rows_from_quality_baseline),
    ("dev-harness", Path("/Users/m5/dev/dev-harness/token_bloat_diagnostic.json"), _rows_from_token_bloat),
    ("dev-harness-run2", Path("/Users/m5/dev/dev-harness-run2/quality_baseline_report.json"), _rows_from_quality_baseline),
    # FORE-393: real source is a DIRECTORY of per-dispatch by-session record files, not one fixed
    # file -- dispatch_writer.py's own DEFAULT_DISPATCH_ROOT (FORE-398, verified directly against
    # its real code this session, not guessed), joined with "by-session" the same way that module
    # derives it. Handled by run()'s directory branch below. Empty/missing today (E1 write-block
    # still open, nothing has landed to run against this default root for real yet) -- this entry
    # is forward-looking, picking up real records the moment that pipeline actually runs.
    ("alice-bob-rebuild-fable-h",
     Path.home() / ".claude" / "foreman" / "alice-bob-dispatch" / "by-session",
     _rows_from_qa_verdict_record),
]


def run(warehouse_db_path=DEFAULT_WAREHOUSE_DB):
    import datetime
    conn = migrate.connect(str(warehouse_db_path))
    conn.execute(CREATE_PILOT_TABLE)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    inserted = 0
    skipped = []

    def _insert(row):
        row["ingested_at"] = now
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO qa_pilot_snapshot ({', '.join(cols)}) VALUES ({placeholders})",
            [row[c] for c in cols],
        )

    for project, path, mapper in SOURCES:
        if not path.exists():
            skipped.append(str(path))
            continue
        if path.is_dir():
            record_paths = sorted(path.glob("*.json"))
            if not record_paths:
                skipped.append(str(path))
            for record_path in record_paths:
                try:
                    obj = json.loads(record_path.read_text())
                except (json.JSONDecodeError, OSError) as exc:
                    skipped.append(f"{record_path} (unreadable: {exc})")
                    continue
                for row in mapper(project, record_path, obj):
                    _insert(row)
                    inserted += 1
            continue
        obj = json.loads(path.read_text())
        for row in mapper(project, path, obj):
            _insert(row)
            inserted += 1
    conn.commit()
    return inserted, skipped, conn


if __name__ == "__main__":
    n, skipped, conn = run()
    print(f"Inserted {n} rows into qa_pilot_snapshot.")
    if skipped:
        print("Skipped (file not found):", skipped)
    print()
    print("Early read:")
    for row in conn.execute(
        "SELECT source_project, report_kind, metric_name, metric_value, metric_text, ingested_at "
        "FROM qa_pilot_snapshot ORDER BY id"
    ):
        print(" ", row)
    conn.close()
