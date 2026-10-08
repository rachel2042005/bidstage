"""Authentication: registration, hashing, login, and role gates.

Seams: command handlers, users projection (not the event stream), Flask
routes `/register` `/login` `/logout`, and `@require_role`.
Trace: FR-AUTH-1 … FR-AUTH-4, T-AUTH-2, T-AUTH-6, T-AUTH-7.
"""

from __future__ import annotations

import pytest

from app.commands.assign_user_role import assign_user_role
from app.commands.authenticate_user import AuthenticateUser, authenticate_user
from app.commands.register_user import RegisterUser, register_user
from app.domain.user import (
    EmailTakenError,
    InvalidCredentialsError,
    InvalidRoleError,
    ORGANIZER,
    RoleImmutableError,
    SUPPLIER,
    replay_user,
)
from app.events.event_types import USER_LOGGED_IN, USER_REGISTERED
from app.events.memory import InMemoryEventStore
from app.projections.users import InMemoryUsersProjection
from app.queries.get_user import get_user_by_email, get_user_by_id
from main import create_app


PLAINTEXT = "correct-horse-battery"


def _backend():
    store = InMemoryEventStore()
    users = InMemoryUsersProjection()
    return store, users


def _register(store, users, *, email="org@example.com", role=ORGANIZER, password=PLAINTEXT):
    return register_user(
        store,
        users,
        RegisterUser(
            email=email,
            password=password,
            display_name="נעמה",
            role=role,
        ),
    )


@pytest.fixture
def app():
    store, users = _backend()
    application = create_app(event_store=store, users=users)
    application.config["TESTING"] = True
    application.config["SECRET_KEY"] = "test-secret"
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def test_register_stores_salted_hash_not_plaintext():
    """T-AUTH-6 / FR-AUTH-2: only a salted hash is persisted."""
    store, users = _backend()
    user_id = _register(store, users)

    events = store.read_stream(f"user-{user_id}")
    assert len(events) == 1
    assert events[0].event_type == USER_REGISTERED
    payload = events[0].event_data
    assert "password" not in payload
    assert payload["password_hash"] != PLAINTEXT
    assert PLAINTEXT not in payload["password_hash"]
    assert payload["password_hash"].startswith("pbkdf2:") or "scrypt" in payload["password_hash"]

    record = get_user_by_email(users, "org@example.com")
    assert record is not None
    assert record.password_hash == payload["password_hash"]
    assert PLAINTEXT not in repr(record)


def test_register_email_is_case_insensitive_unique():
    store, users = _backend()
    _register(store, users, email="Org@Example.com")
    with pytest.raises(EmailTakenError):
        _register(store, users, email="org@example.com", role=SUPPLIER)


def test_register_rejects_unknown_role():
    store, users = _backend()
    with pytest.raises(InvalidRoleError):
        _register(store, users, role="admin")


def test_register_as_organizer_or_supplier():
    """FR-AUTH-1: role is chosen at registration."""
    store, users = _backend()
    org_id = _register(store, users, email="a@example.com", role=ORGANIZER)
    sup_id = _register(store, users, email="b@example.com", role=SUPPLIER)
    assert get_user_by_email(users, "a@example.com").role == ORGANIZER
    assert get_user_by_email(users, "b@example.com").role == SUPPLIER
    org = replay_user(store.read_stream(f"user-{org_id}"))
    sup = replay_user(store.read_stream(f"user-{sup_id}"))
    assert org.role == ORGANIZER
    assert sup.role == SUPPLIER


def test_login_succeeds_and_appends_user_logged_in():
    store, users = _backend()
    user_id = _register(store, users)
    logged_in_id = authenticate_user(
        store,
        users,
        AuthenticateUser(email="org@example.com", password=PLAINTEXT),
    )
    assert logged_in_id == user_id
    record = get_user_by_id(users, logged_in_id)
    assert record is not None
    assert record.role == ORGANIZER
    types = [e.event_type for e in store.read_stream(f"user-{user_id}")]
    assert types == [USER_REGISTERED, USER_LOGGED_IN]


