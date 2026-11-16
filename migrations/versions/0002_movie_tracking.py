"""movie release tracking

Adds when a movie was last seen on a chain's lists and when it went on sale. They let alerts
for a new release be derived from stored state (so a refused alert is retried) and keep movies
that left theatres from being matched by new watches.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("movies", sa.Column("released_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "movies",
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("movies", "last_seen_at")
    op.drop_column("movies", "released_at")
