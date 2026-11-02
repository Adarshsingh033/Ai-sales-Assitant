# AI Sales Assistant — Backend

Production-ready FastAPI backend for the AI Sales Assistant multi-tenant SaaS platform.

---

## Technology Stack

| Layer              | Technology                       |
|--------------------|----------------------------------|
| Framework          | FastAPI                          |
| ORM                | SQLAlchemy 2.x (async)           |
| Database           | PostgreSQL                       |
| Migrations         | Alembic                          |
| Config             | Pydantic Settings                |
| Authentication     | JWT (`python-jose`)              |
| Password Hashing   | Argon2 (`argon2-cffi`)           |
| Testing            | pytest + pytest-asyncio          |

---

## Project Structure

```
backend/
├── app/
│   ├── main.py                  ← FastAPI application entry point
│   ├── core/
│   │   ├── config.py            ← Pydantic Settings (reads .env)
│   │   ├── database.py          ← Async SQLAlchemy engine + session
│   │   ├── security.py          ← Argon2 hashing + JWT utilities
│   │   └── dependencies.py      ← FastAPI auth + role dependencies
│   ├── models/
│   │   ├── __init__.py
│   │   ├── tenant.py            ← Tenant model (multi-tenancy foundation)
│   │   └── user.py              ← User model + UserRole enum
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── auth.py              ← Login request/response schemas
│   ├── routers/
│   │   ├── __init__.py
│   │   └── auth.py              ← POST /api/v1/auth/login
│   ├── services/
│   │   ├── __init__.py
│   │   └── auth_service.py      ← Authentication business logic
│   ├── repositories/
│   │   ├── __init__.py
│   │   └── user_repository.py   ← User database queries
│   └── scripts/
│       ├── __init__.py
│       └── seed.py              ← Super Admin seeder
├── alembic/
│   ├── env.py                   ← Async Alembic environment
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial.py      ← Initial migration (tenants + users)
├── tests/
│   ├── conftest.py              ← Pytest fixtures
│   ├── test_auth.py             ← Authentication endpoint tests
│   └── test_users.py            ← User model + seeder tests
├── .env.example                 ← Environment template (safe to commit)
├── alembic.ini
├── pytest.ini
├── requirements.txt
└── README.md
```

---

## Setup Instructions

### 1. Prerequisites

- Python 3.11+
- PostgreSQL 14+ running locally or remotely

### 2. Clone and enter backend directory

```bash
cd backend
```

### 3. Create virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```env
APP_NAME=AI Sales Assistant
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8000

DATABASE_URL=postgresql+asyncpg://postgres:yourpassword@localhost:5432/ai_sales_assistant

JWT_SECRET_KEY=your-super-secret-key-minimum-32-characters
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60

CORS_ORIGINS=http://localhost:5500,http://127.0.0.1:5500

