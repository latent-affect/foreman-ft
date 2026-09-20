"""C8: D3 recency computation. ATLASSN-120's three-probe battery plus the import-identity and
config-refusal probes GOALS.json's own verification text names.

THE PROBE THAT MATTERS is test_active_by_lease_but_outside_window_is_fatal. Everything else
here would pass against an implementation that checked only liveness and never looked at the
window -- including a "stale assertion" probe whose fixture is expired by its own lease, which
is what the first draft of this suite had. Recency and liveness are different questions and
only one probe distinguishes them.
"""
import datetime
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from atlas.registry import architecture_parse, recency
from atlas.registry.tests import write_path_fixtures as fx

GIT_IDENTITY = ["-c", "user.name=atlas-test", "-c", "user.email=atlas-test@invalid"]

REACH_WITH_REGISTERS = (
    '```yaml reach\n'
    'my_edge: {"repo": "x", "path": "y/", "direction": "registers", "pinned_at": "not-yet-created"}\n'
    '```\n'
)
REACH_NO_REGISTERS = (
    '```yaml reach\n'
    'read_only_edge: {"repo": "x", "path": "y/", "direction": "reads", "pinned_at": "sha256:aaa"}\n'
    '```\n'
)
NO_REACH_AT_ALL = "# no reach block in this document at all\n"
MALFORMED_REACH = '```yaml reach\nmy_edge: not-valid-json\n```\n'

EDGE_ID = "x:y/:registers:my_edge"


def git(repo, *arguments, identity=False):
    command = ["git", "-C", str(repo)] + (GIT_IDENTITY if identity else []) + list(arguments)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"fixture git failed: {' '.join(command)}\n{result.stderr}")
    return result.stdout


def build_repo(directory, architecture_text):
    repo = Path(directory)
    git(repo, "init", "--quiet")
    (repo / "ARCHITECTURE.md").write_text(architecture_text, encoding="utf-8")
    marker = repo / ".foreman"
    marker.mkdir(exist_ok=True)
    (marker / "frozen.json").write_text(
        '{"edges": [], "frozen_at": "2026-01-01T00:00:00Z"}', encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "fixture", identity=True)
    return repo


class ImportIdentityTests(unittest.TestCase):
    def test_recency_uses_the_same_architecture_parse_module_as_the_reconciler(self):
        from atlas.registry import reconcile
        self.assertIs(recency.architecture_parse, architecture_parse)
        self.assertIs(recency.architecture_parse, reconcile.architecture_parse)


