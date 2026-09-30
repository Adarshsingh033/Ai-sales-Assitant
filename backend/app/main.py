"""
AI Sales Assistant — FastAPI Application Entry Point
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


@app.on_event("shutdown")
async def _shutdown() -> None:
    logger.info("Application shutting down")
