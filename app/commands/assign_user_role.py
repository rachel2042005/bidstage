"""There is no role-assignment command. FR-AUTH-1 / T-AUTH-7.

Role is a field of UserRegistered. A compensating UserRoleAssigned event
would be a spec change, not a correction (specs/03-domain-events.md §2).
"""

from __future__ import annotations

from app.domain.user import (
    RoleImmutableError,
    UserNotFoundError,
    replay_user,
    user_stream_id,
)


def assign_user_role(store, users, user_id: str, new_role: str) -> None:
    """Rejects any role change. Replays the user stream so a stale projection
    cannot authorize a rewrite of identity.

    `users` is unused on purpose: role is stream state, not a read-model field
    the command is allowed to overwrite.
    """
    del users
    events = store.read_stream(user_stream_id(user_id))
    if not events:
        raise UserNotFoundError(user_id)
    state = replay_user(events)
    if new_role != state.role:
        raise RoleImmutableError(
            "Role is chosen at registration and is immutable (FR-AUTH-1)."
        )
