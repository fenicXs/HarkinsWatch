"""SQLAlchemy tables, as specified in docs/plan.md Appendix A."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Job kinds that may run once at a time (see claim() in db/queue.py and Appendix B).
LIMITED_JOB_KINDS = ("check_chain", "book", "probe_signals")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class _Common:
    """`id`, `created_at`, `updated_at` on every table."""

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class City(_Common, Base):
    __tablename__ = "cities"

    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    tz: Mapped[str] = mapped_column(Text)


class Chain(_Common, Base):
    __tablename__ = "chains"

    code: Mapped[str] = mapped_column(Text, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(_Common, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("status in ('pending','approved','denied')", name="status"),)

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    display_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    pending_notified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    current_city_id: Mapped[int | None] = mapped_column(ForeignKey("cities.id"))


class Theatre(_Common, Base):
    __tablename__ = "theatres"
    __table_args__ = (UniqueConstraint("chain_id", "external_id"),)

    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"))
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"))
    external_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)


class Movie(_Common, Base):
    __tablename__ = "movies"
    __table_args__ = (
        UniqueConstraint("chain_id", "city_id", "external_id"),
        CheckConstraint("status in ('now_showing','coming_soon')", name="status"),
    )

    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"))
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"))
    external_id: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    normalized_title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When this movie last appeared on a good scrape, and when it went on sale (None for the
    # baseline scrape, so the first run never alerts).
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Showtime(_Common, Base):
    __tablename__ = "showtimes"
    __table_args__ = (UniqueConstraint("theatre_id", "external_session_id"),)

    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    theatre_id: Mapped[int] = mapped_column(ForeignKey("theatres.id"))
    external_session_id: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    format: Mapped[str] = mapped_column(Text, server_default="Standard")
    booking_url: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WatchItem(_Common, Base):
    __tablename__ = "watch_items"
    __table_args__ = (
        UniqueConstraint("user_id", "city_id", "query"),
        CheckConstraint(
            "stage in ('waiting','listed','dates','venues','sessions','done')", name="stage"
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"))
    query: Mapped[str] = mapped_column(Text)
    movie_id: Mapped[int | None] = mapped_column(ForeignKey("movies.id", ondelete="SET NULL"))
    stage: Mapped[str] = mapped_column(Text, server_default="waiting")
    fast_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    format_pref: Mapped[str | None] = mapped_column(Text)


class Signal(_Common, Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("watch_item_id", "stage"),
        CheckConstraint("stage in ('listed','dates','venues','sessions')", name="stage"),
    )

    watch_item_id: Mapped[int] = mapped_column(ForeignKey("watch_items.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Alert(_Common, Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    dedupe_key: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(_Common, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status in ('queued','running','done','failed')", name="status"),
        Index("ix_jobs_status_run_after", "status", "run_after"),
        # At most one running job per (kind, chain, city) for the limited kinds. check_chain
        # jobs carry a city (one per chain + city); book and probe_signals carry none (one per
        # chain). This is the database-level guard behind claim()'s skip predicate.
        Index(
            "uq_jobs_running_limit",
            "kind",
            "chain_id",
            text("coalesce(city_id, 0)"),
            unique=True,
            postgresql_where=text(
                "status = 'running' and kind in ('check_chain','book','probe_signals')"
            ),
        ),
    )

    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer)
    chain_id: Mapped[int | None] = mapped_column(ForeignKey("chains.id"))
    city_id: Mapped[int | None] = mapped_column(ForeignKey("cities.id"))
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ChainSnapshot(_Common, Base):
    __tablename__ = "chain_snapshots"
    __table_args__ = (CheckConstraint("status in ('good','suspect')", name="status"),)

    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"))
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"))
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    movie_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
