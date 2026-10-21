"""Drop and recreate the four Chroma collections.

    python scripts/reset_vector_store.py

Run this after changing EMBEDDING_PROVIDER or EMBEDDING_MODEL. Vectors from
two different models are not comparable, so the store refuses to serve
collections built by another model; this is how you clear them.

Destroys only derived data. Events in SQL Server are the source of truth and
are never touched (`ENV-6`); re-seed afterwards to repopulate.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import bootstrap  # noqa: E402

bootstrap.init()

from app.config import ConfigError, load_settings  # noqa: E402
from app.infrastructure.vector_store import ChromaVectorStore  # noqa: E402


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"FAILED  configuration error: {exc}")
        return 2

    store = ChromaVectorStore(settings)
    try:
        store.heartbeat()
    except Exception as exc:
        print(f"FAILED  Chroma is not reachable at {settings.chroma_endpoint}: {exc}")
        print("        Start it with scripts/run_chroma.ps1, then re-run this.")
        return 2

    print(
        f"Resetting collections for {settings.embedding_provider}/"
        f"{settings.embedding_model} ({settings.embedding_dim} dims) ..."
    )
    for name in store.reset_collections():
        print(f"  recreated {name}")
    print("\nDone. The collections are empty; re-seed to repopulate them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
