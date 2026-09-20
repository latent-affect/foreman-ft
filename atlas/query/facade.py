"""The read-only facade over the warehouse. ARCHITECTURE.md section 8: primary enforcement of
the trust gate lives here, not in the sentinel alone -- "the query facade reads it [v_atlas_status]
before returning anything."

Two guarantees this component adds beyond what the gated views already do at the SQL layer:
(1) a genuinely read-only connection (SQLite's own file:...?mode=ro, not a promise); (2) a
refusal when the warehouse's own last run is not genuinely clean, distinguishing never-run /
failed-run / checks-evaluated-zero (MAJOR-1's vacuous-pass case) from a real clean pass, so a
caller can never mistake "nothing has been checked" for "everything passed."

ATLASSN-103 (ARCHITECTURE.md section 8 addendum, amendments A1-A4): guarantee (2)'s run-level
half stays global exactly as before -- NEVER_RUN, a non-'ok' run status, and the MAJOR-1
vacuous-pass case (CHECKS_NOT_EVALUATED) all still refuse every batch view. What changes is the
CONTRACT-FAILURE dimension: this facade previously collapsed any nonzero global
contract_failures sum into the same FAILED_RUN state, which meant one unrelated source's
contract failure blinded every gated view regardless of what that view actually depends on
(FOREMAN-ROUTER-PROJECT-SPEC.md section 5's named router-blocking prerequisite). A contract
failure now scopes to fetch()'s own per-view dependency check (VIEW_SOURCE_DEPENDENCIES, A2)
against v_queryable_source -- status() no longer folds it into `state` at all, though the raw
count is still carried on the returned Status for a caller that wants it directly.

FIX 2026-09-20 (ATLASSN-190, Iris Chen's real, executed TOCTOU probe). fetch() performed THREE
separate unguarded reads on the single autocommit connection -- self.status(),
self._queryable_sources(), then the real SELECT -- with nothing wrapping them in one snapshot.
Reproduced live, deterministically: a concurrent writer committing a new ingest run with real
failing contract checks BETWEEN the queryable-sources check and the final SELECT let fetch()
return real rows for a source the warehouse had, by SELECT time, already certified contract-
failing -- a caller relying on fetch() to mean "this data just passed its trust gate" could
receive stale-trusted data during any window where an ingest run lands mid-query, which is
plausible in production since ingest and query are separate, uncoordinated processes. Fixed by
wrapping fetch()'s three reads in one explicit SQLite read transaction (BEGIN DEFERRED), so all
three observe the SAME point-in-time snapshot regardless of what a concurrent writer commits in
between -- the same guarantee status()/`_queryable_sources()`/the SELECT already relied on
holding informally, now actually enforced. The transaction is always closed via try/finally,
including on every existing QueryRefused path fetch() can still raise, so a refusal never leaves
the shared connection sitting inside an open transaction for the NEXT caller's fetch() to
silently inherit a stale snapshot from.
"""

import re
import sqlite3

