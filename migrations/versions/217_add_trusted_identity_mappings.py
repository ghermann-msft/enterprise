"""Add explicit Entra-to-Enterprise identity mappings.

Revision ID: 217
Revises: 216
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '217'
down_revision: str | None = '216'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'trusted_identity_mappings',
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('object_id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column(
            'enabled',
            sa.Boolean(),
            server_default=sa.text('true'),
            nullable=False,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('CURRENT_TIMESTAMP'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('CURRENT_TIMESTAMP'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['org_id'], ['org.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('tenant_id', 'object_id'),
    )


def downgrade() -> None:
    op.drop_table('trusted_identity_mappings')
