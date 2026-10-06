"""Web entry point.

bootstrap.init() runs before anything else imports an HTTPS client (ENV-1).
Routes are registered in later phases; see specs/02-architecture.md §2.
"""

from __future__ import annotations

from app import bootstrap

bootstrap.init()

from flask import Flask  # noqa: E402  (import after truststore injection)

from app.config import load_settings  # noqa: E402


def create_app() -> Flask:
    settings = load_settings()
    app = Flask(__name__, template_folder="app/templates", static_folder="app/static")
    app.config["SECRET_KEY"] = settings.flask_secret_key or "dev-only-not-for-demo"

    # Blueprints from app/routes/ are registered here in Phase 3.
    return app


if __name__ == "__main__":
    create_app().run(debug=True)
