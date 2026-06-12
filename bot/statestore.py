"""Concurrency-safe JSON state — the single way to touch shared state files.

Historically every module did an unlocked load → modify → save on its JSON
file. That was safe while the bot was one sequential loop; the event-driven
core (bot/core) runs risk checks, strategy actors, and slow research workers
concurrently, and CLI tools (`python -m bot.shadow`, status) can run beside
the daemon. This module makes the read-modify-write cycle atomic:

  read_json(path, default)          tolerant read (shared semantics)
  write_json(path, obj)             atomic tmp+rename write
  locked_update(path, fn, default)  THE way to mutate shared files:
                                    lock → load → fn(obj) → atomic write

Locking model (both layers are always taken together, in this order):
  1. a per-path threading.RLock — serializes threads inside one process
  2. fcntl.flock on a `<path>.lock` sidecar — serializes processes
Writes stay atomic (tmp + os.replace), so a kill at any moment leaves either
the old or the new file, never a torn one — the restart-anytime property.

RULES:
  - Never do network I/O inside a locked_update fn. Fetch books/metadata
    first, then mutate. Locks are held for milliseconds, not round-trips.
  - Multi-writer files (shadow ledger, open orders) MUST use locked_update.
    Per-strategy state files are single-writer (their strategy actor) and may
    load/save freely, but should still use write_json for atomicity.
"""

import fcntl
import json
import os
import threading

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()
# Per-path flock state: {abspath: {"fd": int, "depth": int}}. flock(2) is
# per open file description, NOT per process — a nested locked() on the same
# path must reuse the already-flocked fd or it deadlocks against itself.
# Mutated only while the path's RLock is held, so no extra guard is needed.
_flocks: dict[str, dict] = {}


def _path_lock(path: str) -> threading.RLock:
    key = os.path.abspath(path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
        return lock


class locked:
    """Hold both the thread lock and the inter-process flock for a path.

    Reentrant within a thread: the RLock allows the re-entry and the flock
    fd is reference-counted instead of re-acquired.
    """

    def __init__(self, path: str):
        self.path = path
        self._key = os.path.abspath(path)
        self._tlock = _path_lock(path)

    def __enter__(self):
        self._tlock.acquire()
        try:
            entry = _flocks.get(self._key)
            if entry is not None:
                entry["depth"] += 1
                return self
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            fd = os.open(self.path + ".lock", os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except Exception:
                os.close(fd)
                raise
            _flocks[self._key] = {"fd": fd, "depth": 1}
        except Exception:
            self._tlock.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            entry = _flocks.get(self._key)
            if entry is not None:
                entry["depth"] -= 1
                if entry["depth"] <= 0:
                    del _flocks[self._key]
                    fcntl.flock(entry["fd"], fcntl.LOCK_UN)
                    os.close(entry["fd"])
        finally:
            self._tlock.release()
        return False


def read_json(path: str, default=None):
    """Read a JSON file; returns default on missing/corrupt file."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path: str, obj):
    """Atomic JSON write: tmp file + os.replace. Safe to kill at any moment."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def locked_update(path: str, fn, default=None):
    """Atomically read-modify-write a JSON file.

    fn receives the current value (or `default` — pass a factory result, it
    is used as-is) and either mutates it in place or returns a replacement.
    Returns the value that was written. fn must not do network I/O.
    """
    with locked(path):
        obj = read_json(path, default=None)
        if obj is None:
            obj = default() if callable(default) else default
        new = fn(obj)
        if new is None:
            new = obj
        write_json(path, new)
        return new