SUPERADMIN_EMAIL=superadmin@yourdomain.com
SUPERADMIN_PASSWORD=YourSecurePassword123!
SUPERADMIN_FIRST_NAME=Super
SUPERADMIN_LAST_NAME=Admin
```

**Never commit `.env` to version control.**

### 6. Create PostgreSQL database

```sql
CREATE DATABASE ai_sales_assistant;
```

### 7. Run Alembic migration

```bash
alembic upgrade head
```

This creates:
- `tenants` table
- `user_role_enum` PostgreSQL enum
- `users` table with proper indexes and constraints

### 8. Seed Super Admin

```bash
python -m app.scripts.seed
```

Output (safe — no credentials logged):
```
Super Admin created successfully — id=... email=...
```

Running again:
```
Super Admin already exists — skipping creation.
```

### 9. Start the development server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## API Documentation

Once running, visit:

| URL                            | Description        |
|--------------------------------|--------------------|
| http://localhost:8000/docs     | Swagger UI         |
| http://localhost:8000/redoc    | ReDoc              |
| http://localhost:8000/health   | Health check       |

---

## Authentication API

### POST /api/v1/auth/login

**Request:**
```json
{
  "email": "superadmin@yourdomain.com",
  "password": "YourSecurePassword123!"
}
```

**Response (200):**
```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "user": {
    "id": "<uuid>",
    "first_name": "Super",
    "last_name": "Admin",
    "email": "superadmin@yourdomain.com",
    "role": "SUPER_ADMIN",
    "tenant_id": null
  }
}
```

**Error responses:**

| Code | Reason                           |
|------|----------------------------------|
| 401  | Invalid email or password        |
| 403  | Account is inactive              |
| 422  | Validation error (missing fields)|

---

## Running Tests

The test suite uses an in-memory SQLite database — no PostgreSQL required for unit tests.

```bash
pytest -v
```

---

## User Roles

| Role          | tenant_id   | Access Level          |
|---------------|-------------|------------------------|
| SUPER_ADMIN   | NULL        | Full platform access  |
| TENANT_ADMIN  | tenant UUID | Tenant management     |
| SALES_USER    | tenant UUID | Sales operations      |

---

## Security Notes

- Passwords hashed with **Argon2id** (OWASP recommended)
- JWT secrets from environment only
- Generic error messages (no email enumeration)
- Active-user check enforced before issuing tokens
- CORS origins configured from environment
- No secrets in source code
- No password/hash/token logging

---

## Environment Variables Reference

| Variable                      | Required | Description                            |
|-------------------------------|----------|----------------------------------------|
| DATABASE_URL                  | ✓        | PostgreSQL async URL                   |
| JWT_SECRET_KEY                | ✓        | JWT signing secret (min 32 chars)      |
| JWT_ALGORITHM                 | ✓        | `HS256` recommended                    |
| JWT_ACCESS_TOKEN_EXPIRE_MINUTES| ✓       | Token lifetime in minutes              |
| CORS_ORIGINS                  | ✓        | Comma-separated allowed origins        |
| SUPERADMIN_EMAIL              | ✓        | Super Admin email (seeder)             |
| SUPERADMIN_PASSWORD           | ✓        | Super Admin password (seeder)          |
| SUPERADMIN_FIRST_NAME         |          | Default: Super                         |
| SUPERADMIN_LAST_NAME          |          | Default: Admin                         |
| APP_NAME                      |          | Default: AI Sales Assistant            |
| APP_ENV                       |          | `development` / `production`           |
| APP_HOST                      |          | Default: 0.0.0.0                       |
| APP_PORT                      |          | Default: 8000                          |

---

## AI Platform (v2)

Every customer turn — on WhatsApp, Telegram or the web simulator — goes through one pipeline
(`app/services/ai/orchestrator.py`):

```
channel adapter ─► (dedup · tenant routing · ordering) ─► ConversationOrchestrator
   verify signature      │
   idempotent by msg id  ├─ 1 persist inbound message (committed first)
   ack fast, queue job   ├─ 2 opt-out / AI-off / monthly token budget
                         ├─ 3 context: persona+rules · customer memory · rolling summary ·
                         │     hybrid-retrieved knowledge · relevant products + live stock
                         ├─ 4 generate (streaming) with tools: check_stock · get_product_details ·
                         │     calculate_discount · search_knowledge · request_human_handoff
                         ├─ 5 guardrails: discount limit · unverified prices · prompt leaks ·
                         │     regenerate once, else escalate to a human
                         ├─ 6 persist reply + AITrace + real token usage
                         └─ 7 analysis (fast model → grounded signals → deterministic lead score,
                               memory, summary, handoff) — after the customer has their answer
