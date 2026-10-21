"""Role gates for Flask views (FR-AUTH-4)."""

from __future__ import annotations

from functools import wraps

from flask import abort, redirect, session, url_for


def current_role() -> str | None:
    return session.get("role")


def current_user_id() -> str | None:
    return session.get("user_id")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user_id():
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def require_role(*roles: str):
    """Abort 403 when the signed-in role is not in `roles` (T-AUTH-2)."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user_id():
                return redirect(url_for("auth.login"))
            if current_role() not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
