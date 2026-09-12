"""merge VESQOR layer and upstream v0.11.1 migration branches

Both branches fork from f0bd01a18a3d (the v0.11.0 head):

  * VESQOR:   v1e0q0r0a0b0c -> 547bd03bd38c -> v2p1r0e0s0e0t
  * upstream: 1ce6ade7d93b  -> 6d09d1bf1f23 -> d4c1a8e37b62

Combining them in one tree leaves two heads, which makes `alembic upgrade head`
fail at boot. This is an empty merge revision: it only rejoins the two lines so
there is a single head again. Deployed databases sit at v2p1r0e0s0e0t, so the
upgrade replays the three upstream revisions and then this no-op.

Revision ID: v0e1s1q1r
Revises: d4c1a8e37b62, v2p1r0e0s0e0t
Create Date: 2026-08-26 00:00:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = 'v0e1s1q1r'
down_revision: tuple[str, ...] = ('d4c1a8e37b62', 'v2p1r0e0s0e0t')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
