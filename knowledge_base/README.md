# Knowledge Base

Source documents embedded into Chroma by `scripts/seed_knowledge_base.py`. See `specs/00-overview.md` §7. `scripts/seed.py` remains the later full rebuild of SQL Server and Chroma (`ENV-6`).

```
rubrics/        one file per requirement type, embedded UNCHUNKED
regulations/    mass-gathering rules, chunked by section (~500–800 tokens, overlapping)
```

| Folder | Collection | Chunking |
| --- | --- | --- |
| `rubrics/` | `rubrics` | None — one document per requirement type |
| `regulations/` | `regulations` | By section, ~500–800 tokens with overlap |

The other two collections, `bid_concepts` and `portfolios`, are populated from
application data rather than from files here.

**Rubrics are never chunked.** A rubric retrieved in fragments can return half a
scoring scale, which would quietly undermine score consistency (`NFR-VEC-3`).

Expected rubric files, one per requirement type: `venue.md`, `content.md`,
`host.md`, `music.md`, `experience.md`. `venue.md` also carries the safety and
capacity anchors, since the VENUE sub-agent absorbed those checks.

Each rubric is Hebrew prose with English frontmatter: `requirement_type` and
`rubric_version`. The seed stores it as one document, id `rubric:{TYPE}`.
Regulation chunks carry `source`, `section` and `chunk_index`. A renamed
heading leaves the old id in place until `scripts/reset_vector_store.py` and
a fresh seed.

Use the `requirement-evaluator` skill when adding a requirement type.

This directory is committed; `chroma-data/` is not, and is rebuilt from here.
