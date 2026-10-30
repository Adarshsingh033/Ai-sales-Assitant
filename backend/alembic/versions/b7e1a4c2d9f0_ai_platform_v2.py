"""AI platform v2: RAG v2, job queue, traces, customer memory, follow-up v2, channel routing, encrypted secrets

Revision ID: b7e1a4c2d9f0
Revises: 9699cdf13ed2
Create Date: 2026-10-30 12:00:00.000000

Notes
  * knowledge_chunks.embedding_json (JSON text) -> knowledge_chunks.embedding (float32 bytes). Existing rows are
    converted and tagged with a legacy model name, so the app re-embeds them with the current model at startup
    (or immediately via `python -m app.scripts.reindex_knowledge`).
  * Stored secrets (Groq key, WhatsApp token) are encrypted in place.
  * The optional pgvector sidecar table is created only when the extension can be enabled; otherwise it is skipped
    and the application falls back to its portable numpy search (it will create the table itself once available).
"""
import json
import struct
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e1a4c2d9f0"
down_revision: Union[str, None] = "9699cdf13ed2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_MODEL = "legacy:BAAI/bge-small-en-v1.5"


def _uuid_col(name: str, **kw):
    return sa.Column(name, postgresql.UUID(as_uuid=True), **kw)


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------ knowledge_documents
    op.add_column("knowledge_documents", sa.Column("content_text", sa.Text(), nullable=True))
    op.add_column("knowledge_documents", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("knowledge_documents", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("knowledge_documents", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("knowledge_documents", sa.Column("embedding_model", sa.String(120), nullable=True))
    op.add_column(
        "knowledge_documents",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_knowledge_documents_content_hash", "knowledge_documents", ["content_hash"])

    # ------------------------------------------------------------------ knowledge_chunks
    op.alter_column("knowledge_chunks", "document_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.add_column("knowledge_chunks", _uuid_col("product_id", nullable=True))
    op.create_foreign_key(
        "fk_knowledge_chunks_product", "knowledge_chunks", "products", ["product_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_knowledge_chunks_product_id", "knowledge_chunks", ["product_id"])
    op.add_column(
        "knowledge_chunks", sa.Column("source_type", sa.String(20), nullable=False, server_default="DOCUMENT")
    )
    op.add_column("knowledge_chunks", sa.Column("section_title", sa.String(255), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("page_number", sa.Integer(), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("embedding", sa.LargeBinary(), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("embedding_model", sa.String(120), nullable=True))
    op.create_index("ix_knowledge_chunks_tenant_source", "knowledge_chunks", ["tenant_id", "source_type"])

    # convert JSON float lists -> float32 bytes
    rows = bind.execute(sa.text("SELECT id, embedding_json FROM knowledge_chunks")).fetchall()
    for chunk_id, raw in rows:
        try:
            values = json.loads(raw)
            blob = struct.pack(f"<{len(values)}f", *values) if values else None
        except (TypeError, ValueError):
            blob = None
        bind.execute(
            sa.text("UPDATE knowledge_chunks SET embedding = :b, embedding_model = :m WHERE id = :i"),
            {"b": blob, "m": LEGACY_MODEL if blob else None, "i": chunk_id},
        )
    op.drop_column("knowledge_chunks", "embedding_json")
    bind.execute(sa.text("UPDATE knowledge_documents SET embedding_model = :m"), {"m": LEGACY_MODEL})

    # ------------------------------------------------------------------ conversations / customers
    op.add_column(
        "conversations", sa.Column("summary_message_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("conversations", sa.Column("handoff_reason", sa.Text(), nullable=True))
    op.add_column("customers", sa.Column("preferred_language", sa.String(20), nullable=True))
    op.add_column("customers", sa.Column("do_not_contact", sa.Boolean(), nullable=False, server_default=sa.false()))

    # ------------------------------------------------------------------ ai_assistant_configs
    op.alter_column("ai_assistant_configs", "groq_api_key", existing_type=sa.String(255), type_=sa.Text())
    op.alter_column("ai_assistant_configs", "whatsapp_access_token", existing_type=sa.String(512), type_=sa.Text())
    op.add_column("ai_assistant_configs", sa.Column("fast_model", sa.String(100), nullable=True))
    op.add_column("ai_assistant_configs", sa.Column("language", sa.String(30), nullable=False, server_default="auto"))
    op.add_column("ai_assistant_configs", sa.Column("whatsapp_app_secret", sa.Text(), nullable=True))
    op.add_column("ai_assistant_configs", sa.Column("whatsapp_followup_template", sa.String(100), nullable=True))
    op.add_column("ai_assistant_configs", sa.Column("max_followups", sa.Integer(), nullable=False, server_default="3"))
    op.add_column(
        "ai_assistant_configs", sa.Column("followup_mode", sa.String(20), nullable=False, server_default="AUTO")
    )
    op.add_column("ai_assistant_configs", sa.Column("monthly_token_limit", sa.Integer(), nullable=True))

    # encrypt existing plaintext secrets in place
    from app.core.crypto import encrypt_secret

    for cfg_id, groq_key, wa_token in bind.execute(
        sa.text("SELECT id, groq_api_key, whatsapp_access_token FROM ai_assistant_configs")
    ).fetchall():
        bind.execute(
            sa.text("UPDATE ai_assistant_configs SET groq_api_key = :g, whatsapp_access_token = :w WHERE id = :i"),
            {"g": encrypt_secret(groq_key), "w": encrypt_secret(wa_token), "i": cfg_id},
        )

    # ------------------------------------------------------------------ follow_up_schedules
    op.add_column("follow_up_schedules", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("follow_up_schedules", sa.Column("channel", sa.String(30), nullable=True))
    op.add_column("follow_up_schedules", sa.Column("stop_reason", sa.String(120), nullable=True))
    op.add_column("follow_up_schedules", sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True))

    # ------------------------------------------------------------------ new tables
    op.create_table(
        "ai_jobs",
        _uuid_col("id", primary_key=True),
        _uuid_col("tenant_id", nullable=True),
        _uuid_col("conversation_id", nullable=True),
        sa.Column("job_type", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_ai_jobs_tenant_id", "ai_jobs", ["tenant_id"])
    op.create_index("ix_ai_jobs_conversation_id", "ai_jobs", ["conversation_id"])
    op.create_index("ix_ai_jobs_status_run_at", "ai_jobs", ["status", "run_at"])

    op.create_table(
        "ai_traces",
        _uuid_col("id", primary_key=True),
        _uuid_col("tenant_id", nullable=False),
        _uuid_col("conversation_id", nullable=True),
        _uuid_col("customer_id", nullable=True),
        sa.Column("trigger", sa.String(30), nullable=False, server_default="INBOUND"),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_message", sa.Text(), nullable=True),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("retrieval", sa.JSON(), nullable=True),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("guardrails", sa.JSON(), nullable=True),
        sa.Column("timings", sa.JSON(), nullable=True),
        sa.Column("handoff_reason", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_ai_traces_tenant_id", "ai_traces", ["tenant_id"])
    op.create_index("ix_ai_traces_conversation_id", "ai_traces", ["conversation_id"])
    op.create_index("ix_ai_traces_tenant_created", "ai_traces", ["tenant_id", "created_at"])

    op.create_table(
        "customer_memories",
        _uuid_col("id", primary_key=True),
        _uuid_col("tenant_id", nullable=False),
        _uuid_col("customer_id", nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("language", sa.String(20), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("customer_id", name="uq_customer_memories_customer_id"),
    )
    op.create_index("ix_customer_memories_tenant_id", "customer_memories", ["tenant_id"])

    op.create_table(
        "channel_bindings",
        _uuid_col("id", primary_key=True),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        _uuid_col("tenant_id", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("channel", "external_id", name="uq_channel_binding_external"),
    )
    op.create_index("ix_channel_bindings_tenant_id", "channel_bindings", ["tenant_id"])

    op.create_table(
        "inbound_events",
        _uuid_col("id", primary_key=True),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        _uuid_col("tenant_id", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("channel", "external_id", name="uq_inbound_event_external"),
    )
    op.create_index("ix_inbound_events_tenant_id", "inbound_events", ["tenant_id"])

    _try_enable_pgvector(bind)


def _try_enable_pgvector(bind) -> None:
    """Create the pgvector sidecar when the extension is installable; silently skip otherwise."""
    from app.core.config import settings

    dim = settings.EMBEDDING_DIM
    try:
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
            bind.execute(
                sa.text(
                    "CREATE TABLE IF NOT EXISTS knowledge_vectors ("
                    "chunk_id uuid PRIMARY KEY REFERENCES knowledge_chunks(id) ON DELETE CASCADE, "
                    "tenant_id uuid NOT NULL, source_type varchar(20) NOT NULL DEFAULT 'DOCUMENT', "
                    f"embedding vector({dim}) NOT NULL)"
                )
            )
            bind.execute(
                sa.text(
                    "CREATE INDEX IF NOT EXISTS ix_knowledge_vectors_hnsw "
                    "ON knowledge_vectors USING hnsw (embedding vector_cosine_ops)"
                )
            )
            bind.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_knowledge_vectors_tenant ON knowledge_vectors (tenant_id)"))
        print("pgvector enabled: knowledge_vectors (HNSW) created.")
    except Exception as exc:  # extension not installed on this server
        print(f"pgvector not available ({str(exc).splitlines()[0][:90]}); using the portable numpy backend.")


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP TABLE IF EXISTS knowledge_vectors")

    for table in ("inbound_events", "channel_bindings", "customer_memories", "ai_traces", "ai_jobs"):
        op.drop_table(table)

    for col in ("max_attempts", "channel", "stop_reason", "locked_at"):
        op.drop_column("follow_up_schedules", col)

    from app.core.crypto import decrypt_secret

    for cfg_id, groq_key, wa_token in bind.execute(
        sa.text("SELECT id, groq_api_key, whatsapp_access_token FROM ai_assistant_configs")
    ).fetchall():
        bind.execute(
            sa.text("UPDATE ai_assistant_configs SET groq_api_key = :g, whatsapp_access_token = :w WHERE id = :i"),
            {"g": decrypt_secret(groq_key), "w": decrypt_secret(wa_token), "i": cfg_id},
        )
    for col in (
        "monthly_token_limit", "followup_mode", "max_followups", "whatsapp_followup_template",
        "whatsapp_app_secret", "language", "fast_model",
    ):
        op.drop_column("ai_assistant_configs", col)
    op.alter_column("ai_assistant_configs", "whatsapp_access_token", existing_type=sa.Text(), type_=sa.String(512))
    op.alter_column("ai_assistant_configs", "groq_api_key", existing_type=sa.Text(), type_=sa.String(255))

    op.drop_column("customers", "do_not_contact")
    op.drop_column("customers", "preferred_language")
    op.drop_column("conversations", "handoff_reason")
    op.drop_column("conversations", "summary_message_count")

    op.add_column("knowledge_chunks", sa.Column("embedding_json", sa.Text(), nullable=True))
    for chunk_id, blob in bind.execute(sa.text("SELECT id, embedding FROM knowledge_chunks")).fetchall():
        values = list(struct.unpack(f"<{len(blob) // 4}f", bytes(blob))) if blob else []
        bind.execute(
            sa.text("UPDATE knowledge_chunks SET embedding_json = :j WHERE id = :i"),
            {"j": json.dumps(values), "i": chunk_id},
        )
    bind.execute(sa.text("DELETE FROM knowledge_chunks WHERE document_id IS NULL"))
    op.alter_column("knowledge_chunks", "embedding_json", nullable=False)
    op.drop_index("ix_knowledge_chunks_tenant_source", table_name="knowledge_chunks")
    for col in ("embedding_model", "embedding", "page_number", "section_title", "source_type"):
        op.drop_column("knowledge_chunks", col)
    op.drop_index("ix_knowledge_chunks_product_id", table_name="knowledge_chunks")
    op.drop_constraint("fk_knowledge_chunks_product", "knowledge_chunks", type_="foreignkey")
    op.drop_column("knowledge_chunks", "product_id")
    op.alter_column("knowledge_chunks", "document_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)

    op.drop_index("ix_knowledge_documents_content_hash", table_name="knowledge_documents")
    for col in ("updated_at", "embedding_model", "error_message", "version", "content_hash", "content_text"):
        op.drop_column("knowledge_documents", col)
