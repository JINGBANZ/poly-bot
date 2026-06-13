"""Tests for bot/statestore.py — concurrency-safe JSON state."""

import json
import multiprocessing
import os
import threading

import pytest

from bot import statestore


def _proc_increment(path, n):
    """Top-level so multiprocessing can pickle it."""
    from bot import statestore as ss
    for _ in range(n):
        ss.locked_update(path, lambda obj: {"count": obj["count"] + 1},
                         default=lambda: {"count": 0})


class TestBasics:
    def test_read_missing_returns_default(self, tmp_path):
        assert statestore.read_json(str(tmp_path / "nope.json"),
                                    default={"a": 1}) == {"a": 1}

    def test_write_then_read(self, tmp_path):
        path = str(tmp_path / "x.json")
        statestore.write_json(path, {"a": [1, 2]})
        assert statestore.read_json(path) == {"a": [1, 2]}

    def test_write_is_atomic_no_tmp_left(self, tmp_path):
        path = str(tmp_path / "x.json")
        statestore.write_json(path, {"a": 1})
        assert not os.path.exists(path + ".tmp")

    def test_corrupt_read_returns_default(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            f.write("{truncated")
        assert statestore.read_json(path, default=[]) == []

    def test_locked_update_mutate_in_place(self, tmp_path):
        path = str(tmp_path / "x.json")
        statestore.locked_update(path, lambda o: o.append(1), default=list)
        statestore.locked_update(path, lambda o: o.append(2), default=list)
        assert statestore.read_json(path) == [1, 2]

    def test_locked_update_replacement_value(self, tmp_path):
        path = str(tmp_path / "x.json")
        statestore.write_json(path, [1, 2, 3])
        statestore.locked_update(path, lambda o: [x for x in o if x != 2])
        assert statestore.read_json(path) == [1, 3]

    def test_locked_is_reentrant_for_same_thread(self, tmp_path):
        path = str(tmp_path / "x.json")
        with statestore.locked(path):
            with statestore.locked(path):
                statestore.write_json(path, {"ok": True})
        assert statestore.read_json(path) == {"ok": True}


class TestConcurrency:
    def test_threads_do_not_lose_updates(self, tmp_path):
        """The exact failure mode of the old unlocked read-modify-write."""
        path = str(tmp_path / "counter.json")
        n_threads, n_inc = 8, 50

        def worker():
            for _ in range(n_inc):
                statestore.locked_update(
                    path, lambda obj: {"count": obj["count"] + 1},
                    default=lambda: {"count": 0})

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert statestore.read_json(path)["count"] == n_threads * n_inc

    def test_processes_do_not_lose_updates(self, tmp_path):
        """flock protects against a CLI tool racing the daemon."""
        path = str(tmp_path / "counter.json")
        n_procs, n_inc = 3, 20
        procs = [multiprocessing.Process(target=_proc_increment,
                                         args=(path, n_inc))
                 for _ in range(n_procs)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        assert all(p.exitcode == 0 for p in procs)
        assert statestore.read_json(path)["count"] == n_procs * n_inc
