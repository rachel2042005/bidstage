"""Web entry point.

bootstrap.init() runs before anything else imports an HTTPS client (ENV-1).
"""

from __future__ import annotations

from app import bootstrap

bootstrap.init()

from flask import Flask, render_template, session  # noqa: E402

from app.config import ConfigError, load_settings  # noqa: E402
from app.routes.auth import bp as auth_bp  # noqa: E402
from app.routes.formatting import (  # noqa: E402
    format_alpha,
    format_event_day,
    format_local_datetime,
    format_shekels,
)
from app.routes.pages import bp as pages_bp  # noqa: E402
from app.routes.tenders import (  # noqa: E402
    EVENT_TYPE_LABELS,
    REQUIREMENT_TYPE_LABELS,
    STATUS_LABELS,
    bp as tenders_bp,
)


def create_app(*, event_store=None, users=None, tenders=None) -> Flask:
    try:
        settings = load_settings()
        secret = settings.flask_secret_key or "dev-only-not-for-demo"
        db_ready = settings.db_is_configured
    except ConfigError:
        secret = "dev-only-not-for-demo"
        db_ready = False
    app = Flask(__name__, template_folder="app/templates", static_folder="app/static")
    app.config["SECRET_KEY"] = secret

    if event_store is not None and users is not None:
        if tenders is None:
            from app.projections.tenders import InMemoryTendersProjection

            tenders = InMemoryTendersProjection()
        app.extensions["backend"] = (event_store, users, tenders)
    elif not db_ready:
        from app.events.memory import InMemoryEventStore
        from app.projections.tenders import InMemoryTendersProjection
        from app.projections.users import InMemoryUsersProjection

        app.extensions["backend"] = (
            InMemoryEventStore(),
            InMemoryUsersProjection(),
            InMemoryTendersProjection(),
        )

    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(tenders_bp)

    app.add_template_filter(format_shekels, "shekels")
    app.add_template_filter(format_event_day, "event_day")
    app.add_template_filter(format_local_datetime, "local_dt")
    app.add_template_filter(format_alpha, "alpha_pct")

    @app.context_processor
    def inject_labels():
        return {
            "event_type_labels": EVENT_TYPE_LABELS,
            "requirement_type_labels": REQUIREMENT_TYPE_LABELS,
            "status_labels": STATUS_LABELS,
        }

    @app.context_processor
    def inject_current_user():
        user_id = session.get("user_id")
        if not user_id:
            return {"current_user": None}
        return {
            "current_user": {
                "user_id": user_id,
                "email": session.get("email"),
                "display_name": session.get("display_name"),
                "role": session.get("role"),
            }
        }

    @app.errorhandler(403)
    def forbidden(_exc):
        return render_template("403.html"), 403

    @app.errorhandler(404)
    def not_found(_exc):
        return render_template("404.html"), 404

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
