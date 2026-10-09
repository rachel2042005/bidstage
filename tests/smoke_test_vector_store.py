"""Vector store connectivity smoke test.

Standalone:   python tests/smoke_test_vector_store.py
Under pytest: python -m pytest   (pytest.ini collects smoke_test_*.py)

Start the server first — scripts/run_chroma.ps1 — or every test here skips.

Two things are proved. The heartbeat proves Chroma is reachable over HTTP in
server mode. The round trip proves rather more: that the OpenAI embeddings call
survives the filtering proxy (`ENV-1`), that Hebrew survives the trip intact,
and that cosine ranking actually discriminates between Hebrew documents
(`FR-SRCH-5`, `NFR-VEC-4`). A heartbeat alone would pass with a broken
embedding pipeline.

Unlike the event store, the vector store is a derived index, so this test
deletes the documents it wrote.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

# Run as a script, Python puts tests/ on sys.path rather than the repo root, so
# `import app` fails. pytest does not need this (pytest.ini sets pythonpath).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402

from app import bootstrap  # noqa: E402

bootstrap.init()

from app.config import ConfigError, load_settings  # noqa: E402
from app.infrastructure.vector_store import (  # noqa: E402
    BID_CONCEPTS,
    COLLECTIONS,
    DISTANCE_METRIC,
    ChromaVectorStore,
)

# Three unrelated bid extracts. Ranking is only meaningful when the collection
# holds plausible alternatives, so a wrong answer is possible.
DOCUMENTS = {
    "stage": "הפקת טקס פרסים: במה מרכזית, מערכת תאורה ממוחשבת והגברה לאולם בן 2,000 מושבים",
    "catering": "שירותי הסעדה לאירוע — תפריט כשר, 500 מנות, צוות מלצרים ואחראי משמרת",
    "broadcast": "שידור חי ברשת עם שלוש מצלמות, בקרת שידור ועריכה בזמן אמת",
}

# Neither query repeats the wording of its document, so a match needs semantic
# similarity rather than shared tokens.
QUERIES = {
    "תאורה והגברה לבמה": "stage",
    "אוכל כשר לאורחי האירוע": "catering",
}


def _store(*, embeddings: bool = False) -> ChromaVectorStore:
    """Skips rather than fails whenever a dependency is simply absent.

    An API key is only demanded by the tests that actually embed, so the
    Chroma half of this file still runs on a machine without one.
    """
    try:
        settings = load_settings()
    except ConfigError as exc:
        pytest.skip(str(exc))
    if embeddings and not settings.embeddings_are_configured:
        pytest.skip(
            f"The {settings.embedding_provider!r} embedding provider is not "
            f"configured. Set OPENAI_API_KEY, or EMBEDDING_PROVIDER=ollama to "
            f"embed locally."
        )

    store = ChromaVectorStore(settings)
    try:
        store.heartbeat()
    except Exception as exc:  # chromadb raises several unrelated types here
        pytest.skip(
            f"Chroma is not reachable at {settings.chroma_endpoint} ({exc}). "
            f"Start it with scripts/run_chroma.ps1."
        )
    return store


def test_heartbeat() -> None:
    """The server answers over HTTP, which is the only supported mode."""
    assert _store().heartbeat() > 0


def test_collections_are_created_with_cosine() -> None:
    """ensure_collections is idempotent and never leaves Chroma's default L2."""
    store = _store()

    assert sorted(store.ensure_collections()) == sorted(COLLECTIONS)
    assert sorted(store.ensure_collections()) == sorted(COLLECTIONS)

    for name in COLLECTIONS:
        assert store.distance_metric(name) == DISTANCE_METRIC, (
            f"{name} was not created with {DISTANCE_METRIC}; its vectors would "
            f"rank by squared L2 instead."
        )