# A2 (ARCHITECTURE.md section 8 addendum). Codomain is dq_check.source_table NAMES, not
# physical tables: 'tessera' has no same-named table; dim_project/git_commit_ticket/
# git_commit_file are derived and translated via SOURCE_TABLE_TRANSLATION below, confirmed
# against the real pull code by the architecture pass's own independent falsification
# (atlas-sonnet-92, TESSERA comment 2119 on ATLASSN-103, SQLite's query authorizer against the
# live warehouse) -- dim_project genuinely reads TESSERA's live projects table
# (run_pull.py:sync_dim_project), while git_commit_ticket is NOT tessera-derived in current
# reality despite its schema's `origin` column naming a 'tessera-link' possibility that is
# never written anywhere; live data is 1,361/1,361 rows via commit-message-regex, already
# covered by git_commit_file's own entry. Both therefore translate to 'git_commit', not
# 'tessera'.
#
# The real dependency map itself is copied from that same addendum's table, derived
# mechanically by transitive base-table closure and independently confirmed 19-of-20 rows
# exact by the authorizer-based falsification (the one open row, v_ticket_diff_binding's
# `tessera` entry, is disclosed there as schema-correct but practically meaningless today --
# 'tessera' gating is a label, not a real check, until a real registry-pull-health check
# exists; named, not fixed, by that same addendum, and this map keeps the label as declared
# rather than silently dropping it).
#
# v_registered_never_proven_recent (ATLASSN-181, migration 22) is mapped here even though its
# own migration is not yet applied (see atlas/warehouse/migrate.py's own commented-out
# apply_migration22 call) -- A4 requires every ALLOWED_VIEWS entry to have a map entry, and it
# is already a member of ALLOWED_VIEWS below. Its own CREATE VIEW joins v_gate_proven_live and
# v_handler_freshness, both hook_verdict-derived, so its transitive closure is {hook_verdict}.
#
# FIX 2026-09-20 (Bob's real-test finding, TESSERA comment 3800 on ATLASSN-188): migration 24
# (ATLASSN-98, this same proposal) re-points v_source_freshness's CREATE VIEW to UNION ALL in a
# git_commit corpus row (C11) the moment ATLASSN-188 wires that migration into connect() -- the
# view's own real SQL now names git_commit, so this entry must too, or fetch('v_source_freshness')
# would gate on session_transcript/subagent_transcript alone while actually reading a third,
# undeclared source. Mechanically re-derivable once migration 24 is live: compute_transitive_
# base_tables('v_source_freshness') finds ingest_source, session_transcript, subagent_transcript,
# git_commit and ingest_run as real sqlite_master names mentioned in the view's SQL;
# translate_source_tables() drops ingest_source and ingest_run (both outside REAL_SOURCE_TABLES,
# the trust-gate machinery's own plumbing, same as comment 3785's cwd_project/ingest_source
# fix), leaving exactly {session_transcript, subagent_transcript, git_commit} -- this entry.
VIEW_SOURCE_DEPENDENCIES = {
    "v_decision_outcome_rate": frozenset({"hook_verdict"}),
    "v_deny_streak": frozenset({"hook_verdict"}),
    "v_gate_proven_live": frozenset({"hook_verdict"}),
    "v_handler_denominator": frozenset({"hook_verdict"}),
    "v_handler_freshness": frozenset({"hook_verdict"}),
    "v_hook_latency_rollup": frozenset({"hook_verdict"}),
    "v_hook_verdict": frozenset({"hook_verdict"}),
    "v_project_resolution_coverage": frozenset({"hook_verdict"}),
    "v_project_scope": frozenset({"hook_verdict"}),
    "v_trapped_agent_candidate": frozenset({"hook_verdict"}),
    "v_verdict_confusion_matrix": frozenset({"hook_verdict"}),
    "v_registered_never_proven_recent": frozenset({"hook_verdict"}),
    "v_fail_open_incident": frozenset({"hook_verdict", "audit_event"}),
    "session_prior": frozenset({"hook_verdict", "subagent_transcript"}),
    "v_source_freshness": frozenset({"session_transcript", "subagent_transcript", "git_commit"}),
    "v_ticket_diff_binding": frozenset({"git_commit", "tessera_event", "tessera"}),
    "v_git_commit_detail": frozenset({"git_commit"}),
    # A3: the five trust-plumbing views map to the empty set and stay servable under a
    # contract failure, still behind the run-level gate above (v_atlas_status is the one
    # further exception to THAT gate too -- see fetch()'s own special case below).
    "v_atlas_status": frozenset(),
    "v_pipeline_selfcheck": frozenset(),
    "v_queryable_source": frozenset(),
    "v_snapshot_publishable": frozenset(),
    "v_source_trust": frozenset(),
}

# A2's translation: a physical table/view sqlite_master actually names, mapped to the
# dq_check.source_table name the declared map above uses. Applied by
# translate_source_tables() after the mechanical closure below finds the real physical names.
SOURCE_TABLE_TRANSLATION = {
    "dim_project": "tessera",
    "git_commit_ticket": "git_commit",
    "git_commit_file": "git_commit",
}

