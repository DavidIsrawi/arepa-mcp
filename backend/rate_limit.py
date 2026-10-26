"""Tiny in-memory sliding-window rate limiter for voucher attempts.

Per-key (e.g. customer_id): if more than MAX_FAILS failures occur within
WINDOW seconds, the key is locked out until enough old failures fall out
of the window. Cleared on success.
"""
import math
import time
from collections import defaultdict, deque
from threading import Lock

_WINDOW = 60          # seconds
_MAX_FAILS = 10
_attempts: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def _prune(dq: deque, now: float) -> None:
    while dq and now - dq[0] > _WINDOW:
        dq.popleft()


def record_failure(key: str) -> None:
    now = time.time()
    with _lock:
        dq = _attempts[key]
        dq.append(now)
        _prune(dq, now)


def is_locked_out(key: str) -> bool:
    now = time.time()
    with _lock:
        dq = _attempts.get(key)
        if not dq:
            return False
        _prune(dq, now)
        return len(dq) >= _MAX_FAILS


def seconds_until_unlock(key: str) -> int:
    """Whole seconds until ``key`` is no longer locked out (0 if not locked)."""
    now = time.time()
    with _lock:
        dq = _attempts.get(key)
        if not dq:
            return 0
        _prune(dq, now)
        if len(dq) < _MAX_FAILS:
            return 0
        oldest_blocking = dq[len(dq) - _MAX_FAILS]
        return max(1, math.ceil(oldest_blocking + _WINDOW - now))


def clear(key: str) -> None:
    with _lock:
        _attempts.pop(key, None)
