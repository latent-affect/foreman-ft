import fcntl
import os
import threading
from contextlib import contextmanager
from pathlib import Path

LOCK_INTERNAL = threading.Lock()


class PathEscapesDocsRoot(Exception):
    pass


def resolve_under_docs_root_internal(docs_root, rel_path):
    docs_root = Path(docs_root).resolve()
    candidate = (docs_root / rel_path).resolve()
    if not (candidate == docs_root or candidate.is_relative_to(docs_root)):
        raise PathEscapesDocsRoot(f"{rel_path!r} resolves outside docs root {docs_root}")
    return candidate


def lockfile_path_internal(docs_root, resolved_path):
    locks_dir = Path(docs_root) / ".locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    from ..common.hashing import sha256_hex

    return locks_dir / f"{sha256_hex(str(resolved_path))}.lock"


def read_doc(docs_root, rel_path):
    path = resolve_under_docs_root_internal(docs_root, rel_path)
    if not path.exists():
        return None
    return path.read_text()


def atomic_replace_internal(path, content):
    tmp_path = path.with_name(f".tmp-{path.name}-{os.getpid()}-{threading.get_ident()}")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


@contextmanager
def locked_internal(docs_root, path):
    """threading.Lock for intra-process thread safety, flock(LOCK_EX) for cross-process
    safety (the CLI is a separate process from the running server) -- both held together
    across the caller's whole critical section, not either alone: flock alone loses
    updates when threads share a cached file descriptor, and threading.Lock alone does
    nothing across process boundaries."""
    lock_path = lockfile_path_internal(docs_root, path)
    with LOCK_INTERNAL:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)


def write_doc(docs_root, rel_path, content):
    """The only writer of docs/ files (api owns this exclusively, per ARCHITECTURE.md).
    An unconditional overwrite, serialized against any other reader/writer of the same
    path -- correct for the UI's explicit-save case (last write wins; there is no
    meaningful "current value" to preserve across a human's edit session). For a true
    atomic read-modify-write, use update_doc() instead -- write_doc() only locks its own
    write, not a read that happened before it."""
    path = resolve_under_docs_root_internal(docs_root, rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked_internal(docs_root, path):
        atomic_replace_internal(path, content)
    return path


def update_doc(docs_root, rel_path, mutate_fn):
    """Atomic read-modify-write: mutate_fn(current_content_or_None) -> new_content,
    executed while holding the SAME lock across the read, the mutation, and the write --
    no other reader/writer can interleave inside that window. This is what write_doc()
    alone cannot provide (read and write as two separate calls means the lock is released
    between them)."""
    path = resolve_under_docs_root_internal(docs_root, rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked_internal(docs_root, path):
        current = path.read_text() if path.exists() else None
        new_content = mutate_fn(current)
        atomic_replace_internal(path, new_content)
    return path
