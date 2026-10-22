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
└───────┬──────────────┘ ──▶ Tavily HTTP (web search)
        │ HttpClient
        ▼
┌──────────────────────┐
│ Chroma server :8000  │
└──────────────────────┘

Gmail MCP (notifications) ◀── Web process only
```

The agent never connects to the web process. It reads its work queue and bid content from one purpose-built read model, `rm_bid_for_assessment`, and otherwise touches domain state only through the two MCP tools in §7 and the in-process `CompleteAssessment` command. That read model **has no price column**, so `NFR-AGT-4` holds structurally: the price is absent from the agent's only source of bid data rather than merely unread. Requirement scores go solely through `submit_requirement_score`. `BidDisqualified` and `QualityAssessmentCompleted` go through `CompleteAssessment`, which is not an MCP tool. Claiming a bid is an operational take on the assessment queue, not a domain event.

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
        ├── app/queries/      ← read side: SELECT from read models only
        └── app/services/     ← application boundary over VectorStore
                │
app/domain/                   ← aggregates, invariants, scoring (no I/O)
app/events/                   ← event definitions + event store
app/projections/              ← read models built from events
```

Controllers contain no business logic. They authorize, build a command or query object, dispatch it, and render. A controller that computes a score or decides a disqualification is a defect.

`app/domain/` imports nothing that performs I/O. Aggregates are pure functions over event lists, which is what makes `NFR-ES-3` and the unit tests in `06-tests.md` cheap.

`app/services/` holds application services over ports that are not SQL read models. `SemanticSearchService` is that boundary for the vector store: query handlers and sub-agents call it, and it never imports `chromadb` (`NFR-VEC-5`). A query handler still does not open the events table (`NFR-CQRS-2`).

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
  services/
    semantic_search_service.py  # search over VectorStore; no chromadb import
  events/
    __init__.py               # re-exports EventStore, NewEvent, StoredEvent
    db.py                     # pyodbc connection + transaction()
    schema.py                 # events DDL and introspection
    event_store.py            # append-only EventStore
    event_types.py            # event name constants
  projections/                # read models, incl. rm_user and rm_bid_for_assessment
  routes/
    auth.py                   # /register, /login, /logout
    access.py                 # login_required, require_role
    pages.py                  # home + role-gated placeholders
  domain/                     # aggregates (pure replay), scoring
  infrastructure/
    vector_store.py           # Chroma adapter — the only chromadb import
    embeddings.py             # OpenAI / Ollama embedding providers
  templates/base.html         # Hebrew RTL shell
  templates/auth/             # registration and login forms
  static/css/app.css
agent/
  runner.py                   # python -m agent.runner; starts run_forever once the queue exists
  worker.py                   # run_forever: drain, idle sleep, stop flag
  orchestrator.py             # Phase 4: Deep Agent planner
  subagents/                  # Phase 4: one per requirement type
  adapters/
    tavily.py                 # PageSearch over Tavily HTTP
    assessor.py               # one chat client, five assessment schemas
mcp_server/
  server.py                   # the two own FastMCP tools
  __main__.py                 # python -m mcp_server (stdio)
knowledge_base/
  rubrics/  regulations/      # embedded by scripts/seed_knowledge_base.py
scripts/
  run_chroma.ps1              # starts the vector store in server mode
  reset_vector_store.py       # drops collections after a provider change
  seed_knowledge_base.py      # embeds knowledge_base/ into rubrics and regulations
  seed.py                     # Phase 3: rebuilds SQL + Chroma (ENV-6)
tests/
  smoke_test_event_store.py
  test_auth.py
  smoke_test_vector_store.py
  test_semantic_search_service.py
  test_mcp_server.py
specs/
README.md                     # setup and the three-process startup order
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

`RegisterUser` · `AuthenticateUser` · `PublishTender` · `ReviseTenderDetails` · `AddRequirement` · `SubmitBid` · `WithdrawBid` · `CloseTender` · `ScoreRequirement` · `RevealPrices` · `OverrideScore` · `SelectWinner` · `CancelTender`

`CloseTender` appends `TenderClosedForBids` for an open tender. `bid_count` is the number of bids still submitted. When that count is zero it also appends `TenderFailed` with reason `no_bids_submitted` (`FR-EVAL-11`, `T-CMD-22`). The web request does not start the agent. `POST /organizer/tenders/<tender_id>/close` is limited to the owner and redirects to the organizer tender page.