```

| Area | Where | Notes |
|---|---|---|
| LLM gateway | `services/ai/llm.py` | OpenAI-compatible (Groq default). Pooled client, retry+backoff (honours `Retry-After`), per-key/model circuit breaker, fallback only on model/transient errors, tool calling, streaming with real usage. |
| RAG | `services/rag_service.py`, `ai/chunking.py`, `ai/vector_store.py` | Structure-aware chunks (headings/pages), multilingual embeddings, dense + BM25 with rank fusion, per-tenant isolation, document versions/dedup/status, products indexed too. |
| Vector backend | `VECTOR_BACKEND=auto` | **pgvector** (HNSW) when the extension exists, else portable **numpy**. Embeddings are always stored as float32 bytes, so switching needs no data migration. |
| Jobs | `services/jobs.py` | Postgres queue (`FOR UPDATE SKIP LOCKED`), per-customer ordering, retries with backoff, crash recovery. Also runs the follow-up scheduler. |
| Follow-ups | `services/follow_up_service.py` | AI-drafted, guardrail-checked, really delivered; max attempts; stops on reply / opt-out / human takeover / closed lead; WhatsApp 24h-window aware; quiet hours; optional rep approval. |
| Observability | `GET /tenant-admin/ai-usage/{stats,traces,traces/{id}}` | Per-turn retrieval, tool calls, guardrail actions, timings, tokens. |
| Secrets | `core/crypto.py` | Tenant API keys / WhatsApp tokens encrypted at rest (`ENCRYPTION_KEY`). |

### Upgrade steps

```bash
pip install -r requirements.txt
alembic upgrade head                      # schema + converts existing embeddings + encrypts stored secrets
python -m app.scripts.reindex_knowledge   # re-embeds with the multilingual model, indexes products
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The first reindex downloads the embedding model (~220 MB, once).

### pgvector on Windows without Docker

`pip install pgvector` only installs the Python client (already in `requirements.txt`). The **extension** must exist
inside PostgreSQL. Until it does, the app transparently uses the numpy backend. To install it (needs Visual Studio Build
Tools and an elevated prompt), open an *x64 Native Tools Command Prompt for VS* **as Administrator**:

```bat
set "PGROOT=C:\Program Files\PostgreSQL\18"
cd %TEMP%
git clone --branch <latest tag, 0.8.1 or newer> https://github.com/pgvector/pgvector.git
cd pgvector
nmake /F Makefile.win
nmake /F Makefile.win install
```

Restart the API. The log line `Vector backend: pgvector (HNSW, dim=384)` confirms it; the app creates the extension and
the `knowledge_vectors` table itself and back-fills it from the stored embeddings.

### Channels

* **WhatsApp** — set the tenant's *phone number id* and an app secret (`WHATSAPP_APP_SECRET` or per tenant). Unsigned or
  mis-signed webhooks are rejected; unknown phone-number ids are ignored (never routed to another tenant).
* **Telegram** — one shared bot. Customers open `https://t.me/<bot>?start=<tenant-slug>` (shown in AI Settings); each chat is
  pinned to that tenant. With exactly one active tenant, unpinned chats are pinned to it automatically.

### Operating notes

* Free-tier Groq limits (tokens/minute) apply: the gateway waits on `429`s, and the prompt budget is `AI_PROMPT_TOKEN_BUDGET`.
* Models your key cannot access are probed once, then skipped for 15 minutes.
* Tests never reach a real LLM: `tests/helpers.py` provides `NullLLM` (default) and a scriptable `FakeLLM` (`fake_llm` fixture).

---

## CRM Workspace

Tenant Admins and Sales Users share one workspace (`/pages/tenantadmin/dashboard.html`): **Home, Inbox, Contacts, Pipeline, Tasks**.
Tenant Admins also get a **Setup** group (Products, Inventory, Knowledge Base, AI Assistant, Team).

* **Who sees what** — a Sales User only sees deals and chats assigned to them, their own tasks, and the contacts they own or whose
  deals/chats are theirs. Only Tenant Admins can reassign a deal (it moves the chat with it, logs it on the contact timeline and
  notifies the new owner) or delete one.
* **Automatic owners** — every new deal gets an owner by round-robin among active, available **sales reps** — never an admin
  (a team with no available rep leaves deals unowned until one is) (switch it off with
  *Round-robin assignment* in the AI Assistant settings): AI-captured leads, and deals an admin adds without choosing an owner
  ("Assign automatically"). A scheduler sweep (`LeadAssignmentService.assign_unowned`, each `FOLLOWUP_SCAN_SECONDS`) also gives an
  owner to any open deal still without one — e.g. created before a rep existed or while nobody was available — and sends each rep
  one summary notification. Deals an admin deliberately unassigned (they carry an `ASSIGNED` timeline entry) are left alone.
