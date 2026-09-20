"""ATLASSN-47 (ported from dev-harness DEVH-40). extract_schema_and_seed_sql() used to search
from section 16's heading to end of file, so a section-16 edit that dropped one of its own two
fences would silently return a LATER section's SQL as this section's schema or seed -- the
fence-count guard still passes, because later sections supply fences. Now bounded to section
16's own extent.

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_ddl_bounds -v

WHY THIS MATTERS MORE HERE THAN IN DEV-HARNESS: this document carries six later migration-DDL
fences (18.6, 19.4, 20.4, 21.3, 22.4, 23.4), every one a viable decoy. dev-harness's vendored
copy has none, so the same patch is inert there. The side with the fix had nothing to fail open
onto; the side with the decoys had no fix.

NEGATIVE-CONTROL DESIGN. Each bound test builds a synthetic ARCHITECTURE.md with section 16's
seed fence REMOVED and a decoy fence in a later section, then asserts extraction raises. Against
the unbounded implementation these tests fail by returning the decoy's SQL instead of raising --
verified by running this file against the pre-fix code before the bound landed. They do not
merely assert that a bound constant exists.
"""

import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from atlas.warehouse import ddl

DECOY_SQL = "SELECT 'decoy-from-a-later-section' AS this_must_never_be_returned;"

WITH_BOTH_FENCES = """# Doc

## 16. Warehouse DDL

```sql
CREATE TABLE real_schema (a INTEGER);
```

### Seed data, applied at schema creation

```sql
INSERT INTO real_schema (a) VALUES (1);
```

## 17. Something else

## 18. Later section

### 18.6 Migration 2 DDL

```sql
{decoy}
```
"""

SEED_FENCE_REMOVED = """# Doc

## 16. Warehouse DDL

```sql
CREATE TABLE real_schema (a INTEGER);
```

## 17. Something else

## 18. Later section

### 18.6 Migration 2 DDL

```sql
{decoy}
```
"""


class DdlBoundsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.original_path = ddl.ARCHITECTURE_PATH

    def tearDown(self):
        ddl.ARCHITECTURE_PATH = self.original_path
        self.tmp.cleanup()

    def point_at(self, text):
        path = Path(self.tmp.name) / "ARCHITECTURE.md"
        path.write_text(text.format(decoy=DECOY_SQL), encoding="utf-8")
        ddl.ARCHITECTURE_PATH = path
        return path

    def test_fixture_is_a_real_control_decoy_is_reachable_unbounded(self):
        """Proves the fixture would actually fool an unbounded extractor. Without this, the
        raise-assertions below could pass against a document where no decoy was reachable at
        all, and would then prove nothing about the bound."""
        path = self.point_at(SEED_FENCE_REMOVED)
        text = path.read_text(encoding="utf-8")
        idx = text.find("## 16. Warehouse DDL")
        unbounded = list(ddl._FENCE_RE.finditer(text[idx:]))
        self.assertGreaterEqual(
            len(unbounded), 2,
            "fixture does not present >=2 fences to an unbounded scan, so it cannot reproduce "
            "the fail-open this test exists to catch")
        self.assertIn("decoy", unbounded[1].group(1))

    def test_missing_seed_fence_raises_instead_of_returning_a_decoy(self):
        """THE control. Unbounded, this returns the decoy as seed_sql and does not raise."""
        self.point_at(SEED_FENCE_REMOVED)
        with self.assertRaises(ddl.DdlExtractionError):
            ddl.extract_schema_and_seed_sql()

    def test_decoy_is_never_returned_as_schema_or_seed(self):
        """REGRESSION TEST, NOT A NEGATIVE CONTROL -- labelled explicitly because it looks like
        one and is not. With both of section 16's fences present, the first two fences in
        document order are the real ones whether or not the bound exists, so this passes against
        the pre-fix implementation too. Verified, not assumed: the control-prover run for
        ATLASSN-47 found this test green against the unbounded extractor, which is what prompted
        this docstring. It still earns its place by guarding the ordinary case, but
        test_missing_seed_fence_raises_instead_of_returning_a_decoy is the test that actually
        discriminates, and it is the one to keep working if these ever have to be trimmed."""
        self.point_at(WITH_BOTH_FENCES)
        schema_sql, seed_sql = ddl.extract_schema_and_seed_sql()
        self.assertIn("real_schema", schema_sql)
        self.assertIn("INSERT INTO real_schema", seed_sql)
        self.assertNotIn("decoy", schema_sql)
        self.assertNotIn("decoy", seed_sql)

    def test_bound_is_located_by_scanning_not_by_a_line_number(self):
        """Inserting filler before section 16 must not shift what is extracted. A hardcoded
        bound would silently misdirect extraction -- the same failure class in a disguise."""
        self.point_at(WITH_BOTH_FENCES)
        baseline = ddl.extract_schema_and_seed_sql()
        padded = ("Filler paragraph.\n\n" * 40) + WITH_BOTH_FENCES
        self.point_at(padded)
        self.assertEqual(ddl.extract_schema_and_seed_sql(), baseline)

    def test_section_16_as_last_section_still_extracts(self):
        """No level-2 heading follows: the bound must fall back to end-of-file rather than
        raising or truncating."""
        self.point_at("""# Doc

## 16. Warehouse DDL

```sql
CREATE TABLE real_schema (a INTEGER);
```

```sql
INSERT INTO real_schema (a) VALUES (1);
```
""")
        schema_sql, seed_sql = ddl.extract_schema_and_seed_sql()
        self.assertIn("real_schema", schema_sql)
        self.assertIn("INSERT INTO", seed_sql)

    def test_level3_subheading_does_not_end_the_section(self):
        """Section 16's own '### Seed data' subheading sits BETWEEN its two fences. A bound that
        stopped at any heading rather than a level-2 one would cut the seed fence off."""
        self.point_at(WITH_BOTH_FENCES)
        _, seed_sql = ddl.extract_schema_and_seed_sql()
        self.assertIn("INSERT INTO real_schema", seed_sql)

    def test_real_document_migration1_hash_is_unchanged_by_the_bound(self):
        """The live warehouse records migration 1's ddl_sha256. If bounding changed what section
        16 extracts, every migrated database would raise MigrationError on the next connect()."""
        self.assertEqual(
            ddl.ddl_sha256(),
            "25ad406526401d00b7300050a111a396a093bb26f2547c28c3da4f1870a86910")

    def test_real_document_has_decoy_fences_the_bound_must_exclude(self):
        """Documents the live risk this bound is carrying, and fails if the situation changes --
        if these later fences ever disappear, this test should be revisited rather than silently
        continuing to assert a bound that no longer guards anything."""
        text = ddl.ARCHITECTURE_PATH.read_text(encoding="utf-8")
        idx = text.find(ddl._SECTION_HEADING)
        unbounded = len(list(ddl._FENCE_RE.finditer(text[idx:])))
        end = ddl.NEXT_LEVEL2_HEADING_RE.search(text, idx + len(ddl._SECTION_HEADING)).start()
        bounded = len(list(ddl._FENCE_RE.finditer(text[idx:end])))
        self.assertEqual(bounded, 2)
        self.assertGreater(
            unbounded, bounded,
            "no later sql fences remain in ARCHITECTURE.md, so the bound currently guards "
            "nothing -- revisit this test rather than assuming it still proves something")


if __name__ == "__main__":
    unittest.main()
