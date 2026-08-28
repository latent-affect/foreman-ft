import hashlib
import unittest
from pathlib import Path

from tessera.tessguard import config


class ShimHashTests(unittest.TestCase):
    def test_expected_shas_match_live_githooks(self):
        root = Path(__file__).resolve().parents[3]
        hooks = root / ".githooks"
        for name, expected in config.EXPECTED_SHIM_SHAS.items():
            path = hooks / name
            self.assertTrue(path.is_file(), f"missing {path}")
            live = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(live, expected, f"{name} hash drifted")
        self.assertIn("commit-msg", config.EXPECTED_SHIM_SHAS)