# The real dq_check.source_table domain -- ticket comment 2119 (independent falsification,
# thrice-reviewed: 2119, 2137/Clint, 2228/Opus cold falsification), re-verified live 2026-09-20
# via `SELECT DISTINCT source_table FROM dq_check` against the real atlas/warehouse/atlas.db, and
# independently cross-checked here against ARCHITECTURE.md section 8 addendum's own text: the 7
# non-advisory names the addendum's dependency-map table and prose actually mention by name
# (hook_verdict, audit_event, tessera_event, tessera, session_transcript, subagent_transcript,
# git_commit) plus the addendum's own named 2 advisory-only names (session_assistant_text,
# session_tool_call) sum to exactly the 9 the addendum states dq_check names in total.
# Anything the mechanical closure finds that is NOT a member of this set, even after
# SOURCE_TABLE_TRANSLATION, is a real physical table SQLite's compiler genuinely touches but
# that can never be a valid dq_check.source_table value -- comment 2119's own named plumbing-
# table set (dq_check, dq_check_run, ingest_run, cwd_project, snapshot_source,
# subagent_pull_run, ingest_source). translate_source_tables() below drops any such name rather
# than passing it through, per ticket comment 3785 (Bob's round-2 real-test finding: cwd_project
# leaked in via v_project_scope's real LEFT JOIN, ingest_source via v_source_freshness).
REAL_SOURCE_TABLES = frozenset({
    "audit_event", "git_commit", "hook_verdict", "session_assistant_text", "session_tool_call",
    "session_transcript", "subagent_transcript", "tessera", "tessera_event",
})

# ARCHITECTURE.md section 8 addendum, the sentence immediately after the dependency-map table:
# "of the 9 tables dq_check names, only 7 carry contract-severity checks -- session_assistant_text
# and session_tool_call are advisory-only and can never appear in v_queryable_source by that
# section's own 'unchecked source is absent, not clean' design; A4's drift test additionally
# asserts no ALLOWED_VIEWS entry ever maps to an advisory-only source, since that would be an
# unpassable gate by construction." A view mapped to either of these two would refuse forever
# regardless of real health -- v_evidence_act's own precondition (ATLASSN-142, session_tool_call
# promotion) depends on exactly this assertion staying enforced once it exists. Checked in
# test_view_dependency_drift.py.post's ViewDependencyDriftTests (this had no test before this
# revision -- a real A4 gap found by re-reading the addendum directly rather than relying on a
# relayed paraphrase, not by execution).
#
# ATLASSN-189 (Iris Chen's real finding, fixed at the SCHEMA level, not here): this static map
# is enforced only in Python, by ViewDependencyDriftTests -- nothing at the SQL level stopped a
# dq_check row from being seeded with severity='contract' against one of these two names anyway.
# Migration 25 adds a real trigger-level guard on dq_check itself; this constant and its own
# Python-level test are UNCHANGED by that fix and still the first line of defense for
# ALLOWED_VIEWS/VIEW_SOURCE_DEPENDENCIES specifically.
ADVISORY_ONLY_SOURCES = frozenset({"session_assistant_text", "session_tool_call"})

