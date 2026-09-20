"""ATLASSN-153: reads each project's `.foreman/GOALS.integration.json` and refreshes
`integration_interface`, the source table `v_integration_progress` (ARCHITECTURE.md section 37)
is built over.

Not an append-only log ingest like session_pull/subagent_pull, and deliberately shaped
differently (section 37.2's reasoning, not repeated here): every pass re-reads the whole file
fresh and treats it as the CURRENT declared state, so there is no byte-offset watermark and no
watermark-replay case. What this shape needs instead is reap-and-refresh, the same discipline
session_pull.refresh_dim_session() uses for the same reason -- a criterion withdrawn from the
file between two ingests must not leave a stale row behind.

Runnable standalone:

    /Users/m5/.venv/bin/python3 -m atlas.ingest.integration_interface_pull
"""

import argparse
import datetime
import json
from pathlib import Path

from atlas.warehouse import migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WAREHOUSE_DB = REPO_ROOT / "atlas" / "warehouse" / "atlas.db"

# The two projects this build's dashboard measures (ARCHITECTURE.md section 37.1). Adding a
# third project means adding it here -- deliberately not a directory scan, since which projects
# count is a scope decision (this build's own two repos), not a filesystem fact.
PROJECT_ROOTS = {
    "claude-hooks-v2": Path("/Users/m5/dev/claude-hooks-v2"),
    "atlas-sonnet": REPO_ROOT,
}

GOALS_INTEGRATION_RELPATH = Path(".foreman") / "GOALS.integration.json"


class IntegrationInterfacePullError(RuntimeError):
    pass


def nowIso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load_criteria(project, project_root):
    """Returns (criteria_list, source_path_str, criteria_hash_at_freeze) for one project.
    (empty list, path string, None) for a project with no file, an empty criteria array, or a
    file that fails to parse -- absence is a real, honest state (ATLASSN-146), not an error this
    ingest should raise on, since a project legitimately declaring nothing is expected. A file
    that EXISTS but is not valid JSON is different -- that is reported via the returned detail
    so the caller can decide whether to reap-only or raise, rather than being silently treated
    as "declares nothing"."""
    path = project_root / GOALS_INTEGRATION_RELPATH
    if not path.is_file():
        return [], str(path), None, None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], str(path), None, f"could not read {path}: {exc}"
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], str(path), None, f"could not parse {path} as JSON: {exc}"
    criteria = doc.get("criteria")
    if not isinstance(criteria, list):
        return [], str(path), None, f"{path} has no 'criteria' array"
    criteria_hash = (doc.get("integrity") or {}).get("criteria_hash_at_freeze")
    return criteria, str(path), criteria_hash, None


def refresh_project(conn, project, project_root):
    """One project's reap-and-refresh pass, in the caller's transaction. Returns
    (declared, error_detail) -- declared is the count of criteria now on record for this
    project (0 for a project with no file, matching ATLASSN-146's honest-zero picture);
    error_detail is None on a clean pass or a string naming what went wrong reading the file
    (the row set for this project is left untouched on a read/parse error -- reaping to zero on
    a transient read failure would be worse than serving one ingest cycle's stale data)."""
    criteria, source_path, criteria_hash, error_detail = _load_criteria(project, project_root)
    if error_detail is not None:
        return 0, error_detail

    now = nowIso()
    current_ids = [c.get("id") for c in criteria if isinstance(c, dict) and c.get("id")]

    if current_ids:
        placeholders = ",".join("?" for _ in current_ids)
        conn.execute(
            f"DELETE FROM integration_interface WHERE project = ? AND interface_id NOT IN "
            f"({placeholders})",
            [project] + current_ids,
        )
    else:
        conn.execute("DELETE FROM integration_interface WHERE project = ?", (project,))

    for criterion in criteria:
        if not isinstance(criterion, dict) or not criterion.get("id"):
            continue
        components = criterion.get("components")
        components_json = json.dumps(components if isinstance(components, list) else [])
        conn.execute(
            "INSERT INTO integration_interface (project, interface_id, components_json, "
            "statement, verification, verifiable, declared_added_at, source_path, "
            "criteria_hash_at_freeze, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (project, interface_id) DO UPDATE SET "
            "components_json = excluded.components_json, statement = excluded.statement, "
            "verification = excluded.verification, verifiable = excluded.verifiable, "
            "declared_added_at = excluded.declared_added_at, "
            "source_path = excluded.source_path, "
            "criteria_hash_at_freeze = excluded.criteria_hash_at_freeze, "
            "ingested_at = excluded.ingested_at",
            (
                project, criterion["id"], components_json,
                criterion.get("statement"), criterion.get("verification"),
                1 if criterion.get("verifiable") else 0,
                criterion.get("added"), source_path, criteria_hash, now,
            ),
        )
    return len(current_ids), None


def run(warehouse_db_path=DEFAULT_WAREHOUSE_DB, project_roots=None):
    """One pass over every registered project. Returns a dict of
    {project: {"declared": n, "error": str-or-None}}. Each project's reap-and-refresh runs in
    its own transaction, so one project's read failure cannot roll back another project's
    already-committed refresh."""
    project_roots = project_roots if project_roots is not None else PROJECT_ROOTS
    conn = migrate.connect(str(warehouse_db_path))
    results = {}
    try:
        for project, project_root in project_roots.items():
            conn.execute("BEGIN IMMEDIATE")
            try:
                declared, error_detail = refresh_project(conn, project, project_root)
            except Exception:
                conn.rollback()
                raise
            conn.commit()
            results[project] = {"declared": declared, "error": error_detail}
    finally:
        conn.close()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_WAREHOUSE_DB))
    args = parser.parse_args()
    results = run(Path(args.db))
    for project, detail in results.items():
        status = f"error: {detail['error']}" if detail["error"] else f"{detail['declared']} declared"
        print(f"{project}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
