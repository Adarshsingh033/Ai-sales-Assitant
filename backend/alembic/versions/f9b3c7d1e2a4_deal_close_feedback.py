"""Deals remember when and why they were closed

Revision ID: f9b3c7d1e2a4
Revises: e7a2b9c4d5f6
Create Date: 2026-11-04 15:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f9b3c7d1e2a4"
down_revision: Union[str, None] = "e7a2b9c4d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("leads", sa.Column("close_reason", sa.String(100), nullable=True))
    # deals closed before this change: the last update is the best guess for when they were closed
    op.execute("UPDATE leads SET closed_at = updated_at WHERE status IN ('WON', 'LOST')")


def downgrade() -> None:
    op.drop_column("leads", "close_reason")
    op.drop_column("leads", "closed_at")
