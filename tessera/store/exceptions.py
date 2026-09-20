class StoreError(Exception):
    """Base class for store-layer rejections. All are raised inside a write transaction
    and therefore always accompany a rollback (see store.write_txn_internal())."""


class WorkflowError(StoreError):
    """An illegal status transition was attempted."""


class HierarchyError(StoreError):
    """A ticket-hierarchy rule (e.g. Sub-task requires a parent) was violated."""


class CycleError(StoreError):
    """A blocks/blocked-by link would create a cycle."""


class UnsupportedSQLiteVersion(StoreError):
    """The host's SQLite is older than the UPDATE...RETURNING floor (3.35)."""


class BlockedError(StoreError):
    """A close transition was attempted while the ticket's blocked_by set is not all
    closed. Only gates the close transition -- editing a blocked ticket (comments, fields,
    criteria, any other status) stays allowed, so recording why something's stuck never
    requires unblocking it first."""


class UnknownProjectError(StoreError):
    """A prefix was given that doesn't resolve to a registered project."""


class CriteriaNotFrozenError(StoreError):
    """A ticket was asked to transition to 'in_progress' without frozen criteria --
    the mechanical form of "document before building", applied per ticket. Mirrors
    goals_freeze_gate.py's "no frozen GOALS.json, no implementation" at ticket grain."""


class ClaimRequiredError(StoreError):
    """A close was attempted on a ticket with no ClaimRecorded event and no
    no_claim_reason from schema.NO_CLAIM_REASONS. Before TESS-192 this close succeeded and
    the absence was recorded advisorily, after the fact, by check_and_record_closure() --
    a real event that nothing consumed. The record was never the problem; nothing acting
    on it was. This makes the same absence refuse at the boundary instead, and a close
    that genuinely has no claim behind it still lands the moment the closer names which
    kind of no-claim close it is."""


class NoClaimRateLimitError(StoreError):
    """More than schema.DISPOSITION_PASS_PER_MINUTE 'disposition-pass' closes were
    attempted by one actor inside one rolling minute. Scoped to that one reason on
    purpose: duplicate / wont-fix / superseded-by each assert a specific checkable fact
    about one ticket, and closing twenty genuine duplicates quickly is not the failure
    mode. 'disposition-pass' asserts only that someone looked, which is exactly the claim
    that gets cheaper the faster it is made."""


class SameProjectError(StoreError):
    """reassign_project's target project resolved to the ticket's own current project."""