# A3 (ARCHITECTURE.md section 8 addendum): the five trust-plumbing views listed in
# VIEW_SOURCE_DEPENDENCIES above ARE the gating machinery itself, not a real data dependency of
# whatever references them -- so compute_transitive_base_tables() below treats a reference to
# any of these five as a closure BOUNDARY, never a name to recurse through.
#
# FIX 2026-09-20, ATLASSN-103, real-test finding (Bob): v_hook_verdict's actual CREATE VIEW
# joins v_project_scope and gates through v_queryable_source rather than reading hook_verdict
# directly. The original closure algorithm recursed into v_queryable_source's own SQL like any
# other view, which pulled in the trust-gate machinery's OWN base tables (cwd_project, dq_check,
# dq_check_run, ingest_run) as if they were real dependencies of every hook_verdict-derived view
# that happens to gate through it -- 12+ of 19 views mismatched against the declared map as a
# result. That is exactly the kind of implementation-detail leakage A3 already exempts these
# five views from at their OWN row in the map ("these five map to the empty set by design, not
# because their real SQL has no tables"). The fix below makes that same exemption apply
# wherever one of the five is encountered mid-closure, not only at the top level: a view that
# merely gates through v_queryable_source is not thereby dependent on v_queryable_source's own
# internals, any more than a caller checking a lock is dependent on the lock implementation's
# own storage engine. This is a correction to the closure algorithm so it actually implements
# A3 as already designed, not a new design decision -- A3 already named these five views as
# machinery rather than data sources; PLUMBING_VIEWS is what makes compute_transitive_base_tables
# honor that consistently at every recursion depth, not just at a view's own row.
PLUMBING_VIEWS = frozenset({
    "v_atlas_status", "v_pipeline_selfcheck", "v_queryable_source",
    "v_snapshot_publishable", "v_source_trust",
})

_COMMENT_RE = re.compile(r"--[^\n]*")


def _strip_sql_comments(sql):
    """A4's own named prerequisite (ARCHITECTURE.md section 8 addendum): the derivation
    method's stated bias is that identifier-tokenizing can over-match a name mentioned only
    inside a SQL comment ('the derivation script still needs a real SQL comment-stripping pass
    before it is fit to seed A4's drift test'). Stripped before any name search below, closing
    that named gap rather than carrying it into this test."""
    return _COMMENT_RE.sub("", sql)


def _names_mentioned(sql, candidate_names):
    """Word-boundary search for each candidate table/view name inside sql. Candidate names
    come from sqlite_master itself (real objects only), never from general tokenization -- a
    column or alias that happens to share a real table's name cannot false-positive here,
    matching the architecture addendum's own stated method (transitive base-table closure by
    searching for real sqlite_master names, not by parsing SQL grammar)."""
    stripped = _strip_sql_comments(sql)
    return {name for name in candidate_names if re.search(rf"\b{re.escape(name)}\b", stripped)}


def compute_transitive_base_tables(connection, object_name, _seen=None):
    """A4: the real transitive base-table closure of one view (or table), recomputed from
    sqlite_master at test time. Recurses through any mentioned name that is ITSELF a view;
    a base table is a leaf and names itself. _seen guards a cyclical view definition looping
    forever -- none exists today, but nothing here assumes that stays true.

    A3 boundary: a name in PLUMBING_VIEWS is also a leaf, but a leaf that contributes NOTHING
    (frozenset(), not {itself}) -- it is the trust-gate machinery, not a data source, whether
    encountered as the top-level object_name or mid-recursion inside another view's SQL. This
    is what keeps a view that merely gates through v_queryable_source (or any of its four
    siblings) from inheriting that machinery's own base tables as if they were real
    dependencies (see PLUMBING_VIEWS's own comment above for the real-test finding that
    required this)."""
    if _seen is None:
        _seen = set()
    if object_name in _seen:
        return frozenset()
    _seen = _seen | {object_name}

    if object_name in PLUMBING_VIEWS:
        return frozenset()

    row = connection.execute(
        "SELECT sql, type FROM sqlite_master WHERE name = ?", (object_name,)
    ).fetchone()
    if row is None or row[0] is None:
        return frozenset()
    sql, obj_type = row
    if obj_type != "view":
        return frozenset({object_name})

    all_objects = {
        r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name != ?",
            (object_name,),
        )
    }
    mentioned = _names_mentioned(sql, all_objects)

    bases = set()
    for name in mentioned:
        bases |= compute_transitive_base_tables(connection, name, _seen)
    return frozenset(bases)


