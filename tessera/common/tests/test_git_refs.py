import unittest

from ..git_refs import validate_commit_sha


class ValidateCommitShaTests(unittest.TestCase):
    def test_accepts_full_40_char_sha(self):
        validate_commit_sha("a" * 40)  # must not raise

    def test_accepts_short_7_char_sha(self):
        validate_commit_sha("deadbee")  # must not raise

    def test_rejects_6_char_sha_below_floor(self):
        with self.assertRaises(ValueError):
            validate_commit_sha("dead12")

    def test_rejects_41_char_sha_above_ceiling(self):
        with self.assertRaises(ValueError):
            validate_commit_sha("a" * 41)

    def test_rejects_uppercase_hex(self):
        with self.assertRaises(ValueError):
            validate_commit_sha("DEADBEE")

    def test_rejects_argument_injection_shaped_value(self):
        # TESS-159: the exact live-reproduced payload -- a leading '-' makes git parse
        # this as an option (--output=<path>) rather than a revision.
        with self.assertRaises(ValueError):
            validate_commit_sha("--output=/tmp/pwned")

    def test_rejects_empty_string(self):
        with self.assertRaises(ValueError):
            validate_commit_sha("")

    def test_rejects_none(self):
        with self.assertRaises(ValueError):
            validate_commit_sha(None)

    def test_rejects_branch_name(self):
        # ARCHITECTURE.md:262-267 -- a bare ref/branch name must not resolve as if it
        # were a pinned commit.
        with self.assertRaises(ValueError):
            validate_commit_sha("master")


if __name__ == "__main__":
    unittest.main()
