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
    partial or empty string silently (F1's fail-open signature)."""
    text = _read_architecture_text()
    heading_idx = text.find(_SECTION_HEADING)
    if heading_idx == -1:
        raise DdlExtractionError(
            f"heading {_SECTION_HEADING!r} not found in {ARCHITECTURE_PATH}"
        )
    tail = text[heading_idx:]
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
