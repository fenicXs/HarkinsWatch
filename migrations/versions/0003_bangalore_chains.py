"""Bangalore chains: BookMyShow and District

Turns both chains on and gives each one city-wide pseudo-theatre in Bangalore. They are
watch-only: the scheduler needs a theatre row to know a chain serves a city, and neither site
is read cinema by cinema.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMES = {
    "bms": "BookMyShow (all Bengaluru cinemas)",
    "district": "District (all Bengaluru cinemas)",
}


def upgrade() -> None:
    op.execute("UPDATE chains SET enabled = true WHERE code IN ('bms', 'district')")
    for code, name in NAMES.items():
        op.execute(
            "INSERT INTO theatres (chain_id, city_id, external_id, name) "
            f"SELECT c.id, ci.id, 'blr', '{name}' FROM chains c, cities ci "
            f"WHERE c.code = '{code}' AND ci.code = 'blr'"
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM theatres WHERE external_id = 'blr' "
        "AND chain_id IN (SELECT id FROM chains WHERE code IN ('bms', 'district'))"
    )
    op.execute("UPDATE chains SET enabled = false WHERE code IN ('bms', 'district')")
