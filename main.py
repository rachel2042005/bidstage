"""Web entry point.

bootstrap.init() runs before anything else imports an HTTPS client (ENV-1).
"""

from __future__ import annotations

from app import bootstrap

bootstrap.init()

from flask import Flask, render_template, session  # noqa: E402

from app.config import ConfigError, load_settings  # noqa: E402
from app.routes.auth import bp as auth_bp  # noqa: E402
from app.routes.pages import bp as pages_bp  # noqa: E402


def create_app(*, event_store=None, users=None) -> Flask:
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
        app.extensions["auth_backend"] = (event_store, users)
    elif not db_ready:
        from app.events.memory import InMemoryEventStore
        from app.projections.users import InMemoryUsersProjection

        app.extensions["auth_backend"] = (
            InMemoryEventStore(),
            InMemoryUsersProjection(),
        )

    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)

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

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
