"""remove_extra_plan_limit_fields

Revision ID: 1d77fdc27f56
Revises: 74088b87a778
Create Date: 2026-09-30 15:57:42.259773

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1d77fdc27f56'
down_revision: Union[str, None] = '74088b87a778'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('subscription_plans', 'max_ai_usage')
    op.drop_column('subscription_plans', 'max_leads')
    op.drop_column('subscription_plans', 'max_storage')


def downgrade() -> None:
    op.add_column('subscription_plans', sa.Column('max_storage', sa.INTEGER(), autoincrement=False, nullable=True))
    op.add_column('subscription_plans', sa.Column('max_leads', sa.INTEGER(), autoincrement=False, nullable=True))
    op.add_column('subscription_plans', sa.Column('max_ai_usage', sa.INTEGER(), autoincrement=False, nullable=True))
