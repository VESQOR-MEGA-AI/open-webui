"""VESQOR: email verification for signup

Revision ID: v1e0q0r0a0b0c
Revises: f0bd01a18a3d
Create Date: 2026-08-13 00:00:00.000000

Adds email-verification support:
- ``auth.verified`` — false for new signups, existing accounts are grandfathered true
- ``email_verification`` table — one-time tokens (user_id, expires_at, used)
"""

import time
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from open_webui.migrations.util import get_existing_tables

revision: str = 'v1e0q0r0a0b0c'
down_revision: Union[str, None] = 'f0bd01a18a3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing_tables = set(get_existing_tables())

    # 1. auth.verified — default false; grandfather existing accounts as verified
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    auth_cols = {c['name'] for c in inspector.get_columns('auth')}
    if 'verified' not in auth_cols:
        op.add_column('auth', sa.Column('verified', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        # Existing accounts (created before verification existed) are trusted.
        op.execute("UPDATE auth SET verified = true")

    # 2. email_verification table — one-time tokens
    if 'email_verification' not in existing_tables:
        op.create_table(
            'email_verification',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('user_id', sa.String(), nullable=False),
            sa.Column('expires_at', sa.BigInteger(), nullable=False),
            sa.Column('used', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )
        op.create_index('idx_email_verification_user_id', 'email_verification', ['user_id'])


def downgrade() -> None:
    op.drop_index('idx_email_verification_user_id', table_name='email_verification')
    op.drop_table('email_verification')
    op.drop_column('auth', 'verified')
