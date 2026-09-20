"""GOALS.json C14 (ARCHITECTURE.md section 8 addendum, amendment A4). Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.query.tests.test_view_dependency_drift -v

A4, verbatim: "The declared map carries a drift test: each view's transitive base-table
closure, recomputed from sqlite_master at test time, must equal the declared map (modulo the
plumbing set and A2's translation) ... every ALLOWED_VIEWS entry must have a map entry, full
stop -- a view with no declared entry refuses fail-closed, and a failing test catches the
omission."

This is a REAL comparison against a REAL migrated warehouse's sqlite_master, not a hand-eyeballed
read (PDP R2) -- the same discipline this project's own architecture pass used to derive the map
in the first place, run again here so a future schema change that silently widens or narrows a
view's real dependencies gets caught by CI rather than by a stale comment.
"""

import unittest

from atlas.query import facade as facade_module
from atlas.query.facade import (
    ADVISORY_ONLY_SOURCES,
    ALLOWED_VIEWS,
    PLUMBING_VIEWS,
    VIEW_SOURCE_DEPENDENCIES,
    QueryFacade,
    QueryRefused,
    compute_transitive_base_tables,
    translate_source_tables,
)
from atlas.query.tests._helpers import TempWarehouse, make_clean_run

# PLUMBING_VIEWS is imported from facade.py itself (single source of truth) rather than
# redeclared here -- it is no longer just a test-time exemption list. FIX 2026-09-20 (Bob's
# real-test finding on the prior draft of this proposal): compute_transitive_base_tables() now
# treats these five as a closure BOUNDARY at every recursion depth, not only when one of them is
# the top-level view under test, which is what makes v_hook_verdict (and every other view that
# merely gates through v_queryable_source) compute correctly. See PLUMBING_VIEWS's own comment
# in facade.py for the full story: v_atlas_status genuinely reads ingest_run/dq_check_run, but
# its declared dependency is deliberately empty so it (and its four plumbing siblings) stay
# servable under a contract failure -- the exemption is A3's own design, not a limitation of
# this test, and the equality check below still excludes these five for exactly that reason.

# ATLASSN-181 (migration 22) and ATLASSN-98 (migration 24) are both mapped in
# VIEW_SOURCE_DEPENDENCIES and both members of ALLOWED_VIEWS (A4 requires an entry regardless of
# migration status), but neither migration is wired into migrate.connect() yet -- see
# atlas/warehouse/migrate.py's own commented-out apply_migration22/apply_migration24 calls. No
# such object exists in sqlite_master for this test's own freshly-migrated warehouse to compute
# a real closure against, so both are excluded from the mechanical equality check below and
# rely on facade.py's own inline comment for their manual reasoning until their migrations land.
UNMIGRATED_VIEWS = frozenset({
    "v_registered_never_proven_recent",  # ATLASSN-181, migration 22
    "v_git_commit_detail",  # ATLASSN-98, migration 24
})