def test_hebrew_round_trip() -> None:
    """Embed Hebrew, store it, query it back, and check the ranking."""
    store = _store(embeddings=True)
    store.ensure_collections()

    run = uuid.uuid4().hex[:8]
    ids = {key: f"smoke-{run}-{key}" for key in DOCUMENTS}

    try:
        store.upsert(
            BID_CONCEPTS,
            ids=[ids[key] for key in DOCUMENTS],
            documents=[DOCUMENTS[key] for key in DOCUMENTS],
            metadata=[{"smoke_run": run, "kind": key} for key in DOCUMENTS],
        )

        for query, expected in QUERIES.items():
            matches = store.query(
                BID_CONCEPTS, query, k=1, where={"smoke_run": run}
            )
            assert matches, f"no match for {query!r}"
            top = matches[0]
            assert top.id == ids[expected], (
                f"{query!r} matched {top.document!r} instead of "
                f"{DOCUMENTS[expected]!r}"
            )
            # Hebrew must survive encoding end to end, not merely rank well.
            assert top.document == DOCUMENTS[expected]
            assert top.metadata["kind"] == expected
            assert 0.0 <= top.distance <= 2.0, "cosine distance out of range"
    finally:
        store.delete(BID_CONCEPTS, list(ids.values()))


def _run_as_script() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"FAILED  configuration error: {exc}")
        return 2
    store = ChromaVectorStore(settings)
    print(f"Connecting to Chroma at {settings.chroma_endpoint} ...")
    try:
        print(f"Heartbeat: {store.heartbeat()}")
    except Exception as exc:
        print(f"SKIPPED  Chroma is not reachable: {exc}")
        print("         Start it with scripts/run_chroma.ps1, then re-run this.")
        return 2

    names = store.ensure_collections()
    print(f"Collections: {', '.join(names)}")
    for name in names:
        metric = store.distance_metric(name)
        verdict = "ok" if metric == DISTANCE_METRIC else "WRONG"
        print(f"  {name:<14} metric={metric}  [{verdict}]")

    # The Chroma half is proved above and needs no provider. Only the
    # embedding round trip does.
    if not settings.embeddings_are_configured:
        print(f"\nSKIPPED  the {settings.embedding_provider!r} embedding provider")
        print("         is not configured, so the Hebrew embed and query round")
        print("         trip could not run.")
        return 2

    run = uuid.uuid4().hex[:8]
    ids = {key: f"smoke-{run}-{key}" for key in DOCUMENTS}
    failures = []

    try:
        print(f"\nEmbedding {len(DOCUMENTS)} Hebrew documents via "
              f"{settings.embedding_provider}/{settings.embedding_model} "
              f"({settings.embedding_dim} dims) at "
              f"{settings.embedding_endpoint} ...")
        store.upsert(
            BID_CONCEPTS,
            ids=[ids[key] for key in DOCUMENTS],
            documents=[DOCUMENTS[key] for key in DOCUMENTS],
            metadata=[{"smoke_run": run, "kind": key} for key in DOCUMENTS],
        )

        print("\n--- queries ---")
        for query, expected in QUERIES.items():
            matches = store.query(
                BID_CONCEPTS, query, k=1, where={"smoke_run": run}
            )
            if not matches:
                failures.append(f"no match for {query!r}")
                continue
            top = matches[0]
            verdict = "ok" if top.id == ids[expected] else "WRONG"
            if verdict == "WRONG":
                failures.append(f"{query!r} matched {top.metadata.get('kind')!r}")
            print(f"  query    : {query}")
            print(f"  matched  : {top.document}")
            print(f"  distance : {top.distance:.4f}  [{verdict}]\n")
    except Exception as exc:
        print(f"FAILED  {type(exc).__name__}: {exc}")
        return 1
    finally:
        store.delete(BID_CONCEPTS, list(ids.values()))

    if failures:
        print("FAILED  " + "; ".join(failures))
        return 1

    print("SUCCESS  Hebrew embed, store and semantic query all round-tripped.")
    return 0


if __name__ == "__main__":
    sys.exit(_run_as_script())
