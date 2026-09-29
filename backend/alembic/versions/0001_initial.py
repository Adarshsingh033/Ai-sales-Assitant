"""create tenants and users tables

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-29

Creates:
  - tenants table (stub for multi-tenancy)
  - user_role_enum PostgreSQL enum
  - users table with FK to tenants
  - Partial unique index: email unique globally for SUPER_ADMIN (tenant_id IS NULL)
  - Partial unique index: email unique per-tenant for TENANT_ADMIN / SALES_USER
  - Standard indexes for fast lookups

Downgrade drops all of the above in reverse order.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. tenants table
    # ------------------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    # ------------------------------------------------------------------
    # 2. user_role_enum
    # ------------------------------------------------------------------
    user_role_enum = postgresql.ENUM(
        "SUPER_ADMIN", "TENANT_ADMIN", "SALES_USER",
        name="user_role_enum",
        create_type=True,
    )
    user_role_enum.create(op.get_bind())

    # ------------------------------------------------------------------
    # 3. users table
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(
                "SUPER_ADMIN", "TENANT_ADMIN", "SALES_USER",
                name="user_role_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_users_tenant_id",
            ondelete="CASCADE",
        ),
    )

    # Standard indexes
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_tenant_id_email", "users", ["tenant_id", "email"])

    # Partial unique index: one Super Admin email globally (tenant_id IS NULL)
    op.execute(
        """
        CREATE UNIQUE INDEX uq_users_email_super_admin
        ON users (email)
        WHERE tenant_id IS NULL
        """
    )

    # Partial unique index: email unique within each tenant
    op.execute(
        """
        CREATE UNIQUE INDEX uq_users_email_per_tenant
        ON users (tenant_id, email)
        WHERE tenant_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("uq_users_email_per_tenant", table_name="users")
    op.drop_index("uq_users_email_super_admin", table_name="users")
    op.drop_index("ix_users_tenant_id_email", table_name="users")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    # Drop the enum type
    postgresql.ENUM(name="user_role_enum").drop(op.get_bind())

    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_table("tenants")
