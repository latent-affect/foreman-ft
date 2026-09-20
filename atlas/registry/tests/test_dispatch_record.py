"""ATLASSN-125 -- dispatch-record consumption. From the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.registry.tests.test_dispatch_record -v

Criteria C1-C3 frozen on the ticket (criteria_hash sha256:213eeb7e).

C3's probes are the ones to read closely. Clause (b) fails in a DIRECTION: skipping a line that
cannot be ruled out moves the computed first-seen LATER, and the clause tests
`created_at < first_seen`, so a later wrong value makes more records incorrectly PASS. Each
probe below therefore asserts the refusal AND, where it matters, that a record which should have
been rejected is not quietly accepted.
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from atlas.registry import dispatch_record

REPO_ROOT = Path(__file__).resolve().parents[3]
CHV2_WRITER = Path("/Users/m5/dev/claude-hooks-v2/hooks/verifier_dispatch_record.py")

AUTHOR = "0384e104-c874-4169-a2e6-76ff70ad60a1"
NAMED = "244e3d16-f16c-4d03-8583-c7fae6e19d9c"
ROLE = "verifier"
PROJECT = "atlas-sonnet"
COMPONENT = "registry"

SESSION_START = "2026-09-12T15:00:00Z"
BEFORE_START = "2026-09-12T14:00:00.123Z"
AFTER_START = "2026-09-12T16:00:00.123Z"


def record(**overrides):
    base = {
        "record_id": "d-01JBXR8Z9QK2M4N6P8R0T2V4W7",
        "author_session_id": AUTHOR,
        "named_session_id": NAMED,
        "role": ROLE,
        "project": PROJECT,
        "repo_scope": [COMPONENT, "warehouse"],
        "created_at": BEFORE_START,
    }
    base.update(overrides)
    return base


class DispatchRecordTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.stream = self.write_stream([
            {"session_id": "someone-else", "ts": "2026-09-12T09:00:00Z", "event": "SessionStart"},
            {"session_id": NAMED, "ts": SESSION_START, "event": "SessionStart"},
            {"session_id": NAMED, "ts": "2026-09-12T18:00:00Z", "event": "SessionEnd"},
        ])

    def write_stream(self, entries, name="sessions.jsonl"):
        path = self.root / name
        with open(path, "w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write((entry if isinstance(entry, str) else json.dumps(entry)) + "\n")
        return path

    def validate(self, rec=None, repo=PROJECT, component=COMPONENT, role=ROLE, stream=None):
        return dispatch_record.validate_dispatch_record(
            rec if rec is not None else record(), repo, component, role,
            sessions_stream_path=stream or self.stream)


class TestWriterLivesElsewhere(DispatchRecordTestCase):
    """C1 -- creation belongs to the dispatching authority, never to this component."""

    def test_no_writer_function_exists_in_atlas_registry(self):
        # Mechanical, not a reading: any module-level function under atlas/registry whose name
        # suggests it mints a dispatch record would collapse producer and consumer into one
        # freeze domain.
        offenders = []
        for path in sorted((REPO_ROOT / "atlas" / "registry").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and "dispatch_record" in node.name:
                    if any(verb in node.name for verb in ("write", "create", "mint", "new")):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.name}")
        self.assertEqual(offenders, [])

    def test_the_real_writer_exists_in_claude_hooks_v2(self):
        # C1's verification names it. If it is gone, this criterion is unmet whatever this
        # module does.
        self.assertTrue(CHV2_WRITER.is_file(), f"{CHV2_WRITER} is missing")

    def test_this_module_and_the_writer_agree_on_the_record_shape(self):
        # The cross-repo agreement no test on either side catches alone. Read the writer's own
        # source and compare the keys it emits against the keys this module requires.
        tree = ast.parse(CHV2_WRITER.read_text(encoding="utf-8"), filename=str(CHV2_WRITER))
        emitted = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = {k.value for k in node.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if "record_id" in keys:
                    emitted |= keys
        self.assertTrue(emitted, "could not find the writer's record literal")
        self.assertEqual(set(dispatch_record.REQUIRED_KEYS) - emitted, set(),
                         "this module requires a key the writer never emits")


class TestReadDispatchRecord(DispatchRecordTestCase):
    """C2 -- absence and corruption are different facts."""

    def test_a_missing_record_returns_none(self):
        self.assertIsNone(dispatch_record.read_dispatch_record("d-nope", records_dir=self.root))

    def test_a_valid_record_round_trips(self):
        (self.root / "d-1.json").write_text(json.dumps(record()), encoding="utf-8")
        self.assertEqual(
            dispatch_record.read_dispatch_record("d-1", records_dir=self.root)["role"], ROLE)

    def test_invalid_json_raises_rather_than_reading_as_absent(self):
        (self.root / "d-2.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            dispatch_record.read_dispatch_record("d-2", records_dir=self.root)

    def test_a_record_missing_a_required_key_raises(self):
        broken = record()
        del broken["named_session_id"]
        (self.root / "d-3.json").write_text(json.dumps(broken), encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            dispatch_record.read_dispatch_record("d-3", records_dir=self.root)
        self.assertIn("named_session_id", str(caught.exception))

    def test_a_json_array_is_not_a_record(self):
        (self.root / "d-4.json").write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(ValueError):
            dispatch_record.read_dispatch_record("d-4", records_dir=self.root)


class TestTheFourClauses(DispatchRecordTestCase):
    """C2 -- each clause's failure mode discretely, plus the positive round trip."""

    def test_a_valid_record_passes_every_clause(self):
        result = self.validate()
        self.assertTrue(result.ok, f"expected a pass, got {result!r}")
        self.assertIsNone(result.reason)

    def test_clause_a_rejects_a_self_authored_record(self):
        result = self.validate(record(author_session_id=NAMED))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "self-authored-dispatch-record")

    def test_clause_b_rejects_a_record_written_after_the_session_started(self):
        result = self.validate(record(created_at=AFTER_START))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "dispatch-record-postdates-session-start")

    def test_clause_b_rejects_a_record_written_in_the_same_instant(self):
        # Strictly precedes. Same-instant is not evidence of "before".
        result = self.validate(record(created_at=SESSION_START))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "dispatch-record-postdates-session-start")

    def test_clause_b_rejects_a_session_absent_from_the_stream(self):
        result = self.validate(record(named_session_id="never-existed"))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "named-session-absent-from-stream")
        self.assertFalse(result.retryable, "an absent session is not a transient failure")

    def test_clause_c_rejects_a_role_mismatch(self):
        result = self.validate(role="architect")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "role-mismatch")

    def test_clause_d_rejects_a_different_project(self):
        result = self.validate(repo="some-other-repo")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "scope-mismatch:project")

    def test_clause_d_rejects_a_component_outside_the_scope(self):
        result = self.validate(component="dashboard")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "scope-mismatch:component")

    def test_clause_d_is_exact_not_prefix_on_the_project(self):
        # G5's own example: a record scoped to "atlas" must not authorize an edge in
        # "atlas-sonnet". A substring comparison would accept this.
        result = self.validate(record(project="atlas"), repo="atlas-sonnet")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "scope-mismatch:project")

    def test_clause_d_is_exact_not_prefix_on_the_component(self):
        result = self.validate(record(repo_scope=["regis"]), component="registry")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "scope-mismatch:component")

    def test_clause_d_rejects_a_malformed_repo_scope(self):
        result = self.validate(record(repo_scope="registry"))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "scope-mismatch:component")

    def test_first_failure_wins(self):
        # A record failing (a) and (c) reports (a): the clauses are ordered and the first
        # failure is the one a caller should act on.
        result = self.validate(record(author_session_id=NAMED), role="architect")
        self.assertEqual(result.reason, "self-authored-dispatch-record")

    def test_a_missing_key_is_reported_before_any_clause_runs(self):
        incomplete = record()
        del incomplete["role"]
        result = self.validate(incomplete)
        self.assertFalse(result.ok)
        self.assertIn("malformed-dispatch-record", result.reason)


