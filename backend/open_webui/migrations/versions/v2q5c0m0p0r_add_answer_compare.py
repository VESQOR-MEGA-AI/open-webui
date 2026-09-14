"""VESQOR VQ-25: answer comparison benchmark tables

Revision ID: v2q5c0m0p0r
Revises: v0e1s1q1r
Create Date: 2026-09-12 00:00:00.000000

Adds the persistence layer for the admin-only answer comparison page:

  * ``answer_compare_run``     — one prompt sent to every provider
  * ``answer_compare_answer``  — one provider's answer, versioned by ``revision``
  * ``answer_compare_report``  — one judge's blind verdict for a run
  * ``answer_compare_summary`` — the current code-assembled summary of a run

All four tables are append-only on ``revision``: regenerating an answer, re-judging
a run or recomputing a summary inserts ``revision + 1`` and leaves every earlier
row intact. Nothing stores an "outdated" flag — a report is outdated exactly when
its ``judged_versions`` differs from the run's current ``[{provider, revision}]``
set, which is computed at read time (owner decision, 2026-09-12).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from open_webui.migrations.util import get_existing_tables

revision: str = 'v2q5c0m0p0r'
down_revision: Union[str, None] = 'v0e1s1q1r'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing_tables = set(get_existing_tables())

    if 'answer_compare_run' not in existing_tables:
        op.create_table(
            'answer_compare_run',
            sa.Column('id', sa.Text(), primary_key=True),
            sa.Column('prompt', sa.Text(), nullable=False),
            sa.Column('reference', sa.Text(), nullable=True),
            sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'active'")),
            sa.Column('rerun_of_run_id', sa.Text(), nullable=True),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('admin_email', sa.Text(), nullable=False),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
        )
        op.create_index('ix_answer_compare_run_created', 'answer_compare_run', ['created_at'])

    if 'answer_compare_answer' not in existing_tables:
        op.create_table(
            'answer_compare_answer',
            sa.Column('id', sa.Text(), primary_key=True),
            sa.Column('run_id', sa.Text(), nullable=False),
            sa.Column('provider', sa.Text(), nullable=False),
            sa.Column('revision', sa.Integer(), nullable=False),
            sa.Column('requested_model', sa.Text(), nullable=True),
            sa.Column('model', sa.Text(), nullable=True),
            sa.Column('engine_version', sa.Text(), nullable=True),
            sa.Column('params', sa.JSON(), nullable=True),
            sa.Column('text', sa.Text(), nullable=True),
            sa.Column('status', sa.Text(), nullable=False),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
        )
        op.create_index('ix_answer_compare_answer_run', 'answer_compare_answer', ['run_id'])
        op.create_index(
            'ux_answer_compare_answer_run_provider_rev',
            'answer_compare_answer',
            ['run_id', 'provider', 'revision'],
            unique=True,
        )

    if 'answer_compare_report' not in existing_tables:
        op.create_table(
            'answer_compare_report',
            sa.Column('id', sa.Text(), primary_key=True),
            sa.Column('run_id', sa.Text(), nullable=False),
            sa.Column('judge', sa.Text(), nullable=False),
            sa.Column('revision', sa.Integer(), nullable=False),
            sa.Column('status', sa.Text(), nullable=False),
            sa.Column('requested_model', sa.Text(), nullable=True),
            sa.Column('model', sa.Text(), nullable=True),
            sa.Column('label_map', sa.JSON(), nullable=True),
            sa.Column('report', sa.JSON(), nullable=True),
            sa.Column('judged_versions', sa.JSON(), nullable=True),
            sa.Column('blinding_compromised', sa.JSON(), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
        )
        op.create_index('ix_answer_compare_report_run', 'answer_compare_report', ['run_id'])
        op.create_index(
            'ux_answer_compare_report_run_judge_rev',
            'answer_compare_report',
            ['run_id', 'judge', 'revision'],
            unique=True,
        )

    if 'answer_compare_summary' not in existing_tables:
        op.create_table(
            'answer_compare_summary',
            sa.Column('id', sa.Text(), primary_key=True),
            sa.Column('run_id', sa.Text(), nullable=False),
            sa.Column('revision', sa.Integer(), nullable=False),
            sa.Column('narrative', sa.Text(), nullable=False),
            sa.Column('tally', sa.JSON(), nullable=False),
            sa.Column('judged_versions', sa.JSON(), nullable=False),
            sa.Column('partial', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('included_judges', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
        )
        op.create_index('ix_answer_compare_summary_run', 'answer_compare_summary', ['run_id'])
        op.create_index(
            'ux_answer_compare_summary_run_rev',
            'answer_compare_summary',
            ['run_id', 'revision'],
            unique=True,
        )


def downgrade() -> None:
    # Guards mirror upgrade(): a table upgrade() skipped because it already
    # existed must not make downgrade() abort half-way, which would leave
    # alembic_version at head over a partially dropped schema.
    existing_tables = set(get_existing_tables())

    if 'answer_compare_summary' in existing_tables:
        op.drop_index('ux_answer_compare_summary_run_rev', table_name='answer_compare_summary', if_exists=True)
        op.drop_index('ix_answer_compare_summary_run', table_name='answer_compare_summary', if_exists=True)
        op.drop_table('answer_compare_summary')

    if 'answer_compare_report' in existing_tables:
        op.drop_index('ux_answer_compare_report_run_judge_rev', table_name='answer_compare_report', if_exists=True)
        op.drop_index('ix_answer_compare_report_run', table_name='answer_compare_report', if_exists=True)
        op.drop_table('answer_compare_report')

    if 'answer_compare_answer' in existing_tables:
        op.drop_index('ux_answer_compare_answer_run_provider_rev', table_name='answer_compare_answer', if_exists=True)
        op.drop_index('ix_answer_compare_answer_run', table_name='answer_compare_answer', if_exists=True)
        op.drop_table('answer_compare_answer')

    if 'answer_compare_run' in existing_tables:
        op.drop_index('ix_answer_compare_run_created', table_name='answer_compare_run', if_exists=True)
        op.drop_table('answer_compare_run')
