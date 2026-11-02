"""Team roles: roles an admin creates, and users.role_id

Revision ID: e7a2b9c4d5f6
Revises: d6f1a3b8c2e4
Create Date: 2026-11-04 10:00:00.000000
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7a2b9c4d5f6"
down_revision: Union[str, None] = "d6f1a3b8c2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", _UUID, primary_key=True, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("access", sa.String(8), nullable=False),
        sa.Column("receives_deals", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_roles_tenant_id", "roles", ["tenant_id"])
    op.create_index("uq_roles_tenant_name", "roles", ["tenant_id", sa.text("lower(name)")], unique=True)

    op.add_column("users", sa.Column("role_id", _UUID, nullable=True))
    op.create_foreign_key("fk_users_role_id", "users", "roles", ["role_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_users_role_id", "users", ["role_id"])

    # Every existing tenant starts with the two built-in roles, and its people are filed under them.
    conn = op.get_bind()
    roles = sa.table(
        "roles",
        sa.column("id", _UUID), sa.column("tenant_id", _UUID), sa.column("name", sa.String),
        sa.column("access", sa.String), sa.column("receives_deals", sa.Boolean), sa.column("is_system", sa.Boolean),
    )
    for (tenant_id,) in conn.execute(sa.text("SELECT id FROM tenants")).fetchall():
        for name, access, user_role, gets_deals in (
            ("Sales Rep", "SALES", "SALES_USER", True),
            ("Admin", "ADMIN", "TENANT_ADMIN", False),
        ):
            role_id = uuid.uuid4()
            conn.execute(sa.insert(roles).values(
                id=role_id, tenant_id=tenant_id, name=name, access=access, receives_deals=gets_deals, is_system=True,
            ))
            conn.execute(
                sa.text(f"UPDATE users SET role_id = :rid WHERE tenant_id = :tid AND role = '{user_role}'").bindparams(
                    sa.bindparam("rid", type_=_UUID), sa.bindparam("tid", type_=_UUID),
                ),
                {"rid": role_id, "tid": tenant_id},
            )


def downgrade() -> None:
    op.drop_index("ix_users_role_id", table_name="users")
    op.drop_constraint("fk_users_role_id", "users", type_="foreignkey")
    op.drop_column("users", "role_id")
    op.drop_table("roles")
