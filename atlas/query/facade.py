"""The read-only facade over the warehouse. ARCHITECTURE.md section 8: primary enforcement of
the trust gate lives here, not in the sentinel alone -- "the query facade reads it [v_atlas_status]
before returning anything."

Two guarantees this component adds beyond what the gated views already do at the SQL layer:
(1) a genuinely read-only connection (SQLite's own file:...?mode=ro, not a promise); (2) a
system-wide refusal when the warehouse's own last run is not genuinely clean, distinguishing
never-run / failed-run / checks-evaluated-zero (a known vacuous-pass case) from a real clean
pass, so a caller can never mistake "nothing has been checked" for "everything passed."
"""

import sqlite3

ALLOWED_VIEWS = frozenset({
    "v_atlas_status", "v_decision_outcome_rate", "v_deny_streak", "v_fail_open_incident",
    "v_gate_proven_live", "v_handler_denominator", "v_hook_latency_rollup", "v_hook_verdict",
    "v_pipeline_selfcheck", "v_project_resolution_coverage", "v_project_scope",
    "v_queryable_source", "v_snapshot_publishable", "v_source_freshness", "v_source_trust",
    "v_ticket_diff_binding", "v_trapped_agent_candidate", "v_verdict_confusion_matrix",
})


class QueryRefused(RuntimeError):
    pass


class QueryFacadeUnavailable(RuntimeError):
    pass


class Status:
    NEVER_RUN = "never-run"
    FAILED_RUN = "failed-run"
    CHECKS_NOT_EVALUATED = "checks-not-evaluated"
    CLEAN = "clean"

    def __init__(self, state, run_id, run_status, checks_evaluated, contract_failures, detail):
        self.state = state
        self.run_id = run_id
        self.run_status = run_status
        self.checks_evaluated = checks_evaluated
        self.contract_failures = contract_failures
        self.detail = detail

    def is_clean(self):
        return self.state == Status.CLEAN


class QueryFacade:
    def __init__(self, db_path):
        try:
            self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.OperationalError as exc:
            raise QueryFacadeUnavailable(
                f"cannot open {db_path} read-only: {exc} -- does it exist and is it migrated?"
            ) from exc
        try:
            self._conn.execute("SELECT 1 FROM ingest_run LIMIT 1")
        except sqlite3.OperationalError as exc:
            raise QueryFacadeUnavailable(
                f"{db_path} does not look like a migrated ATLAS warehouse: {exc}"
            ) from exc

    def close(self):
        self._conn.close()

    def status(self):
        row = self._conn.execute(
            "SELECT run_id, status, checks_evaluated, contract_failures FROM v_atlas_status"
        ).fetchone()
        if row is None or row[1] == "NO-INGEST-RUN-YET":
            return Status(Status.NEVER_RUN, None, None, None, None, "no ingest_run rows yet")
        run_id, run_status, checks_evaluated, contract_failures = row
        if run_status != "ok":
            return Status(
                Status.FAILED_RUN, run_id, run_status, checks_evaluated, contract_failures,
                f"most recent run (id={run_id}) has status={run_status!r}, not 'ok'",
            )
        if not checks_evaluated:
            return Status(
                Status.CHECKS_NOT_EVALUATED, run_id, run_status, checks_evaluated, contract_failures,
                f"run {run_id} is 'ok' but recorded 0 dq_check_run rows -- the known vacuous-pass "
                f"case, not a genuine clean result",
            )
        if contract_failures:
            return Status(
                Status.FAILED_RUN, run_id, run_status, checks_evaluated, contract_failures,
                f"run {run_id} evaluated {checks_evaluated} checks with {contract_failures} "
                f"contract failures",
            )
        return Status(
            Status.CLEAN, run_id, run_status, checks_evaluated, contract_failures,
            f"run {run_id}: {checks_evaluated} checks evaluated, 0 contract failures",
        )

    def fetch(self, view_name, where_sql=None, params=()):
        if view_name not in ALLOWED_VIEWS:
            raise QueryRefused(
                f"{view_name!r} is not one of the {len(ALLOWED_VIEWS)} declared gated views -- "
                f"this facade never reads a raw table, and never an undeclared name"
            )
        current = self.status()
        if not current.is_clean():
            raise QueryRefused(
                f"refusing to serve {view_name!r}: warehouse state is {current.state!r} "
                f"({current.detail})"
            )
        if where_sql is not None:
            raise QueryRefused(
                "where_sql is not accepted; this facade serves whole gated views only"
            )
        sql = f"SELECT * FROM {view_name}"
        cursor = self._conn.execute(sql, params)
        columns = [d[0] for d in cursor.description]
        return columns, cursor.fetchall()
