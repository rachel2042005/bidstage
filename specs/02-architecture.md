# 02 — Architecture

**Status:** Derived from `00-overview.md`. Satisfies `NFR-CQRS-*`, `NFR-ES-*`, `NFR-VEC-5`, `NFR-AGT-2`.

Signatures and DDL below are **contracts**, not implementation. They define what the code must satisfy.

---

## 1. Processes

Three processes run side by side. Keeping them separate is what satisfies *[5]*, and what forces Chroma into server mode (`ENV-4`).

| Process | Command | Responsibility |
| --- | --- | --- |
| Web | `flask run` | MVC UI, command handlers, query handlers, projections |
| Agent | `python -m agent.runner` | Deep Agent; polls for closed tenders and scores bids |
| Vector store | `chroma run --path ./chroma-data --port 8000` | Serves both processes over HTTP |

```
┌──────────────┐  commands/queries  ┌──────────────────────┐
│  Browser     │ ─────────────────▶ │  Web (Flask)         │
│  Hebrew/RTL  │ ◀───────────────── │  MVC + CQRS          │
└──────────────┘                    └───────┬──────────────┘
                                            │ pyodbc
                                            ▼
                                 ┌──────────────────────┐
                                 │ SQL Server (Somee)   │
                                 │ Events + ReadModels  │
                                 └──────────▲───────────┘
                                            │ pyodbc
┌──────────────────────┐  own MCP tools     │
│  Agent process       │  + rm_bid_for_     │
│  deepagents          │    assessment      │
│  5 sub-agents        │ ───────────────────┘
└───────┬──────────────┘ ──▶ Tavily MCP (web search)
        │ HttpClient
        ▼
┌──────────────────────┐
│ Chroma server :8000  │
└──────────────────────┘

Gmail MCP (notifications) ◀── Web process only
```

The agent never connects to the web process. It reads its work queue and bid content from one purpose-built read model, `rm_bid_for_assessment`, and otherwise touches domain state only through the two MCP tools in §7. That read model **has no price column**, so `NFR-AGT-4` holds structurally: the price is absent from the agent's only source of bid data rather than merely unread. Writes go solely through `submit_requirement_score`.

Gmail is driven by the web process, not the agent, so notifications are tied to committed commands.

### Startup order

1. Chroma server.
2. `truststore.inject_into_ssl()` — the **first** statement in both the web and agent entry points, before any client object exists (`ENV-1`).
3. Web and agent, in either order.

---

## 2. Layering

MVC supplies the outer shell; CQRS splits the middle.

```
app/templates/ + app/static/  ← View       (Jinja2, Bootstrap 5 RTL)
app/routes/                   ← Controller (thin: parse, authorize, dispatch)
        │
        ├── app/commands/     ← write side: validate, append events, project
        └── app/queries/      ← read side: SELECT from read models only
                │
app/domain/                   ← aggregates, invariants, scoring (no I/O)
app/events/                   ← event definitions + event store
app/projections/              ← read models built from events
```

Controllers contain no business logic. They authorize, build a command or query object, dispatch it, and render. A controller that computes a score or decides a disqualification is a defect.

`app/domain/` imports nothing that performs I/O. Aggregates are pure functions over event lists, which is what makes `NFR-ES-3` and the unit tests in `06-tests.md` cheap.

### Folder layout

As built in Phase 2:

```
main.py                       # web entry point — bootstrap.init() runs first
init_db.py                    # idempotent events table creation
requirements.txt
pytest.ini
.env / .env.example
app/
  bootstrap.py                # truststore injection + dotenv (ENV-1)
  config.py                   # Settings from .env
  commands/                   # write side
  queries/                    # read side
  events/
    __init__.py               # re-exports EventStore, NewEvent, StoredEvent
    db.py                     # pyodbc connection + transaction()
    schema.py                 # events DDL and introspection
    event_store.py            # append-only EventStore
    event_types.py            # event name constants
  projections/                # read models, incl. rm_bid_for_assessment
  routes/                     # Flask blueprints
  domain/                     # Phase 3: aggregates, scoring
  templates/base.html         # Hebrew RTL shell
  static/css/app.css
agent/
  runner.py                   # poll loop + Evaluate Now consumer
  orchestrator.py             # Phase 4: Deep Agent planner
  subagents/                  # Phase 4: one per requirement type
mcp_server/
  server.py                   # the two own FastMCP tools
knowledge_base/
  rubrics/  regulations/      # embedded by the seed script
scripts/
  seed.py                     # Phase 3: rebuilds SQL + Chroma (ENV-6)
tests/
  smoke_test_event_store.py
specs/
```

