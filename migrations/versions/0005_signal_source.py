"""signal source

A watch can now hear "tickets are open" from each watch-only chain (BookMyShow and District), so
a `sessions` signal records which chain saw it, and the stored signal is unique per watch,
stage and chain. Existing rows get an empty source, so their alert keys do not change.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("source", sa.Text(), server_default="", nullable=False))
    op.drop_constraint("uq_signals_watch_item_id_stage", "signals", type_="unique")
    op.create_unique_constraint(
        "uq_signals_watch_item_id_stage_source", "signals", ["watch_item_id", "stage", "source"]
    )


def downgrade() -> None:
    # Keep one signal per watch and stage, the oldest, before the narrower constraint returns.
    op.execute(
        "DELETE FROM signals WHERE id NOT IN "
        "(SELECT min(id) FROM signals GROUP BY watch_item_id, stage)"
    )
    op.drop_constraint("uq_signals_watch_item_id_stage_source", "signals", type_="unique")
    op.create_unique_constraint(
        "uq_signals_watch_item_id_stage", "signals", ["watch_item_id", "stage"]
    )
    op.drop_column("signals", "source")
