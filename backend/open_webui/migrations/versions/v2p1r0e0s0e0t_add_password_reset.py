"""VESQOR: password reset tokens

Revision ID: v2p1r0e0s0e0t
Revises: 547bd03bd38c
Create Date: 2026-08-25 00:00:00.000000

Adds ``password_reset`` — one-time tokens (user_id, expires_at, used) backing
the "forgot password" flow. Same shape as ``email_verification``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from open_webui.migrations.util import get_existing_tables

revision: str = 'v2p1r0e0s0e0t'
down_revision: Union[str, None] = '547bd03bd38c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing_tables = set(get_existing_tables())

    if 'password_reset' not in existing_tables:
        op.create_table(
            'password_reset',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('user_id', sa.String(), nullable=False),
            sa.Column('expires_at', sa.BigInteger(), nullable=False),
            sa.Column('used', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )
        op.create_index('idx_password_reset_user_id', 'password_reset', ['user_id'])


def downgrade() -> None:
    op.drop_index('idx_password_reset_user_id', table_name='password_reset')
    op.drop_table('password_reset')