def test_commands_never_include_plaintext_in_repr():
    """FR-AUTH-2: command objects must not leak the password when logged."""
    register = RegisterUser(
        email="org@example.com",
        password=PLAINTEXT,
        display_name="נעמה",
        role=ORGANIZER,
    )
    login = AuthenticateUser(email="org@example.com", password=PLAINTEXT)
    assert PLAINTEXT not in repr(register)
    assert PLAINTEXT not in repr(login)


def test_login_rejects_wrong_password():
    store, users = _backend()
    _register(store, users)
    with pytest.raises(InvalidCredentialsError):
        authenticate_user(
            store,
            users,
            AuthenticateUser(email="org@example.com", password="wrong-password"),
        )
    types = [e.event_type for e in store.read_all()]
    assert USER_LOGGED_IN not in types


def test_role_change_after_registration_is_rejected():
    """T-AUTH-7 / FR-AUTH-1: role is immutable."""
    store, users = _backend()
    user_id = _register(store, users, role=ORGANIZER)
    with pytest.raises(RoleImmutableError):
        assign_user_role(store, users, user_id, SUPPLIER)
    assert get_user_by_email(users, "org@example.com").role == ORGANIZER
    replayed = replay_user(store.read_stream(f"user-{user_id}"))
    assert replayed.role == ORGANIZER
    assert len(store.read_stream(f"user-{user_id}")) == 1


def test_query_does_not_read_the_event_stream():
    """NFR-CQRS-2: GetUserByEmail uses the projection only."""
    store, users = _backend()
    _register(store, users)
    found = get_user_by_email(users, "org@example.com")
    assert found is not None
    assert found.email == "org@example.com"


def test_register_form_creates_session(client):
    response = client.post(
        "/register",
        data={
            "email": "org@example.com",
            "password": PLAINTEXT,
            "display_name": "מארגנת",
            "role": ORGANIZER,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess["role"] == ORGANIZER
        assert "user_id" in sess


def test_login_logout_round_trip(client):
    client.post(
        "/register",
        data={
            "email": "sup@example.com",
            "password": PLAINTEXT,
            "display_name": "ספק",
            "role": SUPPLIER,
        },
    )
    client.get("/logout")
    with client.session_transaction() as sess:
        assert "user_id" not in sess

    response = client.post(
        "/login",
        data={"email": "sup@example.com", "password": PLAINTEXT},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess["role"] == SUPPLIER

    client.get("/logout")
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_supplier_cannot_open_organizer_route(client):
    """T-AUTH-2 / FR-AUTH-4."""
    client.post(
        "/register",
        data={
            "email": "sup@example.com",
            "password": PLAINTEXT,
            "display_name": "ספק",
            "role": SUPPLIER,
        },
    )
    response = client.get("/organizer/")
    assert response.status_code == 403
    assert "sup@example.com".encode() not in response.data


def test_organizer_cannot_open_supplier_route(client):
    """FR-AUTH-4: organizer routes and supplier routes are mutually exclusive."""
    client.post(
        "/register",
        data={
            "email": "org@example.com",
            "password": PLAINTEXT,
            "display_name": "מארגנת",
            "role": ORGANIZER,
        },
    )
    response = client.get("/supplier/")
    assert response.status_code == 403


def test_organizer_can_open_organizer_route(client):
    client.post(
        "/register",
        data={
            "email": "org@example.com",
            "password": PLAINTEXT,
            "display_name": "מארגנת",
            "role": ORGANIZER,
        },
    )
    response = client.get("/organizer/")
    assert response.status_code == 200


def test_unauthenticated_organizer_route_redirects_to_login(client):
    response = client.get("/organizer/", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_register_and_login_pages_render_hebrew(client):
    register_page = client.get("/register")
    login_page = client.get("/login")
    assert register_page.status_code == 200
    assert login_page.status_code == 200
    assert "dir=\"rtl\"" in register_page.get_data(as_text=True)
    assert "הרשמה" in register_page.get_data(as_text=True)
    assert "כניסה" in login_page.get_data(as_text=True)
