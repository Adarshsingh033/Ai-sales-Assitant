"""CRM workspace: tasks, contact timeline (activities), contact owner, canonical deal stages

Revision ID: c4d8e2f6a1b3
Revises: b7e1a4c2d9f0
Create Date: 2026-11-02 10:00:00.000000

Notes
  * Older UI builds could write stages PROPOSAL_SENT / CLOSED_WON / CLOSED_LOST and priorities WARM / COLD, which the
    follow-up scheduler and dashboard did not recognise (a "Closed Won" deal kept receiving follow-ups). They are
    rewritten to the canonical PROPOSAL / WON / LOST and MEDIUM / LOW. This part is not reversed on downgrade.
  * Existing deals get one "AI captured this lead" timeline entry (dated when the deal was created, with no score,
    since only the latest score is known) so contact timelines are not empty.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d8e2f6a1b3"
down_revision: Union[str, None] = "b7e1a4c2d9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid(name: str, **kw):
    return sa.Column(name, postgresql.UUID(as_uuid=True), **kw)


def upgrade() -> None:
    op.create_table(
        "tasks",
        _uuid("id", primary_key=True, nullable=False),
        _uuid("tenant_id", nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="OPEN"),
        _uuid("assigned_user_id", nullable=True),
        _uuid("created_by_id", nullable=True),
        _uuid("customer_id", nullable=True),
        _uuid("lead_id", nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_tasks_tenant_id", "tasks", ["tenant_id"])
    op.create_index("ix_tasks_customer_id", "tasks", ["customer_id"])
    op.create_index("ix_tasks_lead_id", "tasks", ["lead_id"])
    op.create_index("ix_tasks_assignee_status_due", "tasks", ["assigned_user_id", "status", "due_at"])
    op.create_index("ix_tasks_tenant_status_due", "tasks", ["tenant_id", "status", "due_at"])

    op.create_table(
        "activities",
        _uuid("id", primary_key=True, nullable=False),
        _uuid("tenant_id", nullable=False),
        _uuid("customer_id", nullable=True),
        _uuid("lead_id", nullable=True),
        _uuid("actor_user_id", nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_activities_tenant_id", "activities", ["tenant_id"])
    op.create_index("ix_activities_lead_id", "activities", ["lead_id"])
    op.create_index("ix_activities_customer_created", "activities", ["customer_id", "created_at"])

    op.add_column("customers", _uuid("owner_user_id", nullable=True))
    op.create_foreign_key("fk_customers_owner_user", "customers", "users", ["owner_user_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_customers_owner_user_id", "customers", ["owner_user_id"])

    # Canonical stages / priorities
    op.execute("UPDATE leads SET status = 'PROPOSAL' WHERE status = 'PROPOSAL_SENT'")
    op.execute("UPDATE leads SET status = 'WON' WHERE status = 'CLOSED_WON'")
    op.execute("UPDATE leads SET status = 'LOST' WHERE status = 'CLOSED_LOST'")
    op.execute("UPDATE leads SET priority = 'MEDIUM' WHERE priority = 'WARM'")
    op.execute("UPDATE leads SET priority = 'LOW' WHERE priority = 'COLD'")

    # One starting timeline entry per existing deal
    op.execute(
        """
        INSERT INTO activities (id, tenant_id, customer_id, lead_id, kind, text, created_at)
        SELECT gen_random_uuid(), tenant_id, customer_id, id, 'AI',
               'AI captured this lead', created_at
        FROM leads WHERE customer_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_customers_owner_user_id", table_name="customers")
    op.drop_constraint("fk_customers_owner_user", "customers", type_="foreignkey")
    op.drop_column("customers", "owner_user_id")
    op.drop_table("activities")
    op.drop_table("tasks")
