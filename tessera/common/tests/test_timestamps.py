import unittest
from datetime import datetime

from ..timestamps import utc_now_iso


class TimestampTests(unittest.TestCase):
    def test_utc_now_iso_format_and_monotonic(self):
        a = utc_now_iso()
        b = utc_now_iso()
        self.assertTrue(a.endswith("Z"))
        parsed_a = datetime.fromisoformat(a[:-1] + "+00:00")
        parsed_b = datetime.fromisoformat(b[:-1] + "+00:00")
        self.assertLessEqual(parsed_a, parsed_b)


if __name__ == "__main__":
    unittest.main()
