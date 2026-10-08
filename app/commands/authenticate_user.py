"""AuthenticateUser — FR-AUTH-3.

Looks up rm_user (never the event stream), verifies the hash, then appends
UserLoggedIn for the audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.domain.passwords import verify_password
from app.domain.user import InvalidCredentialsError, normalize_email, user_stream_id
from app.events.event_types import USER_LOGGED_IN


@dataclass
class AuthenticateUser:
    email: str
    password: str

    def __repr__(self) -> str:
        return f"AuthenticateUser(email={self.email!r}, password=***)"


def authenticate_user(store, users, command: AuthenticateUser) -> str:
    """Returns the user_id on success. Controllers load the session via GetUserById."""
    record = users.get_by_email(normalize_email(command.email))
    if record is None or not verify_password(record.password_hash, command.password):
        raise InvalidCredentialsError("invalid email or password")

    logged_in_at = datetime.now(timezone.utc)
    expected = store.last_version(user_stream_id(record.user_id))
    store.append(
        user_stream_id(record.user_id),
        expected,
        USER_LOGGED_IN,
        {
            "user_id": record.user_id,
            "logged_in_at": logged_in_at.isoformat(),
        },
        metadata={"actor": record.user_id, "actor_role": record.role},
    )
    users.set_last_login_at(record.user_id, logged_in_at)
    return record.user_id
