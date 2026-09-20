"""A DELIBERATELY FAIL-OPEN registry read path. This is a test fixture. Never import it from
production code, and never "fix" it -- it is broken on purpose and its brokenness is the assertion.

WHY THIS IS A PERMANENT COMMITTED FIXTURE rather than a one-time act (ATLASSN-130, criterion E5).
A fail-closed battery that has never been shown to reject a fail-open implementation proves
nothing: every probe in it might be passing for reasons unrelated to the property it names. Run
against this mutant on every pass, the battery has to keep demonstrating that it can tell the
difference. A change that lets this mutant pass is a regression, and the suite fails.

THE SPECIFIC LESSON IT ENCODES, from the pre-freeze toy model. A fail-open variant was caught on
the never-registered and error probes while PASSING the expired probe. Expiry-only DENY looks
correct under the obvious probe, because an expired assertion on a HEALTHY store is handled by
ordinary status computation, which the mutant leaves untouched. Absence and error are where
fail-open hides. That is why this mutant delegates faithfully whenever the store opens, and only
lies when the store cannot be read -- it is built to survive the naive probe.
"""

import sqlite3
from pathlib import Path

from atlas.registry import migrate, status
from atlas.registry.availability import DEFAULT_BUSY_BUDGET_MS, open_readonly


def allow_everything(edge_ids, sub_reason):
    """The mutation: a store-level fault serves ALLOW instead of DENY.

    It still fills in a plausible-looking status, because a fail-open implementation that
    announced itself would be caught by inspection rather than by the battery, and the battery
    is what is being tested here.
    """
    return {
        edge_id: status.StatusResult(edge_id, status.ACTIVE, status.ALLOW, sub_reason, None)
        for edge_id in edge_ids
    }


def read_guarded(store_path, edge_ids, now, busy_budget_ms=DEFAULT_BUSY_BUDGET_MS,
                 known_versions=migrate.KNOWN_VERSIONS, connect=None):
    """Same signature as availability.read_guarded. Fails OPEN on every store-level fault."""
    edge_ids = list(edge_ids)
    path = Path(store_path)

    if not path.is_file():
        return allow_everything(edge_ids, "absent")

    opener = connect if connect is not None else open_readonly
    connection = None
    try:
        connection = opener(path, busy_budget_ms)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in known_versions:
            return allow_everything(edge_ids, "schema-version")
        # Faithful delegation whenever the store actually opens. This is the half that makes the
        # mutant survive an expiry-only probe.
        return status.read_edges(connection, edge_ids, now)
    except sqlite3.DatabaseError:
        return allow_everything(edge_ids, "read-error")
    except Exception:
        return allow_everything(edge_ids, "read-error")
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
