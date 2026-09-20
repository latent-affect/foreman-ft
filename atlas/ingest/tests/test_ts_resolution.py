"""GOALS.json C7 -- the permanent regression test for MAJOR-6/MAJOR-7. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_ts_resolution -v
"""

import unittest

from atlas.ingest.verdicts import derive_ts_resolution


class TsResolutionDerivationTests(unittest.TestCase):
    def test_verdict_ledger_writer_is_microsecond_even_with_round_epoch_ms(self):
        # A verdict_ledger.py-produced row is genuinely microsecond-resolution regardless of
        # what epoch_ms happens to be -- the naive "'.' in ts" rule this replaces would still
        # get this one right, but only by accident; this rule gets it right by writer identity.
        self.assertEqual(derive_ts_resolution("architecture_gate.py", 1000000), "microsecond")
        self.assertEqual(derive_ts_resolution("architecture_gate.py", 1000123), "microsecond")

    def test_shell_writer_fake_precision_row_is_second(self):
        # session-log.sh / laa-commit-flow-advisory.sh emit a literal '.000000Z' suffix (so the
        # naive "'.' in ts" rule would call this 'microsecond') over a real second-resolution
        # epoch_ms -- a whole multiple of 1000. This is MAJOR-7's exact reproduction: 15,932
        # real rows mislabeled by the naive rule.
        self.assertEqual(derive_ts_resolution("session-log.sh", 1723000000000), "second")
        self.assertEqual(derive_ts_resolution("laa-commit-flow-advisory.sh", 1723000000000), "second")

    def test_shell_writer_with_non_round_epoch_ms_is_microsecond(self):
        # Before the 2026-08-12T21:35 format change (ARCHITECTURE-REVIEW.md MAJOR-7), the same
        # shell writers emitted genuinely second-resolution epoch_ms that was NOT always a round
        # thousand in every historical row; this rule only claims 'second' for the round case,
        # which is the one it can actually prove.
        self.assertEqual(derive_ts_resolution("session-log.sh", 1723000000123), "microsecond")

    def test_epoch_ms_none_defaults_to_microsecond(self):
        # 0.15% of real rows have no epoch_ms at all (section 1). Absent evidence of fake
        # precision, this must not guess 'second' -- microsecond is the non-shell-writer default.
        self.assertEqual(derive_ts_resolution("session-log.sh", None), "microsecond")


if __name__ == "__main__":
    unittest.main()