def translate_source_tables(base_tables):
    """A2's translation, applied after the mechanical closure above finds the real physical
    objects a view's SQL names -- the DECLARED map's codomain is dq_check.source_table names,
    which are coarser than the physical schema for exactly the three entries in
    SOURCE_TABLE_TRANSLATION.

    FIX 2026-09-20 (ticket comment 3785, Bob's round-2 real-test finding): translating a name is
    not enough on its own -- a name that survives translation unchanged (`.get(t, t)`) may still
    be a real physical table the mechanical closure legitimately found (cwd_project via
    v_project_scope's real LEFT JOIN, ingest_source via v_source_freshness, and the rest of
    comment 2119's named plumbing-table set) that is nonetheless NOT a valid
    dq_check.source_table value at all. Passing such a name through as if it belonged in the
    map's codomain is the same root-cause mistake PLUMBING_VIEWS above exists to fix for views:
    the trust-gate machinery's own tables are not a real dependency of whatever references them.
    So after translation, anything still outside REAL_SOURCE_TABLES is dropped, not kept."""
    translated = (SOURCE_TABLE_TRANSLATION.get(t, t) for t in base_tables)
    return frozenset(t for t in translated if t in REAL_SOURCE_TABLES)


ALLOWED_VIEWS = frozenset({
    "v_atlas_status", "v_decision_outcome_rate", "v_deny_streak", "v_fail_open_incident",
    "v_gate_proven_live", "v_handler_denominator", "v_hook_latency_rollup", "v_hook_verdict",
    "v_pipeline_selfcheck", "v_project_resolution_coverage", "v_project_scope",
    "v_queryable_source", "v_snapshot_publishable", "v_source_freshness", "v_source_trust",
    "v_ticket_diff_binding", "v_trapped_agent_candidate", "v_verdict_confusion_matrix",
    # ATLASSN-38. hook_verdict-only (batch plane), no live-plane dependency -- first/last-seen
    # per handler_id so a coverage consumer can exclude dead handlers itself, on the same
    # trust gate every other batch view already goes through.
    "v_handler_freshness",
    # ATLASSN-80 / FORE-281 item 1, ARCHITECTURE.md section 28. Gated through fetch()'s batch
    # status() check like every other name in this set -- session_prior's own batch_trust_state
    # column is the finer-grained signal WITHIN a batch-clean window (it and live_trust_state can
    # disagree even when this facade's gate is open), not a replacement for this gate.
    "session_prior",
    # ATLASSN-181, ARCHITECTURE.md section 39.2 (placeholder section number -- see
    # atlas.warehouse.ddl._MIGRATION22_HEADING's own comment; not yet a real ARCHITECTURE.md
    # entry). v_registered_never_proven_recent joins the two already-allowlisted views above
    # (v_gate_proven_live, v_handler_freshness), so it is safe to allowlist here even before
    # migration 22 itself is wired into migrate.connect() -- fetch() will simply refuse it with
    # "no such view" (a real, honest sqlite3 error) until the CREATE VIEW actually runs, never a
    # silent pass-through of a name that resolves to nothing.
    #
    # ATLASSN-98 NOTE (found working that ticket, not fixed here -- not this ticket's own
    # placeholder to correct): "section 39.2" above is now STALE. Section 39 is real as of
    # 2026-09-20 and is ATLASSN-98's v_git_commit_detail (Build 8), not ATLASSN-181's. Whoever
    # lands migration 22 needs a section number that doesn't collide with 39 (taken) or 40
    # (taken, ATLASSN-104) -- flagged here and in ATLASSN-98's own rationale rather than
    # silently corrected, since renumbering someone else's ticket's placeholder isn't this
    # one's call to make.
    "v_registered_never_proven_recent",
    # ATLASSN-98/REQ-77, ARCHITECTURE.md section 39. File-changed-grain view over
    # git_commit LEFT JOIN git_commit_file -- LEFT, not INNER (a toy-modeled correction: 6 real
    # commits have zero git_commit_file rows, which INNER silently drops). author_hash only,
    # never a raw author column and never a session_id join (D8; query/GOALS.json C9). Batch
    # plane only, no live dependency, so this belongs in ALLOWED_VIEWS and not
    # ALLOWED_LIVE_VIEWS -- same gate every other git_commit-derived view already goes through.
    "v_git_commit_detail",
})

