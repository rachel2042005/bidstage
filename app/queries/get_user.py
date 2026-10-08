"""Read-side user lookups. Projection only (NFR-CQRS-2)."""

from __future__ import annotations

from app.domain.user import normalize_email
from app.projections.users import UserRecord, UsersProjection


def get_user_by_email(users: UsersProjection, email: str) -> UserRecord | None:
    return users.get_by_email(normalize_email(email))


def get_user_by_id(users: UsersProjection, user_id: str) -> UserRecord | None:
    return users.get_by_id(user_id)
