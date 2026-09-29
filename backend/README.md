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
