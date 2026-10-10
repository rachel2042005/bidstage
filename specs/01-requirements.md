# 01 — Requirements

**Status:** Derived from `00-overview.md` after three rounds of design review.
**Traceability:** Course section numbers appear in brackets, e.g. *[4.1]*.

Every requirement has an ID so that `02`–`06` can reference it and `06-tests.md` can prove it.

---

## 1. Scope

BidStage manages the tender process for selecting a production supplier for cultural and sports events. An organizer publishes a tender with weighted requirements; suppliers submit bids answering every requirement plus a sealed price; an autonomous agent scores each bid requirement by requirement; the system then reveals prices, ranks the bids, and the organizer selects a winner.

### Actors *[2–3]*

| ID | Actor | Description |
| --- | --- | --- |
| `ACT-ORG` | Organizer | Publishes tenders, reviews assessments, overrides scores, selects the winner |
| `ACT-SUP` | Supplier | Searches tenders, submits and withdraws bids, tracks status |
| `ACT-AGT` | Evaluation agent | Non-human actor; scores bids. Runs as a separate process |

There is no administrator role. Reference data such as the approved host list is seeded, not managed through the UI.

### Out of scope

Administrator role and supplier approval workflow; payment or contracting after award; multi-round or negotiated tenders; supplier-to-supplier visibility of bids; cloud deployment of the Flask application.

---

## 2. Functional Requirements

### 2.1 Identity and access *[2–3]*

| ID | Requirement |
| --- | --- |
| `FR-AUTH-1` | A visitor can register as an organizer or as a supplier. Role is chosen at registration and is immutable afterwards. |
| `FR-AUTH-2` | Passwords are stored only as salted hashes. Plaintext passwords are never persisted or logged. |
| `FR-AUTH-3` | A user can log in and log out. Session state identifies the user and their role. |
| `FR-AUTH-4` | Every route is authorized by role. A supplier cannot reach organizer routes, and vice versa. |
| `FR-AUTH-5` | A supplier can read only their own bids. A supplier can never read another supplier's bid content, price, or assessment — including after award. |
| `FR-AUTH-6` | An organizer can read bids only for tenders they own. |

**Acceptance:** requesting another supplier's bid by direct URL returns 403, not 404 with content.

### 2.2 Search *[4.1]*

| ID | Requirement |
| --- | --- |
| `FR-SRCH-1` | A supplier can list open tenders filtered by event type (culture/sports), event date range, and region. |
| `FR-SRCH-2` | Filters combine conjunctively, and an empty filter set returns all open tenders. |
| `FR-SRCH-3` | A supplier can search tenders semantically by free text, matched against tender descriptions. |
| `FR-SRCH-4` | An organizer can search semantically **within the bids** submitted to their own tenders, e.g. "bids including a tribute segment". |
| `FR-SRCH-5` | Semantic search works for Hebrew query text against Hebrew bid content. |
| `FR-SRCH-6` | Semantic results over bids are restricted to tenders the requesting organizer owns, enforced before results are returned. |

### 2.3 Detail views *[4.2]*

| ID | Requirement |
| --- | --- |
| `FR-DET-1` | A tender detail page shows the event metadata, the submission deadline, α, and every requirement with its type, weight and threshold flag. |
| `FR-DET-2` | A bid detail page shows, per requirement, the agent's score (0–10), its written justification, and the sources it consulted as links. |
| `FR-DET-3` | A bid detail page shows the overall quality score, and the price and price score **only after** prices have been revealed. |
| `FR-DET-4` | If a bid was disqualified, the page states which threshold requirement caused it. |
| `FR-DET-5` | Where a score was overridden by the organizer, both the agent's original score and the override with its justification are shown. |

### 2.4 Comparison table *[4.3]*

| ID | Requirement |
| --- | --- |
| `FR-TBL-1` | An organizer sees a table of all bids for one tender: supplier, quality score, price, price score, final score, status. |
| `FR-TBL-2` | The table is sortable by quality, price and final score. |
| `FR-TBL-3` | Disqualified bids are shown but visually distinguished and excluded from ranking. |
| `FR-TBL-4` | Before prices are revealed, the price columns render as "sealed" rather than empty. |

### 2.5 Dashboard *[4.4]*

