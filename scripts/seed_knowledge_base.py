"""Embed knowledge_base/ into Chroma.

    python scripts/seed_knowledge_base.py
    python scripts/seed_knowledge_base.py --check-only

Rubrics are upserted whole, one document per requirement type (`NFR-VEC-3`).
Regulations are split on `##` sections. A section that fits in 700 tokens stays
one chunk. A longer section is packed from `###` blocks with about 100 tokens
of overlap, and every chunk stays within 800 tokens (`00-overview.md` §7).

Token counts are character lengths. On Hebrew text that is close to cl100k,
which keeps a chunk inside the ceiling instead of over it.

Ids are stable: `rubric:VENUE`, `regulation:mass_events_safety:police_crowd:0`.
Re-running replaces those ids. A renamed section leaves its old id behind;
`scripts/reset_vector_store.py` and a fresh seed are the recovery path.

`scripts/seed.py` remains the later full SQL and Chroma rebuild (`ENV-6`).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import bootstrap  # noqa: E402

bootstrap.init()

from app.config import ConfigError, load_settings  # noqa: E402
from app.infrastructure.embeddings import EmbeddingError  # noqa: E402
from app.infrastructure.vector_store import (  # noqa: E402
    REGULATIONS,
    RUBRICS,
    ChromaVectorStore,
    VectorStoreError,
)

# A section at or under this size is stored whole. Above it, ### blocks are
# packed with overlap. 800 is the hard ceiling from 00-overview.md §7.
_WHOLE_LIMIT = 700
_MAX_TOKENS = 800
_OVERLAP_TOKENS = 100

_RUBRIC_FILES = ("venue.md", "content.md", "host.md", "music.md", "experience.md")
_BANDS = ("9–10", "7–8", "5–6", "3–4", "1–2")
_REQUIRED_SECTIONS = {
    "משטרה ומגבלות קהל": "police_crowd",
    "כבאות ומסלולי מילוט": "fire_egress",
    "רישוי": "licensing",
    "נגישות": "accessibility",
}

_H2 = re.compile(r"^## (?!#)(.+?)\s*$", re.MULTILINE)
_H3 = re.compile(r"^### (.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Record:
    id: str
    document: str
    metadata: dict


def estimate_tokens(text: str) -> int:
    """Character length, a close upper bound on cl100k tokens for Hebrew."""
    return len(text)


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        raise ValueError("rubric is missing frontmatter")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("rubric frontmatter is not closed")
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta


def load_rubrics(root: Path) -> list[Record]:
    folder = root / "rubrics"
    records: list[Record] = []
    for name in _RUBRIC_FILES:
        path = folder / name
        text = path.read_text(encoding="utf-8")
        meta = parse_frontmatter(text)
        requirement_type = meta.get("requirement_type", "")
        version = meta.get("rubric_version", "")
        expected = path.stem.upper()
        if requirement_type != expected:
            raise ValueError(
                f"{name} requirement_type is {requirement_type!r}; expected {expected!r}"
            )
        if version != "1":
            raise ValueError(f"{name} rubric_version is {version!r}; expected '1'")
        for band in _BANDS:
            if f"### {band}" not in text:
                raise ValueError(f"{name} is missing the {band} band")
        if "### 0" not in text or "## כלל הצירוף" not in text:
            raise ValueError(f"{name} is missing the 0 band or the combination rule")
        records.append(
            Record(
                id=f"rubric:{requirement_type}",
                document=text,
                metadata={
                    "requirement_type": requirement_type,
                    "rubric_version": version,
                    "source": name,
                },
            )
        )
    if len(records) != 5:
        raise ValueError(f"expected 5 rubrics, found {len(records)}")
    return records


def split_preface_and_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(_H2.finditer(text))
    if not matches:
        raise ValueError("regulations file has no ## sections")
    preface = text[: matches[0].start()].strip()
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(1).strip(), text[start:end].strip()))
    return preface, sections


def split_units(body: str) -> list[str]:
    matches = list(_H3.finditer(body))
    if not matches:
        parts = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
        return parts or [body.strip()]
    units: list[str] = []
    lead = body[: matches[0].start()].strip()
    if lead:
        units.append(lead)
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        units.append(body[start:end].strip())
    return units


# Repeated on every chunk so a retrieved fragment still says the numbers are
# this corpus's thresholds. The file's title block is not copied onto each
# chunk; doing so pushed every section over the ceiling and cut sentences.
_CHUNK_LABEL = (
    "מאגר לימוד של BidStage. כל מספר כאן הוא סף של המאגר ולא ציטוט של חיקוק."
)


def _overhead(heading: str, overlap: str) -> int:
    blocks = [_CHUNK_LABEL, f"## {heading}"]
    if overlap.strip():
        blocks.append(overlap.strip())
    return estimate_tokens("\n\n".join(blocks) + "\n\n")


def _windows(text: str, budget: int) -> list[str]:
    """Cut one block on word boundaries. Overlap is added later, once."""
    remaining = text.strip()
    if budget < 50:
        raise ValueError("chunk budget is too small for the section heading")
    parts: list[str] = []
    while remaining:
        if estimate_tokens(remaining) <= budget:
            parts.append(remaining)
            break
        window = remaining[:budget]
        cut = window.rfind(" ")
        if cut < budget // 2:
            cut = budget
        piece = remaining[:cut].strip()
        if not piece:
            raise ValueError("could not split an oversized regulation block")
        parts.append(piece)
        remaining = remaining[cut:].strip()
    return parts


def _render(heading: str, overlap: str, parts: list[str]) -> str:
    blocks = [_CHUNK_LABEL, f"## {heading}"]
    if overlap.strip():
        blocks.append(overlap.strip())
    blocks.extend(part.strip() for part in parts if part.strip())
    return "\n\n".join(blocks) + "\n"


def _tail(text: str, tokens: int) -> str:
    text = text.strip()
    if estimate_tokens(text) <= tokens:
        return text
    start = max(0, len(text) - tokens)
    space = text.find(" ", start)
    if space != -1 and space < len(text) - 1:
        start = space + 1
    return text[start:].strip()


def chunk_section(heading: str, body: str) -> list[str]:
    units = split_units(body)
    whole = _render(heading, "", units)
    if estimate_tokens(whole) <= _WHOLE_LIMIT:
        return [whole]

    budget = _MAX_TOKENS - _overhead(heading, "x" * _OVERLAP_TOKENS)
    pieces: list[str] = []
    for unit in units:
        pieces.extend(_windows(unit, budget))

    chunks: list[str] = []
    current: list[str] = []
    overlap = ""
    for piece in pieces:
        trial = _render(heading, overlap, current + [piece])
        if current and estimate_tokens(trial) > _MAX_TOKENS:
            chunks.append(_render(heading, overlap, current))
            overlap = _tail(current[-1], _OVERLAP_TOKENS)
            current = []
        current.append(piece)
    if current:
        chunks.append(_render(heading, overlap, current))
    return chunks


def load_regulations(root: Path) -> list[Record]:
    path = root / "regulations" / "mass_events_safety.md"
    text = path.read_text(encoding="utf-8")
    preface, sections = split_preface_and_sections(text)
    if "מאגר לימוד" not in preface or "לא ציטוט" not in preface:
        raise ValueError(
            "mass_events_safety.md must open by labelling its numbers as "
            "corpus thresholds, not statutory quotations"
        )
    found = {heading: slug for heading, slug in _REQUIRED_SECTIONS.items()}
    headings = [heading for heading, _body in sections]
    if set(headings) != set(found):
        raise ValueError(
            "mass_events_safety.md sections are "
            f"{headings}; expected {list(found)}"
        )

    records: list[Record] = []
    for heading, body in sections:
        slug = found[heading]
        chunks = chunk_section(heading, body)
        for index, chunk in enumerate(chunks):
            if estimate_tokens(chunk) > _MAX_TOKENS:
                raise ValueError(
                    f"{heading} chunk {index} is {estimate_tokens(chunk)} tokens; "
                    f"the ceiling is {_MAX_TOKENS}"
                )
            records.append(
                Record(
                    id=f"regulation:mass_events_safety:{slug}:{index}",
                    document=chunk,
                    metadata={
                        "source": path.name,
                        "section": heading,
                        "chunk_index": index,
                    },
                )
            )
        if len(chunks) > 1:
            for index in range(len(chunks) - 1):
                # One copy of the previous tail opens the next chunk. A second
                # copy means the cutter and the packer both added overlap.
                tail = _tail(chunks[index], _OVERLAP_TOKENS)
                if len(tail) < 40 or chunks[index + 1].count(tail) != 1:
                    raise ValueError(
                        f"{heading} chunks {index} and {index + 1} do not overlap once"
                    )
    if len(records) < 4:
        raise ValueError("expected at least one chunk per regulation section")
    return records


def _check(rubrics: list[Record], regulations: list[Record]) -> None:
    if any(record.id.count(":") != 1 for record in rubrics):
        raise ValueError("rubric ids must be rubric:{TYPE}")
    print(f"check    {len(rubrics)} rubrics, each one document")
    by_section: dict[str, int] = {}
    for record in regulations:
        by_section[record.metadata["section"]] = by_section.get(
            record.metadata["section"], 0
        ) + 1
        print(
            f"         {record.id}  {estimate_tokens(record.document)} tokens"
        )
    print(
        "check    "
        + ", ".join(f"{name}={count}" for name, count in by_section.items())
    )


def _upsert(store: ChromaVectorStore, collection: str, records: list[Record]) -> None:
    store.upsert(
        collection,
        [record.id for record in records],
        [record.document for record in records],
        [record.metadata for record in records],
    )


def _verify(
    store: ChromaVectorStore, rubrics: list[Record], regulations: list[Record]
) -> None:
    for record in rubrics:
        matches = store.query(
            RUBRICS,
            record.document[:400],
            k=1,
            where={"requirement_type": record.metadata["requirement_type"]},
        )
        if len(matches) != 1 or matches[0].id != record.id:
            raise VectorStoreError(
                f"{record.id} was not returned from the rubrics collection"
            )
        if matches[0].document != record.document:
            raise VectorStoreError(
                f"{record.id} came back as a fragment "
                f"({len(matches[0].document)} chars, stored {len(record.document)})"
            )
        if matches[0].metadata.get("rubric_version") != "1":
            raise VectorStoreError(f"{record.id} is missing rubric_version")
        print(f"verified {record.id} whole ({len(record.document)} chars)")

    # Chroma allows one operator per where clause. One source filter returns
    # the whole regulations corpus; id and metadata are checked on the results.
    matches = store.query(
        REGULATIONS,
        "תקנות בטיחות אירועי המונים קהל כבאות רישוי נגישות",
        k=len(regulations),
        where={"source": regulations[0].metadata["source"]},
    )
    by_id = {match.id: match for match in matches}
    if len(by_id) != len(regulations):
        raise VectorStoreError(
            f"regulations query returned {len(by_id)} of {len(regulations)} chunks"
        )
    for record in regulations:
        match = by_id.get(record.id)
        if match is None:
            raise VectorStoreError(
                f"{record.id} was not returned from the regulations collection"
            )
        if match.document != record.document:
            raise VectorStoreError(f"{record.id} text does not match what was stored")
        if match.metadata.get("section") != record.metadata["section"]:
            raise VectorStoreError(f"{record.id} section metadata does not match")
        if int(match.metadata.get("chunk_index", -1)) != record.metadata["chunk_index"]:
            raise VectorStoreError(f"{record.id} chunk_index metadata does not match")
        print(f"verified {record.id}")


def main(argv: list[str] | None = None) -> int:
    check_only = "--check-only" in (argv if argv is not None else sys.argv[1:])
    root = _REPO_ROOT / "knowledge_base"
    try:
        rubrics = load_rubrics(root)
        regulations = load_regulations(root)
        _check(rubrics, regulations)
    except (OSError, ValueError) as exc:
        print(f"FAILED  {exc}")
        return 1

    if check_only:
        print("check    chunking only; nothing was embedded")
        return 0

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"FAILED  configuration error: {exc}")
        return 2
    if not settings.embeddings_are_configured:
        print("FAILED  embeddings are not configured. Set OPENAI_API_KEY, or")
        print("        EMBEDDING_PROVIDER=ollama for the local model.")
        return 2

    store = ChromaVectorStore(settings)
    try:
        store.heartbeat()
    except Exception as exc:
        print(f"FAILED  Chroma is not reachable at {settings.chroma_endpoint}: {exc}")
        print("        Start it with scripts/run_chroma.ps1, then re-run this.")
        return 2

    print(
        f"Seeding {settings.embedding_provider}/{settings.embedding_model} "
        f"({settings.embedding_dim} dims) ..."
    )
    try:
        store.ensure_collections()
        _upsert(store, RUBRICS, rubrics)
        _upsert(store, REGULATIONS, regulations)
        _verify(store, rubrics, regulations)
    except (EmbeddingError, VectorStoreError) as exc:
        print(f"FAILED  {exc}")
        return 1

    print(
        f"\nDone. {len(rubrics)} rubrics and {len(regulations)} regulation "
        f"chunks are indexed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