* **Team roles** — an admin creates the roles the *Add team member* dropdown offers (`GET/POST /tenant-admin/roles`,
  `PUT/DELETE /tenant-admin/roles/{id}`; Team → *Manage roles*). Every team starts with the built-in **Sales Rep** and **Admin**
  roles. A role has an **access level** (Sales = sees only their own deals/chats/tasks; Admin = full workspace; fixed once
  created) and, for Sales roles, a *gets new deals automatically* switch. The platform permission check is unchanged: a person's
  `users.role` (`SALES_USER` / `TENANT_ADMIN`) follows their role's access level, `users.role_id` holds the title. Only Sales
  roles that receive deals are in the round-robin. `POST/PUT /users` take `role_id`; the Team list (`GET /users`) now includes
  admins. Moving someone to a role with different access signs them out; nobody can deactivate, delete, reset the password of
  or change the access of their own account. A role with people in it, and the built-in roles, cannot be deleted.
* **Contacts** — `GET/POST /tenant-admin/contacts`, `GET/PUT /tenant-admin/contacts/{id}` (profile, deals, chats, tasks, timeline),
  notes at `/contacts/{id}/notes`. Duplicates (same email, or same phone digits) are refused.
* **Pipeline** — stages are `NEW, CONTACTED, QUALIFIED, PROPOSAL, WON, LOST`; priorities `LOW, MEDIUM, HIGH, HOT`.
  `GET /tenant-admin/leads/board` powers the drag-and-drop board; deals can also be created by hand (`POST /tenant-admin/leads`).
  Older builds wrote `PROPOSAL_SENT / CLOSED_WON / CLOSED_LOST / WARM / COLD`; migration `c4d8e2f6a1b3` rewrites them and the API
  still accepts them on input.
* **Tasks** — `/tenant-admin/tasks` (+ `/summary`). The client sends `day_end` (end of its local day) so Today / Upcoming match the
  user's clock. The worker's scheduler sends one in-app reminder when a task's due time arrives.
* **Plans** — subscription plans no longer limit anything (one free tier for now); adding users is never blocked by a plan.
* **Demo logins** (after `python -m app.scripts.seed_demo_tenant`): `tenantadmin@example.com` and `salesrep@example.com`, both `Password123!`.

---

## Knowledge Base: "Ask" chat

The Knowledge screen (all roles; reps get Ask + a read-only document list, admins also get Documents and Unanswered) answers
questions **only** from the company's uploaded documents and live product catalog (`POST /tenant-admin/knowledge/ask`).

* **Pipeline** (`app/services/ai/knowledge_qa.py`): follow-up → standalone query → retrieve documents + products (live price/stock) →
  *no relevant passage ⇒ "not found" without calling the model* → model answers from numbered passages with `[n]` citations →
  deterministic checks (valid citations, every number / link / email / phone appears in the sources, each sentence supported by the
  source text) → one corrective retry → otherwise "couldn't confirm" with the closest passages.
* **Outcomes**: `answered`, `not_found`, `not_stated` (related text exists but doesn't say it), `unverified`, `empty`. Everything except
  `answered`/`empty` is logged in `knowledge_gaps` (deduplicated by wording) for admins: `GET /tenant-admin/knowledge/gaps`.
* **Safety**: instruction-like sentences planted in a document ("note to the assistant: ignore your rules…") are removed before the
  model sees the passage; passages far below the best match are dropped; per-person rate limit (20/min); usage counts toward the
  tenant's monthly AI token budget (`action_type = KNOWLEDGE_QA`).
* This cannot be mathematically guaranteed: the checks catch invented numbers/contacts and unsupported sentences, and every answer
  shows its sources so a person can verify. Outdated duplicate documents can still conflict; the model is told to flag disagreements.