| ID | Requirement |
| --- | --- |
| `FR-DASH-1` | An organizer dashboard groups their tenders by status: open for bids, under evaluation, decided, failed, cancelled. |
| `FR-DASH-2` | Each tender shows its number of submitted bids. |
| `FR-DASH-3` | Each open tender shows a live countdown to its submission deadline. |
| `FR-DASH-4` | A supplier dashboard lists their own bids with current status per bid. |
| `FR-DASH-5` | Dashboard figures are read from a projected read model, never computed by replaying events on request. |

### 2.6 Data entry *[4.5]*

| ID | Requirement |
| --- | --- |
| `FR-ENT-1` | An organizer can create a tender with name, type, event date, region, estimated budget, submission deadline and α. |
| `FR-ENT-2` | An organizer can add requirements to a draft tender, each with type, weight and threshold flag. |
| `FR-ENT-3` | A tender cannot be published unless its requirement weights sum to exactly 100%. |
| `FR-ENT-4` | A tender cannot be published with zero requirements, or with a deadline in the past. |
| `FR-ENT-5` | A supplier can submit a bid containing a response to **every** requirement, a free-text concept, and a price. Partial bids are rejected. |
| `FR-ENT-6` | A bid price is sealed on submission and is not readable by anyone — organizer or agent — until prices are revealed. |
| `FR-ENT-7` | A supplier can withdraw their bid while the tender is still open for bids, and not after. |
| `FR-ENT-8` | A bid cannot be submitted after the deadline, and a supplier may hold at most one active bid per tender. |

### 2.7 Evaluation and award

| ID | Requirement |
| --- | --- |
| `FR-EVAL-1` | When the deadline passes, the tender closes for bids and moves to under evaluation. |
| `FR-EVAL-2` | The agent scores each requirement of each bid, recording score, justification and sources. |
| `FR-EVAL-3` | A bid failing any threshold requirement is disqualified and is not scored further. |
| `FR-EVAL-4` | A bid whose quality score is below 60 is disqualified. |
| `FR-EVAL-5` | Prices are revealed only once every bid has reached a terminal assessment state. |
| `FR-EVAL-6` | Final ranking is computed in deterministic code after reveal. |
| `FR-EVAL-7` | An organizer can override any requirement score, with a mandatory justification, which triggers recomputation. |
| `FR-EVAL-8` | An organizer selects the winner explicitly; the system never awards automatically. |
| `FR-EVAL-9` | If no bid qualifies, the tender becomes failed and no prices are revealed. |
| `FR-EVAL-10` | An organizer can trigger evaluation immediately via an "Evaluate Now" action instead of waiting for the poll. |

### 2.8 Notifications *[9]*

| ID | Requirement |
| --- | --- |
| `FR-NOT-1` | A supplier receives email confirmation when their bid is received. |
| `FR-NOT-2` | Suppliers are emailed when a tender they bid on closes for bids. |
| `FR-NOT-3` | On award, the winner and the non-winners receive distinct emails. |
| `FR-NOT-4` | Email failure never blocks or rolls back a domain command. |

---

## 3. Non-Functional Requirements

