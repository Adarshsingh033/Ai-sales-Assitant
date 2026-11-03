"""
AI Sales Assistant — FastAPI Application Entry Point (with real-time streaming)
"""
import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.routers import auth, users
from app.routers.super_admin import tenants as super_admin_tenants
from app.routers.super_admin import subscription_plans as super_admin_subscription_plans
from app.routers.super_admin import billing_cycles as super_admin_billing_cycles
from app.routers.super_admin import audit_logs as super_admin_audit_logs
from app.routers.super_admin import dashboard_stats as super_admin_dashboard_stats
from app.routers.tenant_admin import sales_users as tenant_admin_sales_users
from app.routers.tenant_admin import products as tenant_admin_products
from app.routers.tenant_admin import knowledge as tenant_admin_knowledge
from app.routers.tenant_admin import ai_config as tenant_admin_ai_config
from app.routers.tenant_admin import chat_simulator as tenant_admin_chat_simulator
from app.routers.tenant_admin import leads as tenant_admin_leads
from app.routers.tenant_admin import conversations as tenant_admin_conversations
from app.routers.tenant_admin import dashboard_stats as tenant_admin_dashboard_stats
from app.routers.tenant_admin import ai_usage as tenant_admin_ai_usage
from app.routers.tenant_admin import notifications as tenant_admin_notifications
from app.routers.tenant_admin import inventory as tenant_admin_inventory
from app.routers.tenant_admin import follow_ups as tenant_admin_follow_ups
from app.routers.tenant_admin import contacts as tenant_admin_contacts
from app.routers.tenant_admin import tasks as tenant_admin_tasks
from app.routers.tenant_admin import team as tenant_admin_team
from app.routers.tenant_admin import roles as tenant_admin_roles
from app.routers.tenant_admin import knowledge_ask as tenant_admin_knowledge_ask
from app.routers.webhooks import whatsapp as webhooks_whatsapp
from fastapi.staticfiles import StaticFiles
import os

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
# httpx logs every request URL at INFO, and the Telegram bot token is part of that URL.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title=settings.APP_NAME,
    description="Production-ready AI Sales Assistant SaaS Platform API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ---------------------------------------------------------------------------
# Static Files
# ---------------------------------------------------------------------------
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ---------------------------------------------------------------------------
# CORS — origins from environment, never wildcard in production
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Exception Handlers
# ---------------------------------------------------------------------------
@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error("Database error occurred: %s", str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "A database error occurred. Please try again later."},
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    from fastapi import HTTPException
    # Let FastAPI handle its own HTTPExceptions
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    logger.error("Unexpected error occurred: %s", str(exc), exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An unexpected error occurred. Please try again later."},
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(tenant_admin_sales_users.router, prefix="/api/v1")
app.include_router(tenant_admin_products.router, prefix="/api/v1")
app.include_router(tenant_admin_knowledge.router, prefix="/api/v1")
app.include_router(tenant_admin_ai_config.router, prefix="/api/v1")
app.include_router(tenant_admin_chat_simulator.router, prefix="/api/v1")
app.include_router(tenant_admin_leads.router, prefix="/api/v1")
app.include_router(tenant_admin_conversations.router, prefix="/api/v1")
app.include_router(tenant_admin_dashboard_stats.router, prefix="/api/v1")
app.include_router(tenant_admin_ai_usage.router, prefix="/api/v1")
app.include_router(tenant_admin_notifications.router, prefix="/api/v1")
app.include_router(tenant_admin_inventory.router, prefix="/api/v1")
app.include_router(tenant_admin_follow_ups.router, prefix="/api/v1")
app.include_router(tenant_admin_contacts.router, prefix="/api/v1")
app.include_router(tenant_admin_tasks.router, prefix="/api/v1")
app.include_router(tenant_admin_team.router, prefix="/api/v1")
app.include_router(tenant_admin_roles.router, prefix="/api/v1")
app.include_router(tenant_admin_knowledge_ask.router, prefix="/api/v1")
app.include_router(webhooks_whatsapp.router, prefix="/api/v1")
app.include_router(super_admin_tenants.router, prefix="/api/v1")
app.include_router(super_admin_subscription_plans.router, prefix="/api/v1")
app.include_router(super_admin_billing_cycles.router, prefix="/api/v1")
app.include_router(super_admin_audit_logs.router, prefix="/api/v1")
app.include_router(super_admin_dashboard_stats.router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Health"])
async def health_check() -> dict:
    """Basic liveness probe."""
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}


@app.on_event("startup")
async def _startup() -> None:
    logger.info("Application starting — env=%s", settings.APP_ENV)

    # Vector search: pgvector when the extension is installed, otherwise the portable numpy backend.
    from app.core.database import engine
    from app.services.ai.vector_store import init_vector_backend
    await init_vector_backend(engine)

    # Load the embedding model in the background so the first customer message does not pay for it (several seconds).
    import asyncio

    from app.services.ai.embeddings import get_embedding_provider

    asyncio.get_running_loop().run_in_executor(None, lambda: get_embedding_provider().embed(["warm-up"]))

    # Durable job queue + follow-up scheduler (set AI_WORKER_ENABLED=false to run it as a separate process)
    if settings.AI_WORKER_ENABLED:
        from app.services.jobs import worker
        await worker.start()

    if settings.TELEGRAM_BOT_ENABLED and settings.TELEGRAM_BOT_TOKEN:
        from app.services.telegram_service import telegram_service
        telegram_service.launch()
        logger.info("Telegram Bot integration launched in background.")


@app.on_event("shutdown")
async def _shutdown() -> None:
    logger.info("Application shutting down")
    if settings.TELEGRAM_BOT_ENABLED:
        from app.services.telegram_service import telegram_service
        await telegram_service.stop_polling()
    if settings.AI_WORKER_ENABLED:
        from app.services.jobs import worker
        await worker.stop()
    from app.services.ai.llm import close_http_clients
    await close_http_clients()