`ScoreRequirement` is issued by the agent through `submit_requirement_score`; the rest originate from the UI. `RevealPrices` is issued by the web process only — never the agent. `SqlScoreRequirementHandler` appends `RequirementScored` on the bid stream and inserts one `rm_requirement_score` row in that same transaction (`T-CMD-20`). Weight is copied from `rm_requirement`. A second command for the same `(bid_id, requirement_id)` appends nothing and returns the stored version (`T-AG-4`). The handler rejects a bid with no `BidSubmitted`, a tender with no `TenderClosedForBids` (`T-INV-2`), and a tender that has `PricesRevealed`, `TenderFailed`, or `TenderCancelled` (`T-INV-4`). It does not read `price_amount`.

`CompleteAssessment` is the in-process command behind `run_once`. Its SQL handler appends `QualityAssessmentCompleted` or `BidDisqualified` on the bid stream and, in that same transaction, projects `rm_bid_assessment`. A qualified completion inserts one `rm_requirement_score` row per requirement in the payload only when that `(bid_id, requirement_id)` is absent (`T-CMD-13`, `T-CMD-21`). An intermediate score already stored by `SqlScoreRequirementHandler` is left as it is. The handler does not read `PriceOfferSealed`. A bid stream that already has either terminal event rejects a second completion (`T-CMD-15`).

`RevealPrices` replays the tender stream and every bid stream whose `BidSubmitted` names that tender. A bid that is not `QualityAssessmentCompleted` or `BidDisqualified` rejects the command and appends nothing (`INV-3`, `T-CMD-16`). When at least one bid is qualified, the handler loads `alpha` from `TenderCreated`, each qualified bid's `price_amount` from `PriceOfferSealed`, and `submitted_at` from `BidSubmitted.created_at`, calls `rank_bids`, and appends `PricesRevealed` then `FinalRankingCalculated`. It projects `rm_bid_ranking` for those qualified bids only. When every bid is terminal and none is qualified, it appends `TenderFailed` with `reason` `no_qualified_bids` and does not append `PricesRevealed` (`T-CMD-18`).

There is no `UserRoleAssigned` command or event. Role is a field of `UserRegistered` and is immutable (`FR-AUTH-1`). An attempt to change it after registration is rejected (`T-AUTH-7`).

`RegisterUser` and `AuthenticateUser` return a `user_id`. Session fields (email, display name, role) are loaded afterwards with `GetUserById` from `rm_user`. Failed logins emit nothing.

### Identity and sessions

The web process uses a Flask signed session cookie (`FLASK_SECRET_KEY`), not JWT. The cookie holds `user_id`, `email`, `display_name` and `role`.

`@require_role("organizer")` / `@require_role("supplier")` live in `app/routes/access.py`. Unauthenticated requests to a gated route redirect to `/login`. A signed-in user with the wrong role receives 403 and no resource content (`FR-AUTH-4`, `T-AUTH-2`).

`rm_user` is the login and authorization lookup (`NFR-CQRS-2`): `user_id`, unique case-insensitive `email`, `display_name`, `role` (`organizer` | `supplier`), `password_hash`, `registered_at`, `last_login_at`. The password column is a salted hash only (`FR-AUTH-2`). `init_db.py` creates this table next to `dbo.events`.

---

## 4. Read path

```
Controller → Query object → QueryHandler → SELECT from one read model → ViewModel
```

A query handler that opens the events table is a defect (`NFR-CQRS-2`).

### Queries and their read models

| Query | Read model |
| --- | --- |
| `GetUserByEmail` / `GetUserById` | `rm_user` |
| `SearchTenders` | `rm_tender_search` |
| `GetTenderDetails` | `rm_tender_detail`, `rm_requirement` |
| `GetBidComparison` | `rm_bid_ranking` |
| `GetDashboardStats` | `rm_dashboard` |
| `GetBidAssessment` | `rm_requirement_score` |
| `GetTenderRequirements` | `rm_tender_detail`, `rm_requirement`, approved-hosts reference table |
| `SearchOrganizerBids` | `rm_dashboard` for the organizer's under-evaluation, decided and failed tenders; `rm_bid_ranking` for the supplier name; the excerpt comes from the bid search port, which is called with those tender ids and never sees a price |

`rm_dashboard` holds bid counts and status per tender so `FR-DASH-5` holds. Deadline countdowns are rendered client-side from the stored deadline; the server stores no ticking value.

These read models have columns now (`T-CMD-13` … `T-CMD-18`, `T-QRY-1`):

