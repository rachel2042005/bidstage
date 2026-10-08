"""User aggregate. Pure replay — no I/O (NFR-ES-3).

Role is fixed on UserRegistered (FR-AUTH-1). There is no UserRoleAssigned
event: a later role change would rewrite identity, which the spec forbids.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.events.event_store import StoredEvent
from app.events.event_types import USER_LOGGED_IN, USER_REGISTERED

ORGANIZER = "organizer"
SUPPLIER = "supplier"
ROLES = frozenset({ORGANIZER, SUPPLIER})


class DomainError(ValueError):
    pass


class InvalidRoleError(DomainError):
    pass


class EmailTakenError(DomainError):
    pass


class InvalidCredentialsError(DomainError):
    pass


class RoleImmutableError(DomainError):
    pass


class UserNotFoundError(DomainError):
    pass


class UnknownEventTypeError(DomainError):
    pass


@dataclass(frozen=True)
class UserState:
    user_id: str
    email: str
    display_name: str
    role: str
    password_hash: str
    registered: bool = False
    last_login_at: datetime | None = None


def empty_user() -> UserState:
    return UserState(
        user_id="",
        email="",
        display_name="",
        role="",
        password_hash="",
    )


def apply_user(state: UserState, event: StoredEvent) -> UserState:
    if event.event_type == USER_REGISTERED:
        data = event.event_data
        role = data["role"]
        if role not in ROLES:
            raise InvalidRoleError(f"unsupported role: {role}")
        return UserState(
            user_id=data["user_id"],
            email=data["email"],
            display_name=data["display_name"],
            role=role,
            password_hash=data["password_hash"],
            registered=True,
        )
    if event.event_type == USER_LOGGED_IN:
        if not state.registered:
            raise DomainError("UserLoggedIn on an unregistered stream")
        logged_in_at = event.event_data.get("logged_in_at")
        when = datetime.fromisoformat(logged_in_at) if logged_in_at else event.created_at
        return UserState(
            user_id=state.user_id,
            email=state.email,
            display_name=state.display_name,
            role=state.role,
            password_hash=state.password_hash,
            registered=True,
            last_login_at=when,
        )
    raise UnknownEventTypeError(event.event_type)


def replay_user(events: list[StoredEvent]) -> UserState:
    state = empty_user()
    previous = 0
    for event in events:
        if event.version != previous + 1:
            raise DomainError(
                f"events must be contiguous; saw version {event.version} after {previous}"
            )
        state = apply_user(state, event)
        previous = event.version
    return state


def user_stream_id(user_id: str) -> str:
    return f"user-{user_id}"


def normalize_email(email: str) -> str:
    return email.strip().lower()
