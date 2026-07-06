"""Unit tests for RotatingCaller (S-05).

Covers:
- Rotation follows the given pattern in order
- Default pattern is range(len(callers)) when none given
- Concurrent calls from multiple threads don't corrupt _idx
- __repr__ doesn't raise
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from src.providers.rotating import RotatingCaller


def _make_caller(name: str) -> MagicMock:
    """Return a mock ModelCaller whose .complete() records the caller name."""
    caller = MagicMock()
    result = MagicMock()
    result.text = name
    caller.complete.return_value = result
    return caller


class TestRotationPattern:
    def test_explicit_pattern_followed_in_order(self):
        a, b = _make_caller("a"), _make_caller("b")
        rc = RotatingCaller([a, b], pattern=[0, 1, 1])
        results = [rc.complete("x", max_tokens=10).text for _ in range(6)]
        # Pattern [0,1,1] repeats: a,b,b,a,b,b
        assert results == ["a", "b", "b", "a", "b", "b"]

    def test_default_pattern_is_sequential(self):
        a, b, c = _make_caller("a"), _make_caller("b"), _make_caller("c")
        rc = RotatingCaller([a, b, c])  # no explicit pattern
        results = [rc.complete("x", max_tokens=10).text for _ in range(6)]
        assert results == ["a", "b", "c", "a", "b", "c"]

    def test_single_caller_always_returns_same(self):
        a = _make_caller("a")
        rc = RotatingCaller([a], pattern=[0, 0, 0])
        results = [rc.complete("x", max_tokens=10).text for _ in range(4)]
        assert all(r == "a" for r in results)

    def test_pattern_wraps_via_modulo(self):
        """Pattern index wraps around correctly after exhausting one cycle."""
        a, b = _make_caller("a"), _make_caller("b")
        rc = RotatingCaller([a, b], pattern=[0, 1])
        for _ in range(10):
            rc.complete("x", max_tokens=10)
        assert a.complete.call_count == 5
        assert b.complete.call_count == 5


class TestThreadSafety:
    def test_concurrent_calls_do_not_corrupt_index(self):
        """20 threads calling .complete() simultaneously must collectively
        produce exactly 20 calls and every call must land on a valid caller."""
        n_threads = 20
        callers = [_make_caller(f"caller_{i}") for i in range(2)]
        rc = RotatingCaller(callers, pattern=[0, 1])

        errors: list[Exception] = []

        def worker():
            try:
                rc.complete("prompt", max_tokens=10)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        total_calls = sum(c.complete.call_count for c in callers)
        assert total_calls == n_threads


class TestRepr:
    def test_repr_does_not_raise(self):
        a, b = _make_caller("a"), _make_caller("b")
        rc = RotatingCaller([a, b], pattern=[0, 1])
        r = repr(rc)
        assert "RotatingCaller" in r
        assert "callers=2" in r

    def test_repr_reflects_current_index(self):
        a = _make_caller("a")
        rc = RotatingCaller([a], pattern=[0])
        rc.complete("x", max_tokens=10)
        assert "idx=1" in repr(rc)
