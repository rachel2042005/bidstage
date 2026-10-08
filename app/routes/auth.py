"""Register, login, logout. Thin controller: parse, dispatch, render."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.commands.authenticate_user import AuthenticateUser, authenticate_user
from app.commands.register_user import RegisterUser, register_user
from app.domain.passwords import WeakPasswordError
from app.domain.user import (
    ORGANIZER,
    SUPPLIER,
    DomainError,
    EmailTakenError,
    InvalidCredentialsError,
    InvalidRoleError,
)
from app.queries.get_user import get_user_by_id
from app.routes.services import unit_of_work

bp = Blueprint("auth", __name__)

_ROLE_LABELS = {
    ORGANIZER: "מארגן/ת",
    SUPPLIER: "ספק/ית",
}


def _validation_message(detail: str) -> str:
    if detail == "email is required":
        return "יש להזין כתובת דוא״ל תקינה."
    if detail == "display_name is required":
        return "יש להזין שם."
    return "הנתונים שהוזנו אינם תקינים."


def _put_session(user_id: str, email: str, display_name: str, role: str) -> None:
    session.clear()
    session["user_id"] = user_id
    session["email"] = email
    session["display_name"] = display_name
    session["role"] = role


@bp.get("/register")
def register_form():
    if session.get("user_id"):
        return redirect(url_for("pages.home"))
    return render_template("auth/register.html", roles=_ROLE_LABELS)


@bp.post("/register")
def register_submit():
    if session.get("user_id"):
        return redirect(url_for("pages.home"))
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    display_name = request.form.get("display_name", "")
    role = request.form.get("role", "")
    try:
        with unit_of_work() as (store, users, _tenders):
            user_id = register_user(
                store,
                users,
                RegisterUser(
                    email=email,
                    password=password,
                    display_name=display_name,
                    role=role,
                ),
            )
            record = get_user_by_id(users, user_id)
        if record is None:
            raise RuntimeError("RegisterUser projected no rm_user row")
        _put_session(record.user_id, record.email, record.display_name, record.role)
        flash("נרשמת בהצלחה.", "success")
        return redirect(url_for("pages.home"))
    except EmailTakenError:
        flash("כתובת הדוא״ל כבר רשומה במערכת.", "danger")
    except InvalidRoleError:
        flash("יש לבחור תפקיד: מארגן/ת או ספק/ית.", "danger")
    except WeakPasswordError:
        flash("הסיסמה חייבת להכיל לפחות 8 תווים.", "danger")
    except ValueError as exc:
        flash(_validation_message(str(exc)), "danger")
    except DomainError:
        flash("לא ניתן להשלים את ההרשמה.", "danger")
    return render_template("auth/register.html", roles=_ROLE_LABELS), 400


@bp.get("/login")
def login():
    if session.get("user_id"):
        return redirect(url_for("pages.home"))
    return render_template("auth/login.html")


@bp.post("/login")
def login_submit():
    if session.get("user_id"):
        return redirect(url_for("pages.home"))
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    try:
        with unit_of_work() as (store, users, _tenders):
            user_id = authenticate_user(
                store, users, AuthenticateUser(email=email, password=password)
            )
            record = get_user_by_id(users, user_id)
        if record is None:
            raise RuntimeError("AuthenticateUser found no rm_user row")
        _put_session(record.user_id, record.email, record.display_name, record.role)
        flash("התחברת בהצלחה.", "success")
        return redirect(url_for("pages.home"))
    except InvalidCredentialsError:
        flash("דוא״ל או סיסמה שגויים.", "danger")
        return render_template("auth/login.html"), 401


@bp.get("/logout")
def logout():
    session.clear()
    flash("התנתקת.", "info")
    return redirect(url_for("auth.login"))