class TestClauseBFailsClosed(DispatchRecordTestCase):
    """C3 -- the partition, and the direction of its failure.

    Every probe here asserts the record is NOT accepted. Asserting only "a refusal happened"
    would miss the defect entirely, because the defect's signature is an incorrect PASS.
    """

    def test_an_unparseable_line_forces_unreachable(self):
        # It might have named this session with an earlier ts nobody can read any more.
        stream = self.write_stream([
            "{this line is not json",
            {"session_id": NAMED, "ts": SESSION_START},
        ], name="corrupt.jsonl")
        result = self.validate(stream=stream)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "sessions-stream-unreachable")
        self.assertTrue(result.retryable)

    def test_a_non_object_line_forces_unreachable(self):
        stream = self.write_stream(["[1,2,3]", {"session_id": NAMED, "ts": SESSION_START}],
                                   name="array.jsonl")
        self.assertEqual(self.validate(stream=stream).reason, "sessions-stream-unreachable")

    def test_this_sessions_own_line_with_an_unparseable_ts_forces_unreachable(self):
        stream = self.write_stream([
            {"session_id": NAMED, "ts": "not-a-timestamp"},
            {"session_id": NAMED, "ts": SESSION_START},
        ], name="badts.jsonl")
        result = self.validate(stream=stream)
        self.assertEqual(result.reason, "sessions-stream-unreachable")
        self.assertTrue(result.retryable)

    def test_this_sessions_own_line_with_a_non_string_ts_forces_unreachable(self):
        stream = self.write_stream([
            {"session_id": NAMED, "ts": 1789212206},
            {"session_id": NAMED, "ts": SESSION_START},
        ], name="numts.jsonl")
        self.assertEqual(self.validate(stream=stream).reason, "sessions-stream-unreachable")

    def test_a_different_sessions_malformed_ts_is_safely_skipped(self):
        # The only genuinely harmless skip: positively ruled out as a competing earlier
        # appearance by the same check that identifies it. Without this case the fix would make
        # any unrelated corruption in a shared stream permanently disable the clause.
        stream = self.write_stream([
            {"session_id": "someone-else", "ts": "garbage"},
            {"session_id": NAMED, "ts": SESSION_START},
        ], name="otherbad.jsonl")
        result = self.validate(stream=stream)
        self.assertTrue(result.ok, f"expected the unrelated bad line to be skipped: {result!r}")

    def test_the_adversarial_shape_does_not_produce_an_incorrect_pass(self):
        # THE PROBE THIS CRITERION EXISTS FOR. The record post-dates the session's TRUE start
        # (09:00), so it must be rejected. An attacker corrupts the line carrying that true
        # start, leaving only a later one (17:00). Under skip-and-continue, first_seen resolves
        # to 17:00, the record's 15:00 now "precedes" it, and a record that should fail clause
        # (b) PASSES. Fail-closed turns that into an unreachable refusal instead.
        stream = self.write_stream([
            "{corrupted: this was the 09:00 SessionStart",
            {"session_id": NAMED, "ts": "2026-09-12T17:00:00Z", "event": "SessionEnd"},
        ], name="adversarial.jsonl")
        result = self.validate(record(created_at="2026-09-12T15:00:00.000Z"), stream=stream)
        self.assertFalse(result.ok, "a corrupted line produced an incorrect PASS")
        self.assertEqual(result.reason, "sessions-stream-unreachable")

    def test_an_unreadable_stream_is_retryable(self):
        result = self.validate(stream=self.root / "no-such-stream.jsonl")
        self.assertEqual(result.reason, "sessions-stream-unreachable")
        self.assertTrue(result.retryable)

    def test_first_seen_takes_the_earliest_by_content_not_by_file_order(self):
        """SessionStart/SessionEnd can land out of order; the true first appearance is whichever
        is earliest by content timestamp.

        The THIRD line is load-bearing and was added after mutation testing. With only
        [18:00, 15:00] the earliest line is also the last one processed, so an implementation
        that simply kept the last match returned the right answer and this test passed while
        proving nothing. A later line after the earliest one makes last-wins visibly wrong.
        """
        stream = self.write_stream([
            {"session_id": NAMED, "ts": "2026-09-12T18:00:00Z", "event": "SessionEnd"},
            {"session_id": NAMED, "ts": SESSION_START, "event": "SessionStart"},
            {"session_id": NAMED, "ts": "2026-09-12T20:00:00Z", "event": "SessionEnd"},
        ], name="unordered.jsonl")
        found, seen_at = dispatch_record.first_seen(NAMED, stream)
        self.assertEqual(found, "found")
        self.assertEqual(seen_at, dispatch_record.parse_iso(SESSION_START))


class TestTimestampParsing(DispatchRecordTestCase):
    """Both real formats, checked against what the two producers actually emit."""

    def test_the_streams_second_precision_form(self):
        self.assertIsNotNone(dispatch_record.parse_iso("2026-08-10T01:51:37Z"))

    def test_the_writers_millisecond_form(self):
        self.assertIsNotNone(dispatch_record.parse_iso("2026-09-12T15:00:00.123Z"))

    def test_a_malformed_timestamp_raises_rather_than_sorting_as_epoch_zero(self):
        with self.assertRaises(ValueError):
            dispatch_record.parse_iso("yesterday")


if __name__ == "__main__":
    unittest.main()
