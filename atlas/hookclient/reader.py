"""Zero-dependency read-side library for the published ATLAS snapshot. ARCHITECTURE.md
section 7's consumer contract: resolve current, read index, validate schema_version, validate
age, longest path-component match on roots, read a project shard under the SAME resolved
generation. Every failure path returns a reason and no data; none raise out of the public
functions (section 4's stated guarantee, extended here to a schema_version mismatch too)."""

import datetime
import json
import os

from . import matching

EXPECTED_SCHEMA_VERSION = "atlas-snapshot-2"


class IndexResult:
    __slots__ = ("ok", "reason", "index", "generation_dir", "age_seconds", "is_stale")

    def __init__(self, ok, reason=None, index=None, generation_dir=None, age_seconds=None, is_stale=None):
        self.ok = ok
        self.reason = reason
        self.index = index
        self.generation_dir = generation_dir
        self.age_seconds = age_seconds
        self.is_stale = is_stale


class ProjectResult:
    __slots__ = ("ok", "reason", "shard")

    def __init__(self, ok, reason=None, shard=None):
        self.ok = ok
        self.reason = reason
        self.shard = shard


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_generated_at(s):
    # generated_at is "%Y-%m-%dT%H:%M:%S.%fZ" throughout this project's own snapshot examples.
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=datetime.timezone.utc)


def read_index(snapshot_root, now=None):
    """Never raises. Returns an IndexResult; ok=False means reason names one of: root-removed,
    pointer-missing, pointer-dangling, index-corrupt, schema-version-mismatch. ok=True always
    carries index data, is_stale reports (does not enforce) freshness against max_age_seconds."""
    try:
        if not os.path.isdir(snapshot_root):
            return IndexResult(False, reason="root-removed")

        current_link = os.path.join(snapshot_root, "current")
        if not os.path.islink(current_link) and not os.path.exists(current_link):
            return IndexResult(False, reason="pointer-missing")

        try:
            resolved = os.path.realpath(current_link)
        except OSError:
            return IndexResult(False, reason="pointer-missing")

        if not os.path.isdir(resolved):
            return IndexResult(False, reason="pointer-dangling")

        index_path = os.path.join(resolved, "index.json")
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            return IndexResult(False, reason="index-corrupt")
        try:
            index = json.loads(raw)
        except json.JSONDecodeError:
            return IndexResult(False, reason="index-corrupt")

        if not isinstance(index, dict) or index.get("schema_version") != EXPECTED_SCHEMA_VERSION:
            return IndexResult(False, reason="schema-version-mismatch")

        clock_now = now if now is not None else _now()
        try:
            generated_at = _parse_generated_at(index["generated_at"])
            max_age_seconds = index["max_age_seconds"]
            age_seconds = (clock_now - generated_at).total_seconds()
            is_stale = age_seconds > max_age_seconds
        except (KeyError, ValueError, TypeError):
            return IndexResult(False, reason="index-corrupt")

        return IndexResult(
            True, index=index, generation_dir=resolved, age_seconds=age_seconds, is_stale=is_stale
        )
    except Exception:  # noqa: BLE001 -- F1: never raise out of the public read path, even for
        # a failure mode not in the original five; a catch-all here still returns a NAMED
        # reason rather than letting an unexpected exception propagate to the caller (a hook).
        return IndexResult(False, reason="unexpected-error")


def resolve_project(cwd, index_result):
    """Section 7's contract: longest path-component match against index['roots']. Returns
    (resolution, candidates) via matching.resolve_cwd_against_roots -- unique/ambiguous never
    picks a single winner out of a tie, unregistered means proceed as if ATLAS is absent."""
    roots = (index_result.index or {}).get("roots", {})
    return matching.resolve_cwd_against_roots(cwd, roots)


def read_project(prefix, index_result):
    """Reads projects/<prefix>.json from the SAME resolved generation index_result carries --
    never re-resolves current. Never raises."""
    if not index_result.ok or not index_result.generation_dir:
        return ProjectResult(False, reason="no-valid-index")
    try:
        shard_path = os.path.join(index_result.generation_dir, "projects", f"{prefix}.json")
        try:
            with open(shard_path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            return ProjectResult(False, reason="shard-corrupt")
        try:
            shard = json.loads(raw)
        except json.JSONDecodeError:
            return ProjectResult(False, reason="shard-corrupt")
        return ProjectResult(True, shard=shard)
    except Exception:  # noqa: BLE001 -- same F1 rationale as read_index.
        return ProjectResult(False, reason="unexpected-error")
