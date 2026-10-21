"""RegisterUser — FR-AUTH-1, FR-AUTH-2.

Appends UserRegistered and projects rm_user in the same transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.domain.passwords import hash_password
from app.domain.user import (
    EmailTakenError,
    InvalidRoleError,
    ROLES,
    normalize_email,
    user_stream_id,
)
from app.events.event_types import USER_REGISTERED
from app.projections.users import UserRecord


@dataclass
class RegisterUser:
    email: str
    password: str
    display_name: str
    role: str

    def __repr__(self) -> str:
        return (
            f"RegisterUser(email={self.email!r}, display_name={self.display_name!r}, "
            f"role={self.role!r}, password=***)"
        )


def register_user(store, users, command: RegisterUser) -> str:
    email = normalize_email(command.email)
    if not email or "@" not in email:
        raise ValueError("email is required")
    display_name = command.display_name.strip()
    if not display_name:
        raise ValueError("display_name is required")
    if command.role not in ROLES:
        raise InvalidRoleError(
            "role must be organizer or supplier; administrator is out of scope"
        )
    if users.get_by_email(email) is not None:
        raise EmailTakenError(email)

    user_id = str(uuid.uuid4())
    password_hash = hash_password(command.password)
    registered_at = datetime.now(timezone.utc)
    event_data = {
        "user_id": user_id,
        "email": email,
        "display_name": display_name,
        "role": command.role,
        "password_hash": password_hash,
    }
    store.append(
        user_stream_id(user_id),
        0,
        USER_REGISTERED,
        event_data,
        metadata={"actor": user_id, "actor_role": command.role},
    )
    users.insert(
        UserRecord(
            user_id=user_id,
            email=email,
            display_name=display_name,
            role=command.role,
            password_hash=password_hash,
            registered_at=registered_at,
        )
    )
    return user_id