| Table | Columns |
| --- | --- |
| `dbo.rm_bid_assessment` | `bid_id`, `tender_id`, `status`, `quality_score`, `reason`, `failed_requirement_id` |
| `dbo.rm_requirement_score` | `bid_id`, `requirement_id`, `score`, `weight` |
| `dbo.rm_bid_ranking` | `tender_id`, `bid_id`, `quality`, `price_amount`, `price_score`, `final_score`, `rank` |
| `dbo.rm_tender_detail` | `tender_id`, `name`, `event_type`, `event_date`, `region`, `alpha` |
| `dbo.rm_requirement` | `tender_id`, `requirement_id`, `position`, `type`, `weight`, `is_threshold`, `description` |
| `dbo.approved_hosts` | `name` |

`position` is the order of `RequirementAdded` on the tender stream. `approved_hosts` is the seeded reference list, one row per name, shared by every tender. `PublishTender` is the writer for the two tender tables; that command is a later slice. `GetTenderRequirements` reads the rows that are already there.

`status` is `qualified` or `disqualified`. `tender_id` is taken from `BidSubmitted` when that event is already on the bid stream. A threshold disqualification leaves `quality_score` null and writes no requirement rows. `rm_bid_ranking` has no row for a disqualified bid.

One further projection serves no query: `rm_bid_for_assessment` is the agent's work queue and bid-content source (`05-agent-spec.md` §2). `enqueue_bid_for_assessment` inserts one `pending` row from `BidSubmitted` fields only. `SqlAssessmentQueue.claim` takes the oldest `pending` row, sets `claimed`, and commits before it returns. `release_stalled_claims(conn, timeout_seconds)` sets `status` back to `pending` and `claimed_at` to null where `status` is `claimed` and `claimed_at` is strictly earlier than `SYSUTCDATETIME()` minus `timeout_seconds` (`T-AG-43`). A claim inside that window stays `claimed` (`T-AG-44`). The caller commits. The function does not read or write `RequirementScored`, and nothing calls it yet. `SqlCompleteAssessmentHandler` sets that row to `completed` in the same transaction as the terminal event; zero rows updated is success. Columns:

| Column | Notes |
| --- | --- |
| `bid_id` | Primary key |
| `tender_id`, `supplier_id`, `concept` | Copied from `BidSubmitted` |
| `responses_json` | The `BidSubmitted` response list. No price |
| `status` | `pending`, `claimed`, or `completed` |
| `queued_at` | Default `SYSUTCDATETIME()`; claim order |
| `claimed_at` | Null until claim; null again when a stalled claim is released |

`GetTenderRequirements` is the read behind the MCP tool in §7. It is separate from `GetTenderDetails`, because that UI query may later carry bids and this one must not be able to. The handler returns `{tender, requirements, approved_hosts}`, or nothing when the tender is absent. `tender.type` is `event_type`. Requirements come back in `position` order. `approved_hosts` is every name in the reference table, ordered by name. The protocol is `app/queries/tender_requirements.py`. `SqlTenderRequirementsQuery.fetch` reads only those three tables (`T-QRY-1`, `NFR-CQRS-2`). The MCP tool's own tests still pass an in-memory query.

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

Stream identity: one stream per user, one per tender, and one per bid. Requirement scores belong to the bid stream. User identity for login is served by `rm_user` (email and role), never by replaying the user stream on each request.

---

## 6. Vector store interface

A thin port, so the store is replaceable by configuration (`NFR-VEC-5`).

```python
class VectorStore(Protocol):
    def upsert(self, collection: str, ids: list[str],
               documents: list[str], metadata: list[dict]) -> None: ...

    def query(self, collection: str, text: str, k: int,
              where: dict | None = None) -> list[Match]: ...

    def get(self, collection: str, ids: list[str]) -> list[Match]: ...
```

`get` loads documents by id and does not embed. Ids that are absent are omitted. `distance` on those matches is unused, because a key lookup is not a ranking. Rubric retrieval depends on this: fetching `rubric:VENUE` must work when the embedding provider is down, and a filtered `query` would embed a dummy string to perform a key lookup.

Only `app/infrastructure/vector_store.py` imports `chromadb`. Sub-agents and query handlers depend on `SemanticSearchService`, which depends on the protocol, so swapping to a hosted store is one new adapter plus a config value.

### Semantic search service

