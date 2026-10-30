"""
Application configuration — loaded from environment variables via Pydantic Settings.
Never hardcode secrets, URLs, or environment-specific values here.
"""
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    APP_NAME: str = "AI Sales Assistant"
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DATABASE_URL: str

    # ------------------------------------------------------------------
    # JWT
    # ------------------------------------------------------------------
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # ------------------------------------------------------------------
    # CORS — comma-separated list in .env
    # ------------------------------------------------------------------
    CORS_ORIGINS: str = "http://localhost:5500"

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    # ------------------------------------------------------------------
    # Super Admin seeder (read-only — used only by seed script)
    # ------------------------------------------------------------------
    SUPERADMIN_EMAIL: str
    SUPERADMIN_PASSWORD: str
    SUPERADMIN_FIRST_NAME: str = "Super"
    SUPERADMIN_LAST_NAME: str = "Admin"

    # ------------------------------------------------------------------
    # AI & LLM (Groq & Embeddings)
    # ------------------------------------------------------------------
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-20b"
    JINA_API_KEY: str = ""

    # LLM gateway (any OpenAI-compatible chat-completions provider)
    LLM_BASE_URL: str = "https://api.groq.com/openai/v1"
    LLM_FAST_MODEL: str = "llama-3.1-8b-instant"  # rewrite / analysis / summary tasks
    LLM_FALLBACK_MODELS: str = "llama-3.3-70b-versatile,openai/gpt-oss-20b,llama-3.1-8b-instant"
    LLM_TIMEOUT_SECONDS: float = 30.0
    LLM_MAX_RETRIES: int = 2
    LLM_BREAKER_FAILURES: int = 3
    LLM_BREAKER_COOLDOWN_SECONDS: float = 30.0
    LLM_MODEL_UNAVAILABLE_COOLDOWN_SECONDS: float = 900.0  # a model this key cannot access is skipped for 15 min

    @property
    def llm_fallback_models_list(self) -> List[str]:
        return [m.strip() for m in self.LLM_FALLBACK_MODELS.split(",") if m.strip()]

    # Prompt budgeting (rough tokens, chars/3.5)
    AI_PROMPT_TOKEN_BUDGET: int = 4000
    AI_HISTORY_MESSAGES: int = 12
    AI_MAX_TOOL_ROUNDS: int = 3
    AI_MAX_REPLY_CHARS: int = 1500

    # ------------------------------------------------------------------
    # Embeddings / vector search
    #   EMBEDDING_PROVIDER: "fastembed" (local ONNX) | "hash" (offline, tests/dev)
    #   VECTOR_BACKEND:     "auto" (pgvector when installed, else numpy) | "pgvector" | "numpy"
    # ------------------------------------------------------------------
    EMBEDDING_PROVIDER: str = "fastembed"
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DIM: int = 384
    VECTOR_BACKEND: str = "auto"
    RAG_MIN_SCORE: float = 0.30  # documents (calibrated with multilingual MiniLM: 0 false positives on a bilingual FAQ)
    RAG_MIN_SCORE_PRODUCT: float = 0.25  # product records are short, so their similarities run lower
    RAG_TOP_K: int = 3
    KNOWLEDGE_INLINE_MAX_CHARS: int = 60000  # larger documents are indexed by the background worker

    # ------------------------------------------------------------------
    # Secrets at rest (Fernet key, urlsafe base64 32 bytes). When empty a key is
    # derived from JWT_SECRET_KEY so existing deployments keep working.
    # ------------------------------------------------------------------
    ENCRYPTION_KEY: str = ""

    # ------------------------------------------------------------------
    # Background worker (Postgres-backed job queue + follow-up scheduler)
    # ------------------------------------------------------------------
    AI_WORKER_ENABLED: bool = True
    AI_WORKER_CONCURRENCY: int = 4
    AI_WORKER_POLL_SECONDS: float = 1.0
    FOLLOWUP_SCAN_SECONDS: float = 60.0

    # ------------------------------------------------------------------
    # WhatsApp Integration (Meta Cloud API / Webhook)
    # ------------------------------------------------------------------
    WHATSAPP_VERIFY_TOKEN: str = "ai_sales_verify_token"
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_APP_SECRET: str = ""  # Meta app secret — enables X-Hub-Signature-256 verification

    # ------------------------------------------------------------------
    # Telegram Integration (one shared bot; chats are pinned to a tenant via deep link)
    # ------------------------------------------------------------------
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_BOT_ENABLED: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
