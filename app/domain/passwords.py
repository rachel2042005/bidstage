"""Password hashing. Plaintext is never persisted or logged (FR-AUTH-2)."""

from __future__ import annotations

from werkzeug.security import check_password_hash, generate_password_hash

_MIN_LENGTH = 8


class WeakPasswordError(ValueError):
    pass


def hash_password(plaintext: str) -> str:
    if len(plaintext) < _MIN_LENGTH:
        raise WeakPasswordError(f"password must be at least {_MIN_LENGTH} characters")
    return generate_password_hash(plaintext)


def verify_password(password_hash: str, plaintext: str) -> bool:
    return check_password_hash(password_hash, plaintext)
