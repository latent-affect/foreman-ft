"""docs/GOALS.json C3 (bounded to section 16's own extent, DEVH-28).

    python3 -m unittest atlas.warehouse.tests.test_ddl_bounds -v
"""
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from atlas.warehouse import ddl

REAL_TEXT = ddl.ARCHITECTURE_PATH.read_text(encoding="utf-8")

_FENCE_RE = re.compile(r"^```sql\s*$.*?^```\s*$", re.MULTILINE | re.DOTALL)


def _section_16_span(text):
    start = text.find(ddl._SECTION_HEADING)
    assert start != -1, "fixture must contain the section 16 heading"
    next_heading = ddl.NEXT_LEVEL2_HEADING_RE.search(text, start + len(ddl._SECTION_HEADING))
    end = next_heading.start() if next_heading else len(text)
    return start, end


class DdlBoundsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.real_schema_sql, self.real_seed_sql = ddl.extract_schema_and_seed_sql()
        self.real_hash = ddl.ddl_sha256()

    def _write_fixture(self, text):
        path = Path(self.tmpdir.name) / "atlas-architecture.md"
        path.write_text(text, encoding="utf-8")
        return path

    # ------------------------------------------------------------- case A (regression guard)

    def test_case_a_appending_a_fence_after_section_17_does_not_change_extraction(self):
        appended = REAL_TEXT + "\n\n## A new section after 17\n\n```sql\nSELECT 1 AS decoy;\n```\n"
        fixture = self._write_fixture(appended)
        with mock.patch.object(ddl, "ARCHITECTURE_PATH", fixture):
            schema_sql, seed_sql = ddl.extract_schema_and_seed_sql()
            digest = ddl.ddl_sha256()
        self.assertEqual(schema_sql, self.real_schema_sql)
        self.assertEqual(seed_sql, self.real_seed_sql)
        self.assertEqual(digest, self.real_hash)

    # ------------------------------------------------------------- case B (the actual defect)

    def test_case_b_losing_section_16s_seed_fence_while_a_later_fence_exists_raises(self):
        start, end = _section_16_span(REAL_TEXT)
        section_16_text = REAL_TEXT[start:end]
        fences_in_16 = list(_FENCE_RE.finditer(section_16_text))
        self.assertEqual(
            len(fences_in_16), 2,
            "fixture assumption broken: section 16 no longer has exactly two sql fences",
        )
        second_fence = fences_in_16[1]
        # Remove section 16's SECOND fence (the seed block) entirely, leaving the first
        # (schema) fence untouched -- section 16 now has only one fence of its own.
        section_16_without_seed_fence = (
            section_16_text[: second_fence.start()] + section_16_text[second_fence.end():]
        )
        mutated = (
            REAL_TEXT[:start] + section_16_without_seed_fence + REAL_TEXT[end:]
            + "\n\n## A later section with a decoy fence\n\n```sql\nSELECT 1 AS decoy;\n```\n"
        )
        fixture = self._write_fixture(mutated)
        with mock.patch.object(ddl, "ARCHITECTURE_PATH", fixture):
            with self.assertRaises(ddl.DdlExtractionError):
                ddl.extract_schema_and_seed_sql()

    def test_case_b_control_the_decoy_text_would_have_been_returned_by_the_unbounded_version(self):
        """Not a test of ddl.py -- confirms this fixture actually reproduces the reported bug
        shape (the decoy fence really is the next ```sql block after section 16's remaining
        fence), so case B's pass is evidence of the bound working, not of a fixture that
        never exercised the bug in the first place."""
        start, end = _section_16_span(REAL_TEXT)
        section_16_text = REAL_TEXT[start:end]
        fences_in_16 = list(_FENCE_RE.finditer(section_16_text))
        second_fence = fences_in_16[1]
        section_16_without_seed_fence = (
            section_16_text[: second_fence.start()] + section_16_text[second_fence.end():]
        )
        mutated = (
            REAL_TEXT[:start] + section_16_without_seed_fence + REAL_TEXT[end:]
            + "\n\n## A later section with a decoy fence\n\n```sql\nSELECT 1 AS decoy;\n```\n"
        )
        heading_idx = mutated.find(ddl._SECTION_HEADING)
        unbounded_tail = mutated[heading_idx:]
        unbounded_matches = list(ddl._FENCE_RE.finditer(unbounded_tail))
        self.assertEqual(len(unbounded_matches), 2, "the unbounded scan must still find 2 fences")
        self.assertIn("SELECT 1 AS decoy;", unbounded_matches[1].group(1))


if __name__ == "__main__":
    unittest.main()
