"""initial

Revision ID: 0001
Revises:
Create Date: 2026-11-16 13:53:42.990983
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chains",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chains")),
        sa.UniqueConstraint("code", name=op.f("uq_chains_code")),
    )
    op.create_table(
        "cities",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("tz", sa.Text(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cities")),
        sa.UniqueConstraint("code", name=op.f("uq_cities_code")),
    )
    op.create_table(
        "chain_snapshots",
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("city_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "taken_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("movie_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status in ('good','suspect')", name=op.f("ck_chain_snapshots_status")),
        sa.ForeignKeyConstraint(
            ["chain_id"], ["chains.id"], name=op.f("fk_chain_snapshots_chain_id_chains")
        ),
        sa.ForeignKeyConstraint(
            ["city_id"], ["cities.id"], name=op.f("fk_chain_snapshots_city_id_cities")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chain_snapshots")),
    )
    op.create_table(
        "jobs",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=True),
        sa.Column("city_id", sa.BigInteger(), nullable=True),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('queued','running','done','failed')", name=op.f("ck_jobs_status")
        ),
        sa.ForeignKeyConstraint(["chain_id"], ["chains.id"], name=op.f("fk_jobs_chain_id_chains")),
        sa.ForeignKeyConstraint(["city_id"], ["cities.id"], name=op.f("fk_jobs_city_id_cities")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index("ix_jobs_status_run_after", "jobs", ["status", "run_after"], unique=False)
    op.create_index(
        "uq_jobs_running_limit",
        "jobs",
        ["kind", "chain_id", sa.literal_column("coalesce(city_id, 0)")],
        unique=True,
        postgresql_where=sa.text(
            "status = 'running' and kind in ('check_chain','book','probe_signals')"
        ),
    )
    op.create_table(
        "movies",
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("city_id", sa.BigInteger(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('now_showing','coming_soon')", name=op.f("ck_movies_status")
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"], ["chains.id"], name=op.f("fk_movies_chain_id_chains")
        ),
        sa.ForeignKeyConstraint(["city_id"], ["cities.id"], name=op.f("fk_movies_city_id_cities")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_movies")),
        sa.UniqueConstraint(
            "chain_id",
            "city_id",
            "external_id",
            name=op.f("uq_movies_chain_id_city_id_external_id"),
        ),
    )
    op.create_table(
        "theatres",
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("city_id", sa.BigInteger(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"], ["chains.id"], name=op.f("fk_theatres_chain_id_chains")
        ),
        sa.ForeignKeyConstraint(
            ["city_id"], ["cities.id"], name=op.f("fk_theatres_city_id_cities")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_theatres")),
        sa.UniqueConstraint(
            "chain_id", "external_id", name=op.f("uq_theatres_chain_id_external_id")
        ),
    )
    op.create_table(
        "users",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "pending_notified", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("current_city_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('pending','approved','denied')", name=op.f("ck_users_status")
        ),
        sa.ForeignKeyConstraint(
            ["current_city_id"], ["cities.id"], name=op.f("fk_users_current_city_id_cities")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("telegram_user_id", name=op.f("uq_users_telegram_user_id")),
    )
    op.create_table(
        "alerts",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_alerts_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
        sa.UniqueConstraint("user_id", "dedupe_key", name=op.f("uq_alerts_user_id_dedupe_key")),
    )
    op.create_table(
        "showtimes",
        sa.Column("movie_id", sa.BigInteger(), nullable=False),
        sa.Column("theatre_id", sa.BigInteger(), nullable=False),
        sa.Column("external_session_id", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("format", sa.Text(), server_default="Standard", nullable=False),
        sa.Column("booking_url", sa.Text(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["movie_id"],
            ["movies.id"],
            name=op.f("fk_showtimes_movie_id_movies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["theatre_id"], ["theatres.id"], name=op.f("fk_showtimes_theatre_id_theatres")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_showtimes")),
        sa.UniqueConstraint(
            "theatre_id",
            "external_session_id",
            name=op.f("uq_showtimes_theatre_id_external_session_id"),
        ),
    )
    op.create_table(
        "watch_items",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("city_id", sa.BigInteger(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("movie_id", sa.BigInteger(), nullable=True),
        sa.Column("stage", sa.Text(), server_default="waiting", nullable=False),
        sa.Column("fast_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("format_pref", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage in ('waiting','listed','dates','venues','sessions','done')",
            name=op.f("ck_watch_items_stage"),
        ),
        sa.ForeignKeyConstraint(
            ["city_id"], ["cities.id"], name=op.f("fk_watch_items_city_id_cities")
        ),
        sa.ForeignKeyConstraint(
            ["movie_id"],
            ["movies.id"],
            name=op.f("fk_watch_items_movie_id_movies"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_watch_items_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watch_items")),
        sa.UniqueConstraint(
            "user_id", "city_id", "query", name=op.f("uq_watch_items_user_id_city_id_query")
        ),
    )
    op.create_table(
        "signals",
        sa.Column("watch_item_id", sa.BigInteger(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage in ('listed','dates','venues','sessions')", name=op.f("ck_signals_stage")
        ),
        sa.ForeignKeyConstraint(
            ["watch_item_id"],
            ["watch_items.id"],
            name=op.f("fk_signals_watch_item_id_watch_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signals")),
        sa.UniqueConstraint("watch_item_id", "stage", name=op.f("uq_signals_watch_item_id_stage")),
    )

    # Seed rows (docs/plan.md Appendix A).
    op.execute(
        "insert into cities (code, name, tz) values "
        "('phx', 'Phoenix', 'America/Phoenix'), ('blr', 'Bangalore', 'Asia/Kolkata')"
    )
    op.execute(
        "insert into chains (code, enabled) values "
        "('harkins', true), ('bms', false), ('district', false), ('pvrinox', false)"
    )
    op.execute(
        "insert into theatres (chain_id, city_id, external_id, name) "
        "select chains.id, cities.id, '16', 'Arizona Mills' "
        "from chains, cities where chains.code = 'harkins' and cities.code = 'phx'"
    )


def downgrade() -> None:
    op.drop_table("signals")
    op.drop_table("watch_items")
    op.drop_table("showtimes")
    op.drop_table("alerts")
    op.drop_table("users")
    op.drop_table("theatres")
    op.drop_table("movies")
    op.drop_index(
        "uq_jobs_running_limit",
        table_name="jobs",
        postgresql_where=sa.text(
            "status = 'running' and kind in ('check_chain','book','probe_signals')"
        ),
    )
    op.drop_index("ix_jobs_status_run_after", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("chain_snapshots")
    op.drop_table("cities")
    op.drop_table("chains")