| ID | Requirement |
| --- | --- |
| `NFR-VEC-1` *[6]* | Bid concepts, supplier portfolios, scoring rubrics and regulations are embedded into a vector database for semantic retrieval. |
| `NFR-VEC-2` *[6]* | Four separate collections are used: `bid_concepts`, `portfolios`, `rubrics`, `regulations`. |
| `NFR-VEC-3` *[6]* | Rubrics are stored unchunked, one document per requirement type. |
| `NFR-VEC-4` *[6]* | The embedding model is multilingual so Hebrew is indexed natively. Two providers are supported, selected by `EMBEDDING_PROVIDER`: OpenAI `text-embedding-3-small` at 1536 dimensions (default), or local Ollama `qwen3-embedding:0.6b` at 1024 dimensions. An English-only model is never acceptable. |
| `NFR-VEC-5` | The vector store sits behind a thin interface so it can be replaced by configuration. |
| `NFR-VEC-6` | Each collection records the model that built it, and the store refuses to serve a collection built by a different model. Vectors from two models are not comparable, and the failure is otherwise undetectable: search simply returns the wrong documents. |
| `NFR-CQRS-1` *[7]* | All state change flows through commands; all reads flow through queries. The two never share a model. |
| `NFR-CQRS-2` *[7]* | Queries read exclusively from read models, never from the event stream. |
| `NFR-CQRS-3` *[7]* | Read models are projected synchronously inside the command handler that appended the event. |
| `NFR-ES-1` *[8]* | All state is derived from an append-only event stream. |
| `NFR-ES-2` *[8]* | No `UPDATE` or `DELETE` is ever issued against the events table. |
| `NFR-ES-3` *[8]* | Aggregate state is rebuilt by replaying its stream in `Version` order. |
| `NFR-ES-4` *[8]* | Concurrent writes to one aggregate are rejected by `UNIQUE (StreamId, Version)`. |
| `NFR-ES-5` *[8]* | The stream is a complete audit trail: every score, override and reveal is attributable and timestamped. |
| `NFR-MCP-1` *[9]* | Web search is consumed through the Tavily MCP server. |
| `NFR-MCP-2` *[9]* | Email is sent through the Gmail MCP server. |
| `NFR-TOOL-1` *[10]* | The project exposes exactly two of its own MCP tools, built with FastMCP: `get_tender_requirements` and `submit_requirement_score`. |
| `NFR-AGT-1` *[11]* | The agent is a Deep Agent that plans and delegates to one sub-agent per requirement type. |
| `NFR-AGT-2` *[5]* | The agent runs as a process independent of the web application. |
| `NFR-AGT-3` | Scoring uses structured JSON output against a fixed schema at low temperature, so repeat runs produce comparable scores. |
| `NFR-AGT-4` | The agent never sees a bid price before reveal, and never computes a price score. |
| `NFR-DET-1` | Price scores and final rankings are computed only in deterministic Python, never by an LLM. |
| `NFR-UI-1` | The UI is Hebrew with full RTL layout, built on Bootstrap 5. |
| `NFR-UI-2` | Code, identifiers, comments, schemas, event names and documentation are English. |
| `NFR-SEC-1` | Secrets live in `.env`, which is gitignored and never committed. |

---

## 4. Environment Constraints

Verified on the development machine; see `00-overview.md` §13.

| ID | Constraint |
| --- | --- |
| `ENV-1` | `truststore.inject_into_ssl()` must run before any HTTPS client is constructed, in **both** the web and agent processes. Without it the OpenAI API is unreachable, because the Netspark proxy's certificate fails OpenSSL verification. |
| `ENV-2` | The Somee free database is deleted after 30 days without queries, and is capped at 30 MB data plus 30 MB log. |
| `ENV-3` | Embeddings are never stored in SQL Server; the free tier has no `VECTOR` type and insufficient space. |
| `ENV-4` | Chroma runs in server mode. Two processes must never open one `PersistentClient` path. |
| `ENV-5` | Somee cannot host Flask. The application runs locally against the remote database. |
| `ENV-6` | A single seed script rebuilds both the SQL schema with seed data and the Chroma collections with fresh embeddings. |

---

## 5. Traceability

| Course section | Requirements |
| --- | --- |
| 2–3 users and authentication | `FR-AUTH-1` … `FR-AUTH-6` |
| 4.1 search | `FR-SRCH-1` … `FR-SRCH-6` |
| 4.2 detail views | `FR-DET-1` … `FR-DET-5` |
| 4.3 table | `FR-TBL-1` … `FR-TBL-4` |
| 4.4 dashboard | `FR-DASH-1` … `FR-DASH-5` |
| 4.5 data entry | `FR-ENT-1` … `FR-ENT-8` |
| 5 separate agent process | `NFR-AGT-2` |
| 6 vector database | `NFR-VEC-1` … `NFR-VEC-6` |
| 7 CQRS | `NFR-CQRS-1` … `NFR-CQRS-3` |
| 8 Event Sourcing | `NFR-ES-1` … `NFR-ES-5` |
| 9 external MCP | `NFR-MCP-1`, `NFR-MCP-2`, `FR-NOT-1` … `FR-NOT-4` |
| 10 own tools | `NFR-TOOL-1` |
| 11 Deep Agent | `NFR-AGT-1` |
