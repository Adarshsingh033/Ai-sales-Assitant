# AI Sales Assistant

A production-ready multi-tenant AI Sales Assistant SaaS platform.

## Monorepo Structure

```
Ai-sales-Assitant/
├── backend/     ← FastAPI + PostgreSQL + SQLAlchemy 2.x
└── frontend/    ← Vanilla HTML / CSS / JavaScript
```

See each directory's own `README.md` for detailed setup instructions.

## Quick Start

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env            # Edit with your credentials
alembic upgrade head
python -m app.scripts.seed
uvicorn app.main:app --reload
```

### Frontend

Serve `frontend/` with any static file server:

```bash
cd frontend
python -m http.server 5500
```

Open: http://localhost:5500/pages/login.html

## Architecture

- Multi-tenant SaaS foundation
- Role-based access: `SUPER_ADMIN`, `TENANT_ADMIN`, `SALES_USER`
- Argon2id password hashing
- JWT authentication
- Async PostgreSQL via asyncpg
- Alembic schema migrations
