from fastapi import HTTPException, Request


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return True
    # Browser fetch() with default accept of "*/*" — distinguish via path
    # convention: any /admin/*/data and /admin/orders/*/(ready|collected|cancel)
    # is a JSON endpoint.
    path = request.url.path
    if path.endswith("/data"):
        return True
    if path.startswith("/admin/orders/") and path.rsplit("/", 1)[-1] in (
        "ready",
        "collected",
        "cancel",
    ):
        return True
    return False


def require_admin(request: Request) -> str:
    """
    FastAPI dependency. Returns the logged-in admin username or raises:
      - 401 JSON for API/JSON callers
      - 303 redirect to the login page for browser HTML navigations
    """
    username = request.session.get("admin_user")
    if username:
        return username

    if _wants_json(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    raise HTTPException(
        status_code=303,
        headers={"Location": "/admin/login"},
    )
