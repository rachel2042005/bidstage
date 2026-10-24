"""Web entry point.

bootstrap.init() runs before anything else imports an HTTPS client (ENV-1).
"""

from __future__ import annotations

import logging
import secrets

from app import bootstrap

bootstrap.init()

from flask import Flask, render_template, session  # noqa: E402

from app.config import ConfigError, load_settings  # noqa: E402
from app.routes.auth import bp as auth_bp  # noqa: E402
from app.routes.bid_search import bp as bid_search_bp  # noqa: E402
from app.routes.bids import bp as bids_bp  # noqa: E402
from app.routes.comparison import bp as comparison_bp  # noqa: E402
from app.routes.formatting import (  # noqa: E402
    format_alpha,
    format_event_day,
    format_local_datetime,
    format_price_share,
    format_shekels,
)
from app.routes.pages import bp as pages_bp  # noqa: E402
from app.routes.tenders import (  # noqa: E402
    EVENT_TYPE_LABELS,
    REQUIREMENT_TYPE_LABELS,
    STATUS_LABELS,
    bp as tenders_bp,
)


def create_app(
    *,
    event_store=None,
    users=None,
    tenders=None,
    bids=None,
    bid_notifier=None,
    bid_search=None,
) -> Flask:
    try:
        settings = load_settings()
        secret = settings.flask_secret_key or "dev-only-not-for-demo"
        db_ready = settings.db_is_configured
    except ConfigError:
        secret = "dev-only-not-for-demo"
        db_ready = False
    app = Flask(__name__, template_folder="app/templates", static_folder="app/static")
    app.config["SECRET_KEY"] = secret
    # The signed cookie survives a restart. This value does not, so an old
    # cookie cannot sign the visitor back in.
    app.config["SESSION_BOOT_ID"] = secrets.token_hex(16)

    if event_store is not None and users is not None:
        if tenders is None:
            from app.projections.tenders import InMemoryTendersProjection

            tenders = InMemoryTendersProjection()
        if bids is None:
            from app.projections.bids import InMemoryBidsProjection

            bids = InMemoryBidsProjection()
        app.extensions["backend"] = (event_store, users, tenders, bids)
    elif not db_ready:
        from app.events.memory import InMemoryEventStore
        from app.projections.bids import InMemoryBidsProjection
        from app.projections.tenders import InMemoryTendersProjection
        from app.projections.users import InMemoryUsersProjection

        app.extensions["backend"] = (
            InMemoryEventStore(),
            InMemoryUsersProjection(),
            InMemoryTendersProjection(),
            InMemoryBidsProjection(),
        )
    if bid_notifier is not None:
        app.extensions["bid_notifier"] = bid_notifier
    if bid_search is not None:
        app.extensions["bid_search"] = bid_search

    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(tenders_bp)
    app.register_blueprint(bids_bp)
    app.register_blueprint(bid_search_bp)
    app.register_blueprint(comparison_bp)

    app.add_template_filter(format_shekels, "shekels")
    app.add_template_filter(format_event_day, "event_day")
    app.add_template_filter(format_local_datetime, "local_dt")
    app.add_template_filter(format_alpha, "alpha_pct")
    app.add_template_filter(format_price_share, "price_pct")

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


_LOG = logging.getLogger("bidstage.search")


def build_bid_search(
    settings,
    *,
    embedder_factory=None,
    store_factory=None,
    index_concepts=None,
):
    """Chroma plus the organizer search port.

    A failed heartbeat logs a warning and returns `UnavailableBidSearch`, so
    the web process still starts. Indexing errors leave search connected to
    whatever is already stored.
    """
    from app.infrastructure.embeddings import build_embedder
    from app.infrastructure.vector_store import ChromaVectorStore
    from app.routes.bid_search import UnavailableBidSearch
    from app.services.bid_concept_index import index_stored_bid_concepts
    from app.services.semantic_bid_search import SemanticBidSearch
    from app.services.semantic_search_service import SemanticSearchService

    embedder_factory = embedder_factory or build_embedder
    store_factory = store_factory or ChromaVectorStore
    index_concepts = index_concepts or index_stored_bid_concepts
    try:
        embedder = embedder_factory(settings)
        store = store_factory(settings, embedder=embedder)
        store.heartbeat()
    except Exception:
        _LOG.warning(
            "Chroma is unreachable; organizer search is unavailable",
            exc_info=True,
        )
        return UnavailableBidSearch()
    try:
        indexed = index_concepts(store)
        if indexed:
            _LOG.info("Indexed %s bid concept(s) for organizer search", indexed)
    except Exception:
        _LOG.warning(
            "Bid concepts were not indexed; search uses what is already stored",
            exc_info=True,
        )
    return SemanticBidSearch(SemanticSearchService(store))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        search = build_bid_search(load_settings())
    except ConfigError:
        _LOG.warning("Settings are incomplete; organizer search is unavailable")
        search = None
    create_app(bid_search=search).run(debug=True)
