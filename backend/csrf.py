"""Lightweight CSRF protection backed by the user's session.

Usage:
    from backend.csrf import csrf_token, require_csrf

    # In a Jinja template, expose `csrf_token(request)` and embed it in
    # every state-changing form / fetch call.

    # In a route, add `Depends(require_csrf)` to any state-changing
    # endpoint. require_csrf accepts the token from either:
    #   - form field 'csrf_token'
    #   - header 'X-CSRF-Token'
    # and validates it against the session-bound value with constant-time
    # comparison.

The token is created on first use and reused for the session's lifetime.
That keeps form re-submission and SPA-style polling simple while still
defending against cross-site forged requests, since the cookie alone is
no longer enough — the attacker would need to read the session-scoped
token, which same-origin policy denies.
"""
from __future__ import annotations

import hmac
import secrets

from fastapi import HTTPException, Request


_SESSION_KEY = "csrf_token"


def csrf_token(request: Request) -> str:
    """Return (and lazily create) the CSRF token for the current session."""
    token = request.session.get(_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[_SESSION_KEY] = token
    return token


async def require_csrf(request: Request) -> None:
    """FastAPI dependency: validate the CSRF token on a state-changing request."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    expected = request.session.get(_SESSION_KEY)
    if not expected:
        raise HTTPException(status_code=403, detail="CSRF token missing from session")

    received = request.headers.get("x-csrf-token")
    if not received and request.headers.get("content-type", "").startswith(
        "application/x-www-form-urlencoded"
    ):
        try:
            form = await request.form()
        except Exception:
            form = None
        if form is not None:
            received = form.get("csrf_token")

    if not received or not hmac.compare_digest(str(received), str(expected)):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
