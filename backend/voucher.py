"""Time-rotating voucher codes shown on the public dashboard.

The voucher is derived from a secret + the current 3-minute time window,
so anyone *looking at the dashboard right now* can read it, but it is
useless a few minutes later. The previous window is also accepted as a
grace period so codes copied just before rotation still work briefly.
"""
import hashlib
import hmac
import os
import secrets
import time

WINDOW_SECONDS = 180  # 3 minutes

# Crockford-ish: no 0, 1, I, L, O — easy to read off a screen.
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_CODE_LENGTH = 6
_SECRET_ENV = "VOUCHER_SECRET"


def _secret() -> bytes:
    s = os.environ.get(_SECRET_ENV)
    if not s:
        # Auto-generate a per-process secret on first use. Workshop-friendly:
        # restarting the server rotates everyone's codes, which is fine.
        s = secrets.token_hex(32)
        os.environ[_SECRET_ENV] = s
    return s.encode()


def _code_for_window(window: int) -> str:
    digest = hmac.new(_secret(), str(window).encode(), hashlib.sha256).digest()
    return "".join(_ALPHABET[b % len(_ALPHABET)] for b in digest[:_CODE_LENGTH])


def current_voucher() -> str:
    return _code_for_window(int(time.time()) // WINDOW_SECONDS)


def seconds_until_rotation() -> int:
    now = int(time.time())
    return WINDOW_SECONDS - (now % WINDOW_SECONDS)


def normalize(code: str) -> str:
    return (code or "").strip().upper().replace("-", "").replace(" ", "")


def invalid_characters(code: str) -> list[str]:
    """Characters in ``code`` that can never appear in a voucher, in order of appearance."""
    seen: list[str] = []
    for char in normalize(code):
        if char not in _ALPHABET and char not in seen:
            seen.append(char)
    return seen


def is_well_formed(code: str) -> bool:
    """True if ``code`` could be a voucher at all (length and alphabet)."""
    code = normalize(code)
    return len(code) == _CODE_LENGTH and not invalid_characters(code)


def is_valid_voucher(code: str) -> bool:
    if not code:
        return False
    code = normalize(code)
    if len(code) != _CODE_LENGTH:
        return False
    w = int(time.time()) // WINDOW_SECONDS
    return code in {_code_for_window(w), _code_for_window(w - 1)}
