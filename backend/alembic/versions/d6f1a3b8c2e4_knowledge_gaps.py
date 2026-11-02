"""Knowledge gaps: questions the knowledge base could not answer (for the internal "Ask" chat)

Revision ID: d6f1a3b8c2e4
Revises: c4d8e2f6a1b3
Create Date: 2026-11-03 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d6f1a3b8c2e4"
down_revision: Union[str, None] = "c4d8e2f6a1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_gaps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("normalized", sa.String(300), nullable=False),
        sa.Column("times_asked", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(12), nullable=False, server_default="OPEN"),
        sa.Column("last_asked_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("first_asked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_asked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_asked_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("tenant_id", "normalized", name="uq_knowledge_gaps_tenant_question"),
    )
    op.create_index("ix_knowledge_gaps_tenant_id", "knowledge_gaps", ["tenant_id"])
    op.create_index("ix_knowledge_gaps_tenant_status", "knowledge_gaps", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_table("knowledge_gaps")
