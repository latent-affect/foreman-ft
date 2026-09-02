#!/usr/bin/env python3
"""DEVH-41 finding 1: extract_labeled_hash() used re.search(), returning the first match with
no check it was the only one -- feeds review_binds_architecture(), which gates
architecture_gate.py's opt-in REVIEW_BINDING_MARKER check. A stale draft hash left above a
fresh one, or the label text appearing in a quoted passage, would silently bind against the
wrong hash.

    python3 -m unittest test_foreman_evidence -v

(run from /path/to/home/.claude/hooks so the bare `import foreman_evidence` resolves)
"""
import hashlib
import tempfile
import unittest
from pathlib import Path

import foreman_evidence as fe

LABEL = "Reviews-Architecture-SHA256"


class ExtractLabeledHashTests(unittest.TestCase):
    def test_no_match_returns_none_and_zero(self):
        result, count = fe.extract_labeled_hash("nothing here", LABEL)
        self.assertIsNone(result)
        self.assertEqual(count, 0)

    def test_single_match_returns_the_hash_and_one(self):
        digest = "a" * 64
        text = f"**{LABEL}:** sha256:{digest}\n"
        result, count = fe.extract_labeled_hash(text, LABEL)
        self.assertEqual(result, digest)
        self.assertEqual(count, 1)

    def test_two_matches_returns_none_and_two_not_the_first_one(self):
        # The exact regression this finding names: a stale draft hash left above a fresh one.
        stale = "a" * 64
        fresh = "b" * 64
        text = (
            f"**{LABEL}:** sha256:{stale}\n\n"
            f"(superseded, kept for history)\n\n"
            f"**{LABEL}:** sha256:{fresh}\n"
        )
        result, count = fe.extract_labeled_hash(text, LABEL)
        self.assertIsNone(result, "must not silently pick the first (or last) candidate")
        self.assertEqual(count, 2)

    def test_label_appearing_in_a_quoted_passage_is_still_counted(self):
        # The other named risk: the label text quoted verbatim, not meant as a real binding.
        real = "c" * 64
        quoted = "d" * 64
        text = (
            f"**{LABEL}:** sha256:{real}\n\n"
            f'Earlier drafts used the line `**{LABEL}:** sha256:{quoted}` before this format.\n'
        )
        result, count = fe.extract_labeled_hash(text, LABEL)
        self.assertIsNone(result)
        self.assertEqual(count, 2)


class ReviewBindsArchitectureAmbiguousTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)
        self.arch_path = self.tmp / "ARCHITECTURE.md"
        self.review_path = self.tmp / "ARCHITECTURE-REVIEW.md"

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _real_hash(self):
        return hashlib.sha256(self.arch_path.read_bytes()).hexdigest()

    def test_two_labeled_hashes_yields_ambiguous_not_bound_or_mismatch(self):
        self.arch_path.write_text("# Architecture\n\nsome content\n")
        current = self._real_hash()
        self.review_path.write_text(
            f"**{LABEL}:** sha256:{current}\n\n"
            f"**{LABEL}:** sha256:{'0' * 64}\n"
        )
        verdict, reason = fe.review_binds_architecture(self.review_path, self.arch_path, LABEL)
        self.assertEqual(verdict, "ambiguous")
        self.assertIn("2", reason)

    def test_single_matching_hash_still_binds(self):
        # Regression: the existing bound case must be completely unaffected.
        self.arch_path.write_text("# Architecture\n\nsome content\n")
        current = self._real_hash()
        self.review_path.write_text(f"**{LABEL}:** sha256:{current}\n")
        verdict, reason = fe.review_binds_architecture(self.review_path, self.arch_path, LABEL)
        self.assertEqual(verdict, "bound")

    def test_single_stale_hash_still_mismatches(self):
        self.arch_path.write_text("# Architecture\n\nsome content\n")
        self.review_path.write_text(f"**{LABEL}:** sha256:{'0' * 64}\n")
        verdict, reason = fe.review_binds_architecture(self.review_path, self.arch_path, LABEL)
        self.assertEqual(verdict, "mismatch")

    def test_no_hash_still_unbound(self):
        self.arch_path.write_text("# Architecture\n\nsome content\n")
        self.review_path.write_text("no binding line here\n")
        verdict, reason = fe.review_binds_architecture(self.review_path, self.arch_path, LABEL)
        self.assertEqual(verdict, "unbound")


if __name__ == "__main__":
    unittest.main()
