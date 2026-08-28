import unittest

from ..git_sha import git_show_rev_args, validated_commit_sha


class GitShaTests(unittest.TestCase):
    def test_hex_sha_accepted(self):
        sha = "deadbeef"
        self.assertEqual(validated_commit_sha(sha), sha)
        self.assertEqual(git_show_rev_args(sha), [sha])

    def test_leading_dash_rejected(self):
        self.assertIsNone(validated_commit_sha("--output=/tmp/x"))
        self.assertIsNone(git_show_rev_args("--output=/tmp/x"))

    def test_option_like_pretty_rejected(self):
        self.assertIsNone(validated_commit_sha("-pretty=format:x"))

    def test_non_hex_rejected(self):
        self.assertIsNone(validated_commit_sha("HEAD"))
        self.assertIsNone(validated_commit_sha("../foo"))