The FastMCP tools live in a top-level `mcp_server/` rather than under `agent/`, because they are a boundary the agent consumes rather than a part of it.

---

## 3. Write path

Every state change follows one path. There are no exceptions, which is the first project rule.

```
Controller
  → Command object
  → CommandHandler
      1. load aggregate by replaying its stream
      2. check invariants           → reject on violation
      3. append new event(s)        → UNIQUE(stream_id, version) guards concurrency
      4. project affected read models   (same transaction)
      5. enqueue side effects           (email; after commit)
  → redirect / render
```

Steps 3 and 4 share one transaction, so a read model can never reflect an event that was rolled back (`NFR-CQRS-3`). Step 5 sits outside it, so a Gmail failure cannot roll back a submitted bid (`FR-NOT-4`).

### Commands

`PublishTender` · `AddRequirement` · `SubmitBid` · `WithdrawBid` · `CloseTender` · `ScoreRequirement` · `RevealPrices` · `OverrideScore` · `SelectWinner` · `CancelTender`

`ScoreRequirement` is issued by the agent through `submit_requirement_score`; the rest originate from the UI. `RevealPrices` is issued by the web process only — never the agent.

---

## 4. Read path

```
Controller → Query object → QueryHandler → SELECT from one read model → ViewModel
```

A query handler that opens the events table is a defect (`NFR-CQRS-2`).

### Queries and their read models

| Query | Read model |
| --- | --- |
| `SearchTenders` | `rm_tender_search` |
| `GetTenderDetails` | `rm_tender_detail`, `rm_requirement` |
| `GetBidComparison` | `rm_bid_ranking` |
| `GetDashboardStats` | `rm_dashboard` |
| `GetBidAssessment` | `rm_requirement_score` |

`rm_dashboard` holds bid counts and status per tender so `FR-DASH-5` holds. Deadline countdowns are rendered client-side from the stored deadline; the server stores no ticking value.

One further projection serves no query: `rm_bid_for_assessment` is the agent's work queue and bid-content source (`05-agent-spec.md` §2). It carries `bid_id`, `tender_id`, `concept` and the per-requirement responses, and deliberately **omits price**.

---

## 5. Event store

One append-only table (`NFR-ES-1`, `NFR-ES-2`).

```sql
CREATE TABLE dbo.events (
    global_seq  BIGINT IDENTITY(1,1) NOT NULL,
    event_id    UNIQUEIDENTIFIER     NOT NULL,
    stream_id   VARCHAR(100)         NOT NULL,
    version     INT                  NOT NULL,
    event_type  VARCHAR(100)         NOT NULL,
    event_data  NVARCHAR(MAX)        NOT NULL,
    metadata    NVARCHAR(MAX)        NULL,
    created_at  DATETIME2            NOT NULL
        CONSTRAINT df_events_created_at DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_events PRIMARY KEY CLUSTERED (global_seq),
    CONSTRAINT uq_events_event_id UNIQUE (event_id),
    CONSTRAINT uq_events_stream_version UNIQUE (stream_id, version),
    CONSTRAINT ck_events_version_positive CHECK (version > 0)
);
CREATE INDEX ix_events_event_type ON dbo.events (event_type);
```

Naming is snake_case throughout and the table is `dbo.events`.

`UNIQUE (stream_id, version)` is the optimistic-concurrency mechanism (`NFR-ES-4`): a handler computes the next version from what it replayed, and a concurrent writer that computed the same version fails on insert and retries. No locking, no version column elsewhere. `EventStore` translates that constraint violation into `ConcurrencyError`.

`event_id` is a client-generated UUID, unique across the table, serving as the idempotency key. A writer that times out and retries may reuse the same `event_id`; the duplicate is rejected as `DuplicateEventError` rather than written twice. This is the mechanism behind `submit_requirement_score` being idempotent on `(bid_id, requirement_id)`.

`metadata` carries cross-cutting context — acting user, role, correlation id — kept separate from the domain payload in `event_data`. Domain fields stay in `event_data`; see `03-domain-events.md` §6.

`global_seq` is load-bearing, not decorative. Rebuilding read models needs a total order *across* streams: `version` orders only within a stream, and `created_at` ties when two events share a timestamp. The identity column is the only unambiguous global append order, and `read_all()` pages through it.

