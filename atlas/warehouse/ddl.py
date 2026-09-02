"""Extracts the warehouse DDL and seed SQL from ARCHITECTURE.md section 16 at import/test
time, rather than keeping a second, hand-copied version of the same SQL in the implementation.
The two can drift silently otherwise -- exactly the failure this project's F2/F3 failure
signatures name (a claim with no reproduction path; a reference assumed rather than checked).

ARCHITECTURE.md section 16 has exactly two fenced ```sql blocks: the schema (CREATE TABLE /
CREATE VIEW / INSERT INTO snapshot_source) and, after "### Seed data, applied at schema
creation", the dq_check / dq_known_metric_bug seed INSERTs. Located by heading text and fence
order, not by hardcoded line numbers, so an unrelated edit elsewhere in the document does not
silently point this at the wrong block.
"""

import hashlib
import re
from pathlib import Path

ARCHITECTURE_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "atlas-architecture.md"

_SECTION_HEADING = "## 16. Warehouse DDL"
_FENCE_RE = re.compile(r"^```sql\s*$(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
# Matches only a level-2 heading ("## ..."), never section 16's own internal level-3
# subheadings ("### Seed data, applied at schema creation") -- used to find where section 16
# ENDS, so extraction cannot run past its own extent into a later section (docs/GOALS.json C3).
NEXT_LEVEL2_HEADING_RE = re.compile(r"^## ", re.MULTILINE)


class DdlExtractionError(RuntimeError):
    pass


def _read_architecture_text():
    if not ARCHITECTURE_PATH.is_file():
        raise DdlExtractionError(f"{ARCHITECTURE_PATH} does not exist")
    return ARCHITECTURE_PATH.read_text(encoding="utf-8")


def extract_schema_and_seed_sql():
    """Returns (schema_sql, seed_sql), the exact text inside the two ```sql fences following
    section 16's heading, in document order. Raises DdlExtractionError if the heading is
    missing, or fewer than two sql-fenced blocks follow it -- fails loud, never returns a
    partial or empty string silently (F1's fail-open signature).

    Bounded to section 16's own extent: search stops at the next level-2 heading ("## ...")
    after section 16's, or end of file if section 16 is the last section. Without this bound,
    a section-16 edit that drops one of its own two fences while ANY later section has a
    fenced sql block passes the fence-count check (still >= 2 matches) and silently returns
    that later block as this section's schema or seed SQL -- reproduced live (docs/GOALS.json
    C3/F1): with section 16's seed fence removed and a decoy fence added in a later section,
    the prior unbounded version returned the decoy's SQL with no error. Never hardcodes a line
    number for this bound (this module's own stated reason: an unrelated edit elsewhere would
    then silently misdirect extraction, the same failure class in a different disguise) --
    the next level-2 heading is located by scanning, so it moves with the document."""
    text = _read_architecture_text()
    heading_idx = text.find(_SECTION_HEADING)
    if heading_idx == -1:
        raise DdlExtractionError(
            f"heading {_SECTION_HEADING!r} not found in {ARCHITECTURE_PATH}"
        )
    next_heading_match = NEXT_LEVEL2_HEADING_RE.search(text, heading_idx + len(_SECTION_HEADING))
    section_end = next_heading_match.start() if next_heading_match else len(text)
    tail = text[heading_idx:section_end]
    matches = list(_FENCE_RE.finditer(tail))
    if len(matches) < 2:
        raise DdlExtractionError(
            f"expected at least 2 fenced sql blocks after {_SECTION_HEADING!r}, found "
            f"{len(matches)}"
        )
    schema_sql = matches[0].group(1)
    seed_sql = matches[1].group(1)
    if not schema_sql.strip():
        raise DdlExtractionError("extracted schema sql block is empty")
    if not seed_sql.strip():
        raise DdlExtractionError("extracted seed sql block is empty")
    return schema_sql, seed_sql


def ddl_sha256():
    """sha256 of the exact schema SQL text extracted above -- stored in schema_migration so a
    schema that drifted from its recorded migration is mechanically detectable (ARCHITECTURE.md
    section 16's own stated purpose for this column)."""
    schema_sql, _ = extract_schema_and_seed_sql()
    return hashlib.sha256(schema_sql.encode("utf-8")).hexdigest()
