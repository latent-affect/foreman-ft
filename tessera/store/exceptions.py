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


class SameProjectError(StoreError):
    """reassign_project's target project resolved to the ticket's own current project."""