`created_at` defaults to `SYSUTCDATETIME()`, so the timestamp comes from the database rather than from whichever process wrote the row. That keeps the audit trail free of client clock skew.

`NVARCHAR(MAX)` is required rather than `VARCHAR` for `event_data` and `metadata`, because payloads carry Hebrew text.

### Module layout

`app/events/event_store.py` holds `EventStore` — `append`, `append_many`, `read_stream`, `read_all`, `last_version` — with `db.py` for connections, `schema.py` for DDL and introspection, and `event_types.py` for event-name constants.

**Size.** Somee's free tier allows 30 MB (`ENV-2`). A tender with five requirements and four bids produces roughly 45 events; justifications dominate the payload at ~1–2 KB each, so a complete tender costs well under 100 KB. The cap permits hundreds of tenders, provided embeddings stay out (`ENV-3`).

Stream identity: one stream per tender, and one per bid. Requirement scores belong to the bid stream.

---

## 6. Vector store interface

A thin port, so the store is replaceable by configuration (`NFR-VEC-5`).

```python
class VectorStore(Protocol):
    def upsert(self, collection: str, ids: list[str],
               documents: list[str], metadata: list[dict]) -> None: ...

    def query(self, collection: str, text: str, k: int,
              where: dict | None = None) -> list[Match]: ...
```

Only `infrastructure/vector_store.py` imports `chromadb`. Sub-agents and query handlers depend on the protocol, so swapping to a hosted store is one new adapter plus a config value.

Collections, content and chunking are specified in `00-overview.md` §7. Embeddings use OpenAI `text-embedding-3-small` at 1536 dimensions (`NFR-VEC-4`); the dimension is recorded in config because changing it invalidates every stored vector.

---

## 7. Own MCP tools

Exactly two, via FastMCP (`NFR-TOOL-1`).

```
get_tender_requirements(tender_id: str) -> {
    tender: { name, type, event_date, region, alpha },
    requirements: [ { id, type, weight, is_threshold, description } ],
    approved_hosts: [ str ]        # present only when a HOST requirement exists
}

submit_requirement_score(
    bid_id: str, requirement_id: str,
    score: int,                    # 0..10
    justification: str,
    sources: list[str]
) -> { accepted: bool, event_version: int }
```

`get_tender_requirements` returns no bid prices, by construction. `approved_hosts` is read from a seeded reference table, which is why a third tool is unnecessary (`FR-EVAL-2`, host list ownership).

`submit_requirement_score` is a command, not a write: it validates the range, appends `RequirementScored`, and projects. It is idempotent on `(bid_id, requirement_id)` — a retry after a timeout must not double-score.

---

## 8. External MCP

| Server | Used by | Purpose |
| --- | --- | --- |
| Tavily | Agent sub-agents | Verifying venue capacity, host availability, performer activity, claimed past events |
| Gmail | Web process | Bid receipt, closure notice, winner and non-winner emails |

Gmail is driven by the web process rather than the agent, so notifications are tied to committed commands.

---

## 9. Configuration

All secrets in `.env` (`NFR-SEC-1`), which is gitignored.

| Variable | Purpose |
| --- | --- |
| `SQLSERVER_CONN` | Somee connection string (ODBC Driver 17) |
| `OPENAI_API_KEY` | Scoring and embeddings |
| `CHROMA_HOST`, `CHROMA_PORT` | Vector store endpoint |
| `EMBEDDING_MODEL`, `EMBEDDING_DIM` | `text-embedding-3-small`, `1536` |
| `TAVILY_API_KEY` | Via Tavily MCP |
| `GMAIL_*` | Gmail MCP credentials |
| `FLASK_SECRET_KEY` | Session signing |
| `AGENT_POLL_SECONDS` | Default `60` |
| `QUALITY_FLOOR` | Fixed at `60`; configurable only to support testing |

---

## 10. Architectural rules

These are the two rules submitted as course deliverables, and they are enforceable by review:

1. **State changes only through a command that emits an event.** No `UPDATE` against `Events`, and no direct mutation of a read model outside a projection.
2. **Price scoring and final ranking only in deterministic Python.** No LLM call participates in arithmetic. `app/domain/scoring.py` imports nothing from `agent/` and makes no network calls.

A third, implied by `ENV-1`: `truststore.inject_into_ssl()` precedes every HTTPS client construction in both entry points.