`app/services/semantic_search_service.py` is the application boundary over `VectorStore` (`FR-SRCH-4`, `FR-SRCH-5`, `NFR-VEC-3`).

```python
class SemanticSearchService:
    def search_bids(self, query: str, tender_id: str | None = None,
                    top_k: int = 5) -> list[dict]: ...
    def get_rubric(self, requirement_type: str) -> dict | None: ...
    def search_regulations(self, query: str, top_k: int = 3) -> list[dict]: ...
```

`search_bids` queries `bid_concepts`. When `tender_id` is given, the filter `{"tender_id": tender_id}` is passed into `query`, so the restriction is applied by the store before neighbours are chosen (`FR-SRCH-4`). Omitting `tender_id` searches across tenders. Ownership of those tenders is the caller's job (`FR-SRCH-6`, `T-AUTH-4`); this service takes no organizer id.

`search_bids_for_tenders(query, tender_ids, top_k)` is the same query with `tender_id` either equal to the single id or `{"$in": tender_ids}`. An empty list returns nothing. `SemanticBidSearch` is the object `main.py` stores on `app.extensions["bid_search"]`: it calls that method and returns `bid_id`, `tender_id`, and `excerpt`, which is the port `SearchOrganizerBids` expects. `main.py` builds the embedder, then `ChromaVectorStore`, and checks `heartbeat()` before serving. A failed heartbeat is logged and the extension stays `UnavailableBidSearch`, so the web process still starts. Concepts for under-evaluation, decided, and failed tenders are copied from `rm_bid_for_assessment` into `bid_concepts` when that id is missing. That read model has no price column. Ids already stored are left in place.

`search_regulations` queries `regulations`.

`search_portfolios(query, supplier_id, top_k=5)` is the portfolio port `evaluate_experience` calls (`05` §5, `T-AG-24`). It will query `portfolios` with the filter `{"supplier_id": supplier_id}` applied before neighbours are chosen, and return the same hit shape. `SemanticSearchService` does not implement it yet.

`search_bids` and `search_regulations` return hits `{id, text, metadata, score}` in the store's order (closest first). `score` is cosine similarity, `1 - distance`, where `distance` is the cosine distance the collections are created with. Higher is closer.

`get_rubric` uppercases the requirement type and loads `rubric:{TYPE}` through `get`, one whole document (`NFR-VEC-3`, `T-VEC-3`). The type taxonomy is the closed set `VENUE`, `CONTENT`, `HOST`, `MUSIC`, `EXPERIENCE`; any other value raises `ValueError`. A known type with nothing stored returns `None`. The result is `{id, text, metadata}` — a key lookup has no score.

Collections, content and chunking are specified in `00-overview.md` §7.

### Embedding providers

Two, selected by `EMBEDDING_PROVIDER` (`NFR-VEC-4`). Both index Hebrew natively, which is the requirement that excludes every English-only model.

| Provider | Endpoint | Model | Dim | Key | Notes |
| --- | --- | --- | --- | --- | --- |
| `openai` (default) | `api.openai.com` | `text-embedding-3-small` | 1536 | required | No local process; subject to `ENV-1` |
| `ollama` | `localhost:11434` | `qwen3-embedding:0.6b` | 1024 | none | Free, offline, bid text never leaves the machine |

`app/infrastructure/embeddings.py` is the only importer of the OpenAI SDK for embeddings. One class serves both providers, because Ollama exposes an OpenAI-compatible `/v1/embeddings`; they differ only in base URL, model and key. Model and dimension are derived from the provider rather than configured beside it, so a switch cannot leave a mismatched pair.

**The two are not interchangeable once documents are stored.** Vectors from different models are incomparable, and nothing downstream can detect it — retrieval simply returns the wrong documents. Each collection is therefore stamped with the model that built it, and the store raises rather than serving a collection built by another (`NFR-VEC-6`). `scripts/reset_vector_store.py` clears them; re-seed afterwards.

### Distance metric

All four collections are created with cosine distance (`{"hnsw:space": "cosine"}`), the conventional pairing with OpenAI embeddings. Chroma's own default is squared L2, and the metric is fixed at creation — correcting it later means dropping and re-embedding every collection. Collections are therefore created in exactly one place, `ensure_collections()`, which is idempotent; a collection created ad hoc elsewhere would silently get L2 and return subtly wrong neighbours.

### Where embeddings are computed

In the application process, never by the Chroma server. `vector_store.py` owns an OpenAI client built after `bootstrap.init()` and passes vectors to Chroma explicitly, with `embedding_function=None` on every collection.

