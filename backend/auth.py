from fastapi import HTTPException, Request


def require_admin(request: Request) -> str:
    """
    FastAPI dependency. Returns the logged-in admin username or raises a
    redirect to the login page.
    """
    username = request.session.get("admin_user")
    if not username:
        raise HTTPException(
            status_code=303,
            headers={"Location": "/admin/login"},
        )
    return username