# ATLASSN-27. The live plane's views, deliberately a SEPARATE allowlist rather than eighteen
# names becoming twenty. ALLOWED_VIEWS above is unchanged, and fetch() still refuses every one of
# these -- the two sets do not overlap and neither method will serve a name from the other's.
#
# Why a second gate at all, in a system whose section 8 is built around having exactly one: the
# batch gate certifies that 19 data-quality checks passed over hook_verdict, tessera_event,
# audit_event and git_commit. None of them examines a subagent transcript. Gating the live view on
# it would mean an unrelated batch failure blinds a parent session to what its own dispatched
# subagent is doing -- which is not hypothetical, since the warehouse has been non-clean on
# fail_open_not_double_counted while this was written, and every gated view is refused as a
# result. That is the exact blindness ATLASSN-27 was filed about, reproduced inside its own fix.
# The full reasoning, and the alternative that was rejected with its cost, is in
# DECISION-ATLASSN-27.md. It is a provisional decision, not a settled one.
ALLOWED_LIVE_VIEWS = frozenset({
    "v_subagent_tool_call", "v_subagent_activity",
    # ATLASSN-32. The letter-versus-intent candidate set: one row per denied subagent tool call,
    # with the deny's named remedy and the same agent's next turns. It carries no label, on
    # purpose -- see ARCHITECTURE.md section 19.3.
    "v_subagent_deny_join",
    # ATLASSN-33. v_transcript_deny_join is the one to read: the same candidate set over BOTH
    # populations, after main-session ingest took ledger joinability from 33.0% to 99.3%.
    # v_subagent_deny_join stays because dropping a view a consumer may already read is a
    # breaking change, and every migration here is additive by construction.
    "v_transcript_deny_join", "v_ledger_join_coverage",
    # ATLASSN-36. Depends on subagent_tool_call/session_tool_call (live-plane-fed), so it carries
    # the same trust_state sentinel v_transcript_deny_join uses, not ALLOWED_VIEWS's batch gate.
    "v_pip_coverage",
    # ATLASSN-153, ARCHITECTURE.md section 37.4. v_integration_progress reads
    # integration_interface, which has no dependency on subagent/session transcript data at all
    # -- it is gated here only because ALLOWED_VIEWS's batch trust gate would refuse it via the
    # unrelated v_queryable_source-is-empty state (BLOCKER-ATLASSN-143-153-ARCHITECTURE-DDL-
    # 20260912.md's live-verified finding), not because this view has any real live-freshness
    # relationship to v_subagent_live_status. Disclosed, not silently accepted: every entry in
    # this set already shares that one gate regardless of which live source it reads.
    "v_integration_progress",
    # ATLASSN-154, ARCHITECTURE.md section 38.3. Same coupling as v_integration_progress just
    # above, same reason: v_registry_assertion reads registry_assertion, which has no dependency
    # on subagent/session transcript data at all -- gated here only because the batch trust gate
    # is dead (ATLASSN-109), not because of any real live-freshness relationship to
    # v_subagent_live_status.
    "v_registry_assertion",
})

# The one live_state that opens the live gate. Every other value -- never-run, live-pull-failed,
# live-pull-stale, live-credential-hit -- refuses, and the refusal names which, so a caller can
# tell "the pull is dead" from "a secret was found" from "nothing has run yet".
LIVE_STATE_OPEN = "live"


class QueryRefused(RuntimeError):
    pass


