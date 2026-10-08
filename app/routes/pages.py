"""Placeholder dashboards so role gates have a real URL to protect (FR-AUTH-4)."""

from __future__ import annotations

from flask import Blueprint, render_template

from app.domain.user import ORGANIZER, SUPPLIER
from app.routes.access import require_role

bp = Blueprint("pages", __name__)


@bp.get("/")
def home():
    return render_template("home.html")


@bp.get("/organizer/")
@require_role(ORGANIZER)
def organizer_home():
    return render_template("organizer/home.html")


@bp.get("/supplier/")
@require_role(SUPPLIER)
def supplier_home():
    return render_template("supplier/home.html")