class ViewDependencyDriftTests(unittest.TestCase):
    def setUp(self):
        self.wh = TempWarehouse()

    def tearDown(self):
        self.wh.close()

    def test_every_allowed_view_has_a_declared_map_entry(self):
        """A4: 'every ALLOWED_VIEWS entry must have a map entry, full stop.'"""
        missing = ALLOWED_VIEWS - set(VIEW_SOURCE_DEPENDENCIES)
        self.assertEqual(missing, set(), f"unmapped ALLOWED_VIEWS entries: {sorted(missing)}")

    def test_declared_map_has_no_entry_for_an_undeclared_view(self):
        """The converse -- a stale map entry for a view that was removed from ALLOWED_VIEWS
        would otherwise sit unnoticed forever."""
        extra = set(VIEW_SOURCE_DEPENDENCIES) - ALLOWED_VIEWS
        self.assertEqual(extra, set(), f"map entries with no matching ALLOWED_VIEWS member: "
                                       f"{sorted(extra)}")

    def test_no_declared_entry_maps_to_an_advisory_only_source(self):
        """ADDED 2026-09-20: ARCHITECTURE.md section 8 addendum, the sentence immediately
        following the dependency-map table, verbatim in substance: 'of the 9 tables dq_check
        names, only 7 carry contract-severity checks -- session_assistant_text and
        session_tool_call are advisory-only and can never appear in v_queryable_source by that
        section's own "unchecked source is absent, not clean" design; A4's drift test
        additionally asserts no ALLOWED_VIEWS entry ever maps to an advisory-only source, since
        that would be an unpassable gate by construction.'

        This was a real, previously-unimplemented A4 requirement, not exercised by any prior
        round of this proposal -- found by re-reading the addendum's actual text directly rather
        than trusting a relayed paraphrase of it, not by execution (no ALLOWED_VIEWS member
        currently violates it, which is exactly why its absence had nothing to fail against
        yet). v_evidence_act's own precondition (ATLASSN-142, gated on session_tool_call being
        promoted to contract severity first) depends on this assertion staying enforced -- this
        is the test that keeps it enforced once that view is ever added."""
        offenders = {
            view: sorted(sources & ADVISORY_ONLY_SOURCES)
            for view, sources in VIEW_SOURCE_DEPENDENCIES.items()
            if sources & ADVISORY_ONLY_SOURCES
        }
        self.assertEqual(offenders, {}, f"view(s) mapped to an advisory-only source (an "
                                        f"unpassable gate by construction): {offenders}")

    def test_mechanical_closure_matches_the_declared_map_for_every_real_view(self):
        """The core A4 assertion. UNMIGRATED_VIEWS is skipped here specifically -- neither
        migration (22, 24) is yet applied, so neither object exists in sqlite_master for this
        test's own migrated warehouse to compute a closure against; each one's declared entry is
        asserted present above (test_every_allowed_view_has_a_declared_map_entry) and each one's
        own docstring/comment in facade.py states the manual reasoning for its entry until the
        real view exists to check mechanically."""
        conn = self.wh.setup_conn
        mismatches = {}
        for view in sorted(ALLOWED_VIEWS - PLUMBING_VIEWS - UNMIGRATED_VIEWS):
            declared = VIEW_SOURCE_DEPENDENCIES[view]
            computed_raw = compute_transitive_base_tables(conn, view)
            computed = translate_source_tables(computed_raw)
            if computed != declared:
                mismatches[view] = {"declared": sorted(declared), "computed": sorted(computed)}
        self.assertEqual(mismatches, {}, f"declared map disagrees with the real schema: "
                                         f"{mismatches}")


class UnmappedViewFailsClosedTests(unittest.TestCase):
    """A4's own required ablation, in its own class so the monkeypatch below is scoped to
    exactly one test method's lifetime and cannot leak into ViewDependencyDriftTests or any
    other test in this process.

    SELF-CAUGHT BUG, fixed before this was handed off: the first draft of this test called
    fetch() with a view name that was not in ALLOWED_VIEWS at all, which takes fetch()'s FIRST
    fail-closed branch ('not one of the declared gated views') rather than the SECOND one A4
    actually specifies ('has no declared entry in VIEW_SOURCE_DEPENDENCIES'). That version
    would have passed even if the second branch did not exist, so it exercised the wrong
    invariant, and no execution was needed to see it: fetch()'s own two `if` statements in
    ATLASSN-103-facade.py.post are checked against two clearly different conditions, and the
    original test only ever reached the first. Fixed to monkeypatch a real ALLOWED_VIEWS member
    OUT of a real (restored) copy of the map, so the exact second branch is what actually fires."""

    def test_a_view_present_in_ALLOWED_VIEWS_but_absent_from_the_map_refuses(self):
        target = "v_hook_verdict"
        self.assertIn(target, VIEW_SOURCE_DEPENDENCIES, "precondition: target must start mapped")
        original = dict(facade_module.VIEW_SOURCE_DEPENDENCIES)
        wh = TempWarehouse()
        try:
            make_clean_run(wh.setup_conn)
            facade = QueryFacade(wh.db_path)
            # Sanity check BEFORE sabotage: the real, unmapped-free module serves this view.
            facade.fetch(target)
            del facade_module.VIEW_SOURCE_DEPENDENCIES[target]
            try:
                with self.assertRaises(QueryRefused) as ctx:
                    facade.fetch(target)
                self.assertIn("no declared entry", str(ctx.exception))
            finally:
                facade_module.VIEW_SOURCE_DEPENDENCIES.clear()
                facade_module.VIEW_SOURCE_DEPENDENCIES.update(original)
            # Confirm restoration actually took: the same fetch that just refused must serve
            # again, proving this test's own cleanup is real rather than trusted blindly.
            facade.fetch(target)
            facade.close()
        finally:
            wh.close()


if __name__ == "__main__":
    unittest.main()