def _bound_limit(limit):
    """Returns (sql_suffix, bind_params) for a caller-supplied limit (ATLASSN-48, ported from
    dev-harness DEVH-50 / SECURITY-PRIVACY-REVIEW.md F2).

    limit reaches SQL as a bound PARAMETER, never string-interpolated. A non-integer limit is
    refused rather than coerced, because coercion is how a string that looks numeric ends up
    concatenated later. bool is excluded explicitly -- isinstance(True, int) is True in Python,
    and `limit=True` silently meaning `LIMIT 1` is the kind of surprise this facade should not
    have."""
    if limit is None:
        return "", []
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise QueryRefused(
            f"limit must be an int or None, got {type(limit).__name__} ({limit!r}) -- refused "
            f"rather than coerced, so caller-controlled text can never reach the SQL string"
        )
    if limit < 1:
        raise QueryRefused(f"limit must be >= 1, got {limit}")
    return " LIMIT ?", [limit]


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
                f"run {run_id} is 'ok' but recorded 0 dq_check_run rows -- MAJOR-1's vacuous-pass "
                f"case, not a genuine clean result",
            )
        # ATLASSN-103/A1: a nonzero GLOBAL contract_failures sum no longer forces FAILED_RUN
        # here. Contract failures are scoped per-source now, enforced in fetch() via
        # VIEW_SOURCE_DEPENDENCIES/v_queryable_source (A1/A2) -- this run-level check gates only
        # the three states C4 (corrected) names: NEVER_RUN, a non-'ok' run status, and the
        # vacuous-pass case above. contract_failures is still carried on the returned Status for
        # a caller that wants the raw count directly; it no longer changes `state`.
        return Status(
            Status.CLEAN, run_id, run_status, checks_evaluated, contract_failures,
            f"run {run_id}: {checks_evaluated} checks evaluated" + (
                f", {contract_failures} contract failure(s) present but scoped per-source at "
                f"fetch() rather than a global refusal (ATLASSN-103)"
                if contract_failures else ", 0 contract failures"
            ),
        )

    def live_status(self):
        """The live plane's own gate state (ATLASSN-27). Returns (live_state, detail_row_dict).

        A warehouse migrated to version 1 but not version 2 has no v_subagent_live_status at all;
        that is reported as its own state rather than raising, so a caller on an older warehouse
        gets 'the live plane is not installed here' instead of an OperationalError it has to
        interpret."""
        try:
            cursor = self._conn.execute("SELECT * FROM v_subagent_live_status")
        except sqlite3.OperationalError:
            return "live-plane-not-migrated", {}
        row = cursor.fetchone()
        if row is None:
            return "never-run", {}
        columns = [d[0] for d in cursor.description]
        detail = dict(zip(columns, row))
        return detail.get("live_state") or "never-run", detail

    def fetch_live(self, view_name, limit=None):
        """Read one of the live plane's views under the live plane's OWN freshness gate.

        This does not weaken fetch() and does not share its gate. It also never triggers a pull:
        the connection is mode=ro and stays that way, which is one of the two guarantees section 8
        says this component exists to add. A caller that wants zero staleness runs
        `python3 -m atlas.ingest.subagent_pull` as a separate process and then calls this.

        NOT wrapped in fetch()'s new ATLASSN-190 transaction below -- out of scope for this fix,
        which is scoped to fetch() exactly as Iris's own ticket named it. fetch_live() has its own
        single-gate-then-one-SELECT shape (live_status() then one SELECT), a narrower TOCTOU
        surface than fetch()'s three-part sequence; if a future finding shows it needs the same
        treatment, that is a separate ticket."""
        if view_name not in ALLOWED_LIVE_VIEWS:
            raise QueryRefused(
                f"{view_name!r} is not one of the {len(ALLOWED_LIVE_VIEWS)} declared live views "
                f"-- fetch_live() serves only the live plane; the batch pipeline's views go "
                f"through fetch(), which has its own gate"
            )
        live_state, detail = self.live_status()
        if live_state != LIVE_STATE_OPEN:
            staleness = detail.get("staleness_seconds")
            raise QueryRefused(
                f"refusing to serve {view_name!r}: live plane state is {live_state!r}"
                + (f" (last finished pull {staleness}s ago)" if staleness is not None else "")
                + (f", pull run {detail['pull_run_id']}" if detail.get("pull_run_id") else "")
            )
        sql = f"SELECT * FROM {view_name}"
        limit_sql, limit_params = _bound_limit(limit)
        sql += limit_sql
        cursor = self._conn.execute(sql, limit_params)
        columns = [d[0] for d in cursor.description]
        return columns, cursor.fetchall()

    def _queryable_sources(self):
        """A1/A2: the set of dq_check.source_table names currently queryable, per
        v_queryable_source's own definition (a source with a registered contract check and
        zero contract failures for the current anchor run). An unchecked source is absent from
        this set entirely, matching v_queryable_source's own documented "unchecked is absent,
        not clean" design."""
        return {r[0] for r in self._conn.execute("SELECT source_table FROM v_queryable_source")}

    def _blocked_source_detail(self, source):
        """A2: 'the refusal names the blocking source(s) and their failing check counts, pulled
        from v_source_trust' -- diagnosability is half of what this fix exists to give a
        caller, not a side effect."""
        row = self._conn.execute(
            "SELECT contract_failures, checks_run FROM v_source_trust WHERE source_table = ?",
            (source,),
        ).fetchone()
        if row is None:
            return f"{source} (no contract check registered for this source at all)"
        contract_failures, checks_run = row
        return f"{source} ({contract_failures} of {checks_run} checks failing)"

    def fetch(self, view_name, limit=None):
        if view_name not in ALLOWED_VIEWS:
            raise QueryRefused(
                f"{view_name!r} is not one of the {len(ALLOWED_VIEWS)} declared gated views -- "
                f"this facade never reads a raw table, and never an undeclared name"
            )
        # ATLASSN-190: everything below, through the real SELECT, now runs inside one explicit
        # read transaction -- status(), _queryable_sources() and the SELECT all observe the SAME
        # sqlite snapshot, closing the window a concurrent ingest commit could otherwise land
        # inside (see this module's own top docstring for the real, executed repro). Always
        # closed via finally, on every return path INCLUDING every QueryRefused this function can
        # still raise below -- an open transaction must never be left on the shared connection
        # for the next fetch() call to silently inherit a stale snapshot from.
        self._conn.execute("BEGIN DEFERRED")
        try:
            # A3: v_atlas_status is self-describing and stays servable in every run-level state,
            # including never-run -- refusing it would defeat the one view whose entire job is to
            # report state, and a router needing to learn WHY the facade is refused could never
            # ask.
            if view_name != "v_atlas_status":
                current = self.status()
                if not current.is_clean():
                    raise QueryRefused(
                        f"refusing to serve {view_name!r}: warehouse state is {current.state!r} "
                        f"({current.detail})"
                    )
            # A1/A2/A4: per-source contract-failure scoping. A view with no declared entry
            # refuses fail-closed (A4's own "an unmapped view otherwise has undefined gate
            # behavior the day someone adds one") -- this is a bug in VIEW_SOURCE_DEPENDENCIES,
            # not a reason to silently allow an unmapped name through.
            sources = VIEW_SOURCE_DEPENDENCIES.get(view_name)
            if sources is None:
                raise QueryRefused(
                    f"{view_name!r} has no declared entry in VIEW_SOURCE_DEPENDENCIES -- refused "
                    f"fail-closed per ARCHITECTURE.md section 8 addendum amendment A4"
                )
            if sources:
                blocked = sources - self._queryable_sources()
                if blocked:
                    detail = "; ".join(self._blocked_source_detail(s) for s in sorted(blocked))
                    raise QueryRefused(
                        f"refusing to serve {view_name!r}: source(s) unqueryable -- {detail}"
                    )
            sql = f"SELECT * FROM {view_name}"
            limit_sql, limit_params = _bound_limit(limit)
            sql += limit_sql
            cursor = self._conn.execute(sql, limit_params)
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
        finally:
            self._conn.execute("COMMIT")
        return columns, rows
