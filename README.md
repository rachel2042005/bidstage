# BidStage

A tender management system for selecting production suppliers for cultural and
sports events. Bids are assessed on quality by an AI agent that cannot see
prices; price is opened and scored deterministically only after every bid has
been assessed.

Built on Event Sourcing and CQRS. The specifications in `specs/` are written
before the code and are the source of truth — start with
[`specs/00-overview.md`](specs/00-overview.md).

Interface is Hebrew and right-to-left; code and identifiers are English.

## Prerequisites

- Python 3.13 in a `.venv`
- ODBC Driver 17 or 18 for SQL Server
- A SQL Server database on Somee, plus OpenAI and Tavily API keys

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env   # then fill in the credentials
python init_db.py             # idempotent; creates the events table
```

On this network an HTTPS-filtering proxy re-signs certificates, so Python
rejects `api.openai.com` out of the box. Every entry point calls
`bootstrap.init()` first, which routes verification through the Windows trust
store. Never work around a certificate error by disabling verification.

## Running

Three processes, started in this order.

```powershell
.\scripts\run_chroma.ps1   # 1. vector store on :8000 — leave this open
python main.py             # 2. Flask web application
python agent\runner.py     # 3. evaluation agent (later phase)
```

Chroma must be its own process: it is not process-safe, and Flask and the agent
both use it. Embedded `PersistentClient` mode is forbidden.

## Embeddings

Two providers, selected by `EMBEDDING_PROVIDER` in `.env`. Both index Hebrew
natively, which is what rules out English-only models.

| Provider | Model | Dim | Needs |
| --- | --- | --- | --- |
| `openai` (default) | `text-embedding-3-small` | 1536 | `OPENAI_API_KEY` |
| `ollama` | `qwen3-embedding:0.6b` | 1024 | Ollama running locally, no key |

To embed locally instead, install [Ollama](https://ollama.com), pull the model,
and set the provider:

```powershell
ollama pull qwen3-embedding:0.6b
# in .env: EMBEDDING_PROVIDER="ollama"
#          and remove EMBEDDING_MODEL / EMBEDDING_DIM so the defaults apply
python scripts\reset_vector_store.py
```

The reset is required, not optional. Vectors produced by two different models
cannot be compared, and the failure is invisible — search just returns the
wrong documents. Each collection records the model that built it, and the
store refuses to serve one built by a different model.

## Tests

```powershell
python -m pytest
```

Smoke tests skip, rather than fail, when the database or the Chroma server is
unavailable — so read the skip messages before trusting a green run. Each can
also run on its own for a verbose report:

```powershell
python tests\smoke_test_event_store.py
python tests\smoke_test_vector_store.py
```

## Layout

| Path | Contents |
| --- | --- |
| `specs/` | Numbered specifications, written before the code |
| `app/events/` | Append-only event store |
| `app/commands/`, `app/queries/` | CQRS write and read sides |
| `app/domain/` | Aggregates and scoring; no I/O, no LLM |
| `app/projections/` | Read models rebuilt from the event stream |
| `app/infrastructure/` | Adapters to outside systems; the only `chromadb` import |
| `agent/` | Deep Agent orchestrator and per-requirement sub-agents |
| `mcp_server/` | The project's two own FastMCP tools |
| `knowledge_base/` | Rubrics and regulations, embedded by the seed script |