Two reasons, both structural. The server is started by a bare `chroma run` that no code of ours wraps, so `truststore.inject_into_ssl()` cannot be injected into it and any outbound call it made would fail against the filtering proxy (`ENV-1`). And leaving the embedding function unset would let Chroma fall back to its bundled ONNX MiniLM model, which is English-only and would degrade Hebrew retrieval without raising anything. A side benefit is that the Chroma server never receives the OpenAI key.

This is also why `sentence-transformers`, and the PyTorch stack behind it, is absent from `requirements.txt`. `onnxruntime` arrives as a transitive dependency of `chromadb` itself, but the bundled embedding function is never constructed and no ONNX model is ever downloaded.

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
    score: int,                    # real int, 0..10 inclusive
    justification: str,
    sources: list[str],
    rubric_version: str
) -> { accepted: bool, event_version: int }
```

`get_tender_requirements(tender_id, query)` copies only the fields in the shape above, so a price column on the view is dropped (`T-AG-2`). `approved_hosts` is attached only when a requirement has type `HOST` (`T-AG-3`); the names themselves come from the seeded reference table, which is why a third tool is unnecessary (`FR-EVAL-2`). A blank `tender_id` raises `ValueError` before the query runs. An unknown id raises `TenderNotFound`, a `LookupError`, and the message includes the id.

FastMCP registers a wrapper per tool. `get_tender_requirements` accepts only `tender_id`, and `submit_requirement_score` accepts `bid_id`, `requirement_id`, `score`, `justification`, `sources`, and `rubric_version`. The query and the command handler stay out of the tool schema. `python -m mcp_server` starts the server on stdio. `bootstrap.init()` is the first statement in `mcp_server/server.py`, and the FastMCP import follows it.

`submit_requirement_score(bid_id, requirement_id, score, justification, sources, rubric_version, handler)` validates the arguments, then dispatches a `ScoreRequirement` command. `rubric_version` is an argument because every `RequirementScored` records it (`03-domain-events.md` §2) and this tool is the only writer; §7 previously omitted it. `tender_id` is not an argument: `BidSubmitted` already stores it, and the handler reads it from the bid stream. `scored_by` is stamped `"agent"`. `sources` stays, because `FR-EVAL-2` and `FR-DET-2` render them; an empty list is accepted here, and `T-AG-7` remains the orchestrator's check against `verifications`. A score must be a real `int` in `0..10` (`T-AG-5`, `T-AG-6`); `bool` is rejected. Blank `bid_id`, `requirement_id`, `justification`, or `rubric_version`, and a blank source entry, raise `ValueError` before the handler runs. The handler's `ScoringRejected` propagates unchanged. Idempotency on `(bid_id, requirement_id)` and `INV-2` / `INV-4` belong to `SqlScoreRequirementHandler` (`T-AG-4`, `T-INV-2`, `T-INV-4`). The tool opens one `transaction()` around that handler.

---

## 8. External MCP

| Server | Used by | Purpose |
| --- | --- | --- |
| Tavily | Agent sub-agents | HTTP search behind `PageSearch`. An empty key returns no pages |
| Gmail | Web process | Bid receipt, closure notice, winner and non-winner emails |

Gmail is driven by the web process rather than the agent, so notifications are tied to committed commands.

---

## 9. Configuration

All secrets in `.env` (`NFR-SEC-1`), which is gitignored.

| Variable | Purpose |
| --- | --- |
| `SOMEE_DB_CONNECTION_STRING` | Somee connection string (ODBC Driver 17) |
| `OPENAI_API_KEY` | Scoring and embeddings |
| `CHROMA_HOST`, `CHROMA_PORT` | Vector store endpoint |
| `EMBEDDING_PROVIDER` | `openai` (default) or `ollama`; selects model and dimension |
| `OLLAMA_BASE_URL` | Read only when the provider is `ollama`; defaults to `http://localhost:11434/v1` |
| `EMBEDDING_MODEL`, `EMBEDDING_DIM` | Optional overrides for an unlisted model. Normally unset |
| `TAVILY_API_KEY` | Tavily HTTP search. Empty returns no pages |
| `AGENT_MODEL_PROVIDER` | `openai` (default) or `ollama`. Chat only; embeddings stay on `EMBEDDING_PROVIDER` |
| `AGENT_MODEL` | Optional chat-model override. Unset uses `gpt-4o-mini` or `qwen3:8b` |
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
