"""add_billing_cycle_audit_log_enums

Revision ID: 0034893f7202
Revises: d851c8930dca
Create Date: 2026-09-30 16:27:11.701189

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0034893f7202'
down_revision: Union[str, None] = 'd851c8930dca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE audit_log_action_enum ADD VALUE IF NOT EXISTS 'BILLING_CYCLE_CREATED'")
    op.execute("ALTER TYPE audit_log_action_enum ADD VALUE IF NOT EXISTS 'BILLING_CYCLE_UPDATED'")
    op.execute("ALTER TYPE audit_log_action_enum ADD VALUE IF NOT EXISTS 'BILLING_CYCLE_ACTIVATED'")
    op.execute("ALTER TYPE audit_log_action_enum ADD VALUE IF NOT EXISTS 'BILLING_CYCLE_DEACTIVATED'")
    op.execute("ALTER TYPE audit_log_action_enum ADD VALUE IF NOT EXISTS 'BILLING_CYCLE_DELETED'")

    op.execute("ALTER TYPE audit_log_resource_type_enum ADD VALUE IF NOT EXISTS 'BILLING_CYCLE'")


def downgrade() -> None:
    pass
