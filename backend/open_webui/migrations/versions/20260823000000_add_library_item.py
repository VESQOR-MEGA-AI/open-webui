"""VESQOR: add library_item table (LIB-1)

Revision ID: 547bd03bd38c
Revises: v1e0q0r0a0b0c
Create Date: 2026-08-23 00:00:00.000000

Adds the ``library_item`` table backing the VESQOR Library — per-user
records of exported reports and uploaded documents, grouped by chat
session in the frontend.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from open_webui.migrations.util import get_existing_tables

revision: str = '547bd03bd38c'
down_revision: Union[str, None] = 'v1e0q0r0a0b0c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing_tables = set(get_existing_tables())

    if 'library_item' not in existing_tables:
        op.create_table(
            'library_item',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('user_id', sa.String(), nullable=True),
            sa.Column('chat_id', sa.String(), nullable=True),
            sa.Column('message_id', sa.String(), nullable=True),
            sa.Column('chat_title', sa.Text(), nullable=True),
            sa.Column('filename', sa.Text(), nullable=True),
            sa.Column('content_type', sa.Text(), nullable=True),
            sa.Column('size', sa.BigInteger(), nullable=True),
            sa.Column('format', sa.String(), nullable=True),
            sa.Column('source', sa.String(), nullable=True),
            sa.Column('path', sa.Text(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=True),
        )
        op.create_index('idx_library_item_user_id', 'library_item', ['user_id'])
        op.create_index('idx_library_item_chat_id', 'library_item', ['chat_id'])
        op.create_index('idx_library_item_created_at', 'library_item', ['created_at'])


def downgrade() -> None:
    op.drop_index('idx_library_item_created_at', table_name='library_item')
    op.drop_index('idx_library_item_chat_id', table_name='library_item')
    op.drop_index('idx_library_item_user_id', table_name='library_item')
    op.drop_table('library_item')
