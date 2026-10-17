"""Atomic, digest-checked checkpoints. One record owns submissions, scores and accounting."""

import fcntl
import gzip
import json
import os
import re
import tempfile
from pathlib import Path

from .canonical import digest, packed
from .errors import BudgetExceeded, Rejected

SYMLINK_REFUSED = 'Symlinks are forbidden in managed storage'
QUOTA_EXHAUSTED = 'Managed storage quota exhausted'
RUN_STOPS = frozenset({SYMLINK_REFUSED, QUOTA_EXHAUSTED})  # Storage failures that stop the run instead of failing one job.


def valid_run_id(run_id):
    return isinstance(run_id, str) and re.fullmatch(r'[A-Za-z0-9_-]+', run_id) is not None


def tree_bytes(root):
    """Total size of a directory tree; any symlink inside it is refused."""
    total = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            p = Path(base) / name
            if p.is_symlink():
                raise Rejected(SYMLINK_REFUSED)
            if p.is_file():
                total += p.stat().st_size
    return total


class Storage:
    """``<root>/<run_id>/``, locked exclusively; at most one active run per root."""

    def __init__(self, root, run_id, limit):
        if not valid_run_id(run_id):
            raise Rejected('Invalid run name')
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.limit = limit
        self.global_lock = self._lock(self.root / '.execution.lock', 'Another agent is active in managed storage')
        self.path = self.root / run_id
        self.path.mkdir(exist_ok=True)
        if self.path.is_symlink():
            raise Rejected('Unsafe run directory')
        self.lock = self._lock(self.path / '.lock', 'Run already active')
        self.check()

    @staticmethod
    def _lock(path, message):
        handle = open(path, 'a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            handle.close()
            raise Rejected(message) from e
        return handle

    def check(self, extra=0):
        size = tree_bytes(self.root)  # Always retain the symlink boundary, even without a quota.
        if self.limit is not None and size + extra > self.limit:
            raise BudgetExceeded(QUOTA_EXHAUSTED)

    def write(self, relative, value):
        target = self.path / relative
        if target.parent != self.path or target.is_symlink():
            raise Rejected('Unsafe output path')
        blob = gzip.compress(packed(value), mtime=0)
        self.check(len(blob))  # Includes coexistence during atomic replacement.
        fd, tmp = tempfile.mkstemp(prefix='.atomic-', dir=self.path)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(blob)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, target)
            parent = os.open(self.path, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def read(self, relative):
        path = self.path / relative
        if not path.exists():
            return None
        if path.is_symlink():
            raise Rejected('Unsafe checkpoint')
        with gzip.open(path, 'rt') as src:
            return json.load(src)

    def save(self, state):
        state['checkpoint_digest'] = checkpoint_digest(state)
        self.write('checkpoint.json.gz', state)

    def load(self):
        state = self.read('checkpoint.json.gz')
        if state and state.get('checkpoint_digest') != checkpoint_digest(state):
            raise Rejected('Checkpoint digest mismatch')
        return state

    def close(self):
        for handle in (self.lock, self.global_lock):
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


def checkpoint_digest(state):
    return digest({k: v for k, v in state.items() if k != 'checkpoint_digest'})


def read_checkpoint(path):
    """Load and verify a checkpoint written by :class:`Storage`."""
    with gzip.open(path, 'rt') as f:
        state = json.load(f)
    if state.get('checkpoint_digest') != checkpoint_digest(state):
        raise Rejected('Checkpoint digest mismatch')
    return state
