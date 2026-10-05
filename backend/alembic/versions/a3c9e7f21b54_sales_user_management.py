"""sales user management: availability, token versioning, global email uniqueness

Revision ID: a3c9e7f21b54
Revises: 71014cbe3bdb
Create Date: 2026-10-02 11:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3c9e7f21b54'
down_revision: Union[str, None] = '71014cbe3bdb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('is_available', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        'users',
        sa.Column('token_version', sa.Integer(), nullable=False, server_default='0'),
    )
    op.execute("UPDATE users SET is_available = false WHERE is_active = false")

    op.create_index('ix_users_tenant_role_active', 'users', ['tenant_id', 'role', 'is_active'])
    op.create_index('ix_users_tenant_role_available', 'users', ['tenant_id', 'role', 'is_available'])

    op.execute("ALTER TYPE audit_log_action_enum ADD VALUE IF NOT EXISTS 'USER_DELETED'")
    op.execute("ALTER TYPE audit_log_action_enum ADD VALUE IF NOT EXISTS 'USER_PASSWORD_RESET'")

    duplicates = op.get_bind().execute(
        sa.text(
            "SELECT lower(email) AS email, count(*) AS cnt FROM users "
            "GROUP BY lower(email) HAVING count(*) > 1"
        )
    ).fetchall()
    if duplicates:
        emails = ", ".join(row.email for row in duplicates)
        raise RuntimeError(
            "Cannot enforce global email uniqueness: duplicate user emails exist "
            f"({emails}). Resolve them manually and re-run the migration."
        )

    op.execute("CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))")


def downgrade() -> None:
    op.drop_index('uq_users_email_lower', table_name='users')
    op.drop_index('ix_users_tenant_role_available', table_name='users')
    op.drop_index('ix_users_tenant_role_active', table_name='users')
    op.drop_column('users', 'token_version')
    op.drop_column('users', 'is_available')
