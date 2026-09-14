"""VESQOR VQ-25: params column on answer_compare_report

Revision ID: v2q5p0a0r0m
Revises: v2q5c0m0p0r
Create Date: 2026-09-12 00:00:00.000000

Adds ``answer_compare_report.params`` — what was actually sent to the judge
and the structured-output ladder diagnostics (``structured_output_mode``,
``structured_output_rejections``, the ``temperature``/``response_format`` that
went out). Mirrors ``answer_compare_answer.params``.

A separate additive revision rather than an edit of v2q5c0m0p0r: that revision
is already applied to a database in use, and editing it in place would leave
the schema drifting from the migration history.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = 'v2q5p0a0r0m'
down_revision: Union[str, None] = 'v2q5c0m0p0r'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'answer_compare_report'
COLUMN = 'params'


def _has_column() -> bool:
    conn = op.get_bind()
    return COLUMN in {column['name'] for column in inspect(conn).get_columns(TABLE)}


def upgrade() -> None:
    if not _has_column():
        # A nullable column: plain ADD COLUMN works natively on SQLite too.
        op.add_column(TABLE, sa.Column(COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    if _has_column():
        op.drop_column(TABLE, COLUMN)