class RequiredByScopeTests(unittest.TestCase):
    def test_registers_edge_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_WITH_REGISTERS)
            self.assertEqual(recency.required_by_scope(repo), {EDGE_ID})

    def test_non_registers_edge_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_NO_REGISTERS)
            self.assertEqual(recency.required_by_scope(repo), set())

    def test_no_reach_block_at_all_is_genuinely_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, NO_REACH_AT_ALL)
            self.assertEqual(recency.required_by_scope(repo), set())

    def test_malformed_reach_block_raises_not_silently_empty(self):
        """F3: the whole point. A reach block that exists but will not parse must NOT come
        back as the same empty set a dependency-free document produces."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, MALFORMED_REACH)
            with self.assertRaises(architecture_parse.BlockMalformed):
                recency.required_by_scope(repo)


class RecencyHarness(unittest.TestCase):
    NOW = datetime.datetime(2026, 9, 12, 12, 0, 0, tzinfo=datetime.timezone.utc)

    def seed(self, store_path, age_s, lease_s):
        """One assertion on EDGE_ID, verified `age_s` seconds before NOW."""
        fx.create_store(store_path)
        verified_at = (self.NOW - datetime.timedelta(seconds=age_s)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        connection = sqlite3.connect(str(store_path))
        connection.execute(
            "INSERT INTO assertion (assertion_uid, edge_id, component, class, lease_s, "
            "verified_at, verifier_session_id, dispatch_record_id, evidence_tool_use_id, "
            "evidence_class) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("a-seed0000000000000000000000", EDGE_ID, "somecomp", "structural", lease_s,
             verified_at, "s-verifier", "d-seed", "toolu_seed", "observed-probe"))
        connection.commit()
        connection.close()


class CheckRecencyTests(RecencyHarness):
    def test_active_and_recent_is_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_WITH_REGISTERS)
            store = Path(tmp) / "registry.db"
            self.seed(store, age_s=60, lease_s=86401)
            config_path = fx.write_config_and_decision(Path(tmp), window_s=1801)
            self.assertEqual(
                recency.check_recency(repo, store, config_path, now=self.NOW), ("go", None))

    def test_active_by_lease_but_outside_window_is_fatal(self):
        """THE DISCRIMINATING PROBE. verified 3600s ago under an 86401s lease, so the
        assertion is genuinely ACTIVE -- and the window is 1801s, so it is stale for THIS
        decision. An implementation that checks only status == ACTIVE returns go here, which
        is precisely the defect this probe exists to catch and precisely what the first
        implementation did."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_WITH_REGISTERS)
            store = Path(tmp) / "registry.db"
            self.seed(store, age_s=3600, lease_s=86401)
            config_path = fx.write_config_and_decision(Path(tmp), window_s=1801)

            from atlas.registry import availability
            live = availability.read_one(store, EDGE_ID, self.NOW)
            self.assertEqual(live.status, "active",
                             "fixture must be ACTIVE, or this probe is just re-testing expiry")

            outcome, reason = recency.check_recency(repo, store, config_path, now=self.NOW)
            self.assertEqual(outcome, "fatal")
            self.assertTrue(reason.startswith(recency.REASON_STALE), reason)
            self.assertIn(EDGE_ID, reason)

    def test_expired_by_lease_is_fatal_and_says_so(self):
        """Distinct from the window case, and its reason must say which one happened."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_WITH_REGISTERS)
            store = Path(tmp) / "registry.db"
            self.seed(store, age_s=7200, lease_s=3600)
            config_path = fx.write_config_and_decision(Path(tmp), window_s=1801)
            outcome, reason = recency.check_recency(repo, store, config_path, now=self.NOW)
            self.assertEqual(outcome, "fatal")
            self.assertTrue(reason.startswith(recency.REASON_NOT_ACTIVE), reason)
            self.assertIn("expired", reason)

    def test_never_registered_edge_is_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_WITH_REGISTERS)
            store = Path(tmp) / "registry.db"
            fx.create_store(store)
            config_path = fx.write_config_and_decision(Path(tmp), window_s=1801)
            outcome, reason = recency.check_recency(repo, store, config_path, now=self.NOW)
            self.assertEqual(outcome, "fatal")
            self.assertIn("never-registered", reason)

    def test_computed_empty_with_declared_deps_is_fatal_not_silent_pass(self):
        """F3 at the check_recency level: a malformed reach block must FATAL, never compute
        empty and pass as dependency-free."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, MALFORMED_REACH)
            outcome, reason = recency.check_recency(repo, Path(tmp) / "registry.db")
            self.assertEqual(outcome, "fatal")
            self.assertTrue(reason.startswith(recency.REASON_SCOPE_MALFORMED), reason)

    def test_genuinely_dependency_free_scope_passes_without_touching_config(self):
        """The other half of F3: a document that really declares nothing must PASS, and must
        do so without consulting config -- proven by pointing config at a file that does not
        exist, which would raise if it were read."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, NO_REACH_AT_ALL)
            self.assertEqual(
                recency.check_recency(repo, Path(tmp) / "registry.db",
                                      config_path=Path(tmp) / "no-such-config.json"),
                ("go", None))


class ClockNormalisationTests(RecencyHarness):
    def test_epoch_float_now_is_accepted_not_reported_as_a_store_outage(self):
        """availability.read_guarded turns a caller's type error into registry-unavailable,
        which misreports a programming mistake as a store outage -- and that is exactly how
        the first version of this suite got a green "stale" probe for the wrong reason.
        Normalising here means this module cannot produce that lie."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_WITH_REGISTERS)
            store = Path(tmp) / "registry.db"
            self.seed(store, age_s=60, lease_s=86401)
            config_path = fx.write_config_and_decision(Path(tmp), window_s=1801)
            self.assertEqual(
                recency.check_recency(repo, store, config_path, now=self.NOW.timestamp()),
                ("go", None))

    def test_naive_datetime_is_treated_as_utc(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_WITH_REGISTERS)
            store = Path(tmp) / "registry.db"
            self.seed(store, age_s=60, lease_s=86401)
            config_path = fx.write_config_and_decision(Path(tmp), window_s=1801)
            naive = self.NOW.replace(tzinfo=None)
            self.assertEqual(
                recency.check_recency(repo, store, config_path, now=naive), ("go", None))


class ConfigRefusalTests(RecencyHarness):
    def test_config_absent_raises_when_window_is_needed(self):
        from atlas.registry import config as config_module
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_WITH_REGISTERS)
            store = Path(tmp) / "registry.db"
            self.seed(store, age_s=60, lease_s=86401)
            with self.assertRaises(config_module.ConfigUnsigned):
                recency.check_recency(repo, store,
                                      config_path=Path(tmp) / "absent-config.json",
                                      now=self.NOW)

    def test_hash_mismatched_decision_record_raises(self):
        from atlas.registry import config as config_module
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_WITH_REGISTERS)
            store = Path(tmp) / "registry.db"
            self.seed(store, age_s=60, lease_s=86401)
            config_path = fx.write_config_and_decision(Path(tmp))
            (Path(tmp) / "decision-record.md").write_text("tampered", encoding="utf-8")
            with self.assertRaises(config_module.ConfigUnsigned):
                recency.check_recency(repo, store, config_path=config_path, now=self.NOW)


if __name__ == "__main__":
    unittest.main()
