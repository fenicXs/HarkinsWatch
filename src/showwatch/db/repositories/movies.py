"""Movies, showtimes and the per-chain scrape snapshots that guard the baseline."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import Text, and_, cast, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.db import tables
from showwatch.domain.models import (
    ChainSnapshot,
    Movie,
    MovieListing,
    MovieStatus,
    Showtime,
    ShowtimeListing,
    User,
)
from showwatch.domain.title_match import normalize_title

# A scrape below this share of the last good movie count is treated as a broken scrape.
SUSPECT_BELOW_FRACTION = 0.5

# A movie not seen on a good scrape for this long has left the lists.
LISTED_WINDOW = timedelta(days=2)

MovieChange = Literal["inserted", "status_changed", "unchanged"]


async def get(session: AsyncSession, movie_id: int) -> Movie | None:
    row = await session.get(tables.Movie, movie_id)
    return Movie.model_validate(row) if row else None


async def upsert_movie(
    session: AsyncSession,
    chain_id: int,
    city_id: int,
    listing: MovieListing,
    *,
    mark_release: bool = True,
) -> tuple[Movie, MovieChange]:
    """Insert or update one movie. The change tells the caller whether it is a "new release".

    A new release is a row inserted as `now_showing`, or a `coming_soon` row that becomes
    `now_showing`. A title seen on both pages stays `now_showing` (never downgraded). Every
    call refreshes `last_seen_at`. `released_at` is set on a new release unless `mark_release`
    is False (the baseline scrape, so the first run never alerts).
    """
    row = await session.scalar(
        select(tables.Movie).where(
            tables.Movie.chain_id == chain_id,
            tables.Movie.city_id == city_id,
            tables.Movie.external_id == listing.external_id,
        )
    )
    if row is None:
        row = tables.Movie(
            chain_id=chain_id,
            city_id=city_id,
            external_id=listing.external_id,
            title=listing.title,
            normalized_title=normalize_title(listing.title),
            url=listing.url,
            status=listing.status,
            last_seen_at=func.now(),
            released_at=func.now() if mark_release and listing.status == "now_showing" else None,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        return Movie.model_validate(row), "inserted"

    change: MovieChange = "unchanged"
    if row.status == "coming_soon" and listing.status == "now_showing":
        row.status = "now_showing"
        change = "status_changed"
        if mark_release:
            row.released_at = func.now()
    row.title = listing.title
    row.normalized_title = normalize_title(listing.title)
    row.url = listing.url
    row.last_seen_at = func.now()
    await session.flush()
    await session.refresh(row)
    return Movie.model_validate(row), change


async def list_for_city(
    session: AsyncSession,
    city_id: int,
    *,
    status: MovieStatus | None = None,
    current_only: bool = False,
) -> list[Movie]:
    """Movies in a city. `current_only` keeps those seen on a good scrape in the last 2 days,
    so a movie that left theatres is not offered or matched by a new watch."""
    stmt = select(tables.Movie).where(tables.Movie.city_id == city_id)
    if status is not None:
        stmt = stmt.where(tables.Movie.status == status)
    if current_only:
        stmt = stmt.where(tables.Movie.last_seen_at >= func.now() - LISTED_WINDOW)
    rows = await session.scalars(stmt.order_by(tables.Movie.id))
    return [Movie.model_validate(r) for r in rows]


async def unalerted_releases(
    session: AsyncSession, chain_id: int, city_id: int, since: datetime
) -> list[tuple[User, Movie]]:
    """New-release alerts still owed for one chain: (user, movie) pairs with no delivered alert.

    Deciding an alert and delivering it are separate steps and Telegram can refuse a message,
    so this is how an undelivered alert is found again. Only movies that went on sale since
    `since`; only users who existed at that moment; and not users who watch that movie (they get
    the watch alert instead).
    """
    alert_key = literal("new_release:") + cast(tables.Movie.id, Text)
    watching = (
        select(tables.WatchItem.id)
        .where(
            tables.WatchItem.user_id == tables.User.id, tables.WatchItem.movie_id == tables.Movie.id
        )
        .exists()
    )
    rows = await session.execute(
        select(tables.User, tables.Movie)
        .join(
            tables.Movie,
            and_(
                tables.Movie.chain_id == chain_id,
                tables.Movie.city_id == tables.User.current_city_id,
                tables.Movie.released_at.is_not(None),
                tables.Movie.released_at >= since,
                tables.Movie.released_at >= tables.User.created_at,
                tables.Movie.status == "now_showing",
                tables.Movie.last_seen_at >= func.now() - LISTED_WINDOW,
            ),
        )
        .outerjoin(
            tables.Alert,
            and_(tables.Alert.user_id == tables.User.id, tables.Alert.dedupe_key == alert_key),
        )
        .where(
            tables.User.status == "approved",
            tables.User.current_city_id == city_id,
            tables.Alert.id.is_(None),
            ~watching,
        )
        .order_by(tables.Movie.id, tables.User.id)
    )
    return [(User.model_validate(u), Movie.model_validate(m)) for u, m in rows.all()]


async def upsert_showtimes(
    session: AsyncSession,
    movie_id: int,
    theatre_id: int,
    listings: Sequence[ShowtimeListing],
) -> list[Showtime]:
    """Store the sessions a chain returned; a session seen again is updated, not duplicated."""
    for listing in listings:
        stmt = insert(tables.Showtime).values(
            movie_id=movie_id,
            theatre_id=theatre_id,
            external_session_id=listing.external_session_id,
            starts_at=listing.starts_at,
            format=listing.format,
            booking_url=listing.booking_url,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_showtimes_theatre_id_external_session_id",
            set_={
                "movie_id": stmt.excluded.movie_id,
                "starts_at": stmt.excluded.starts_at,
                "format": stmt.excluded.format,
                "booking_url": stmt.excluded.booking_url,
                "updated_at": func.now(),  # onupdate does not fire for ON CONFLICT
            },
        )
        await session.execute(stmt)
    return await list_showtimes(session, movie_id, theatre_id)


async def list_showtimes(
    session: AsyncSession,
    movie_id: int,
    theatre_id: int,
    *,
    starts_from: datetime | None = None,
    starts_before: datetime | None = None,
    fresh_within: timedelta | None = None,
) -> list[Showtime]:
    """Stored sessions for a movie at a theatre. `fresh_within` keeps only sessions a lookup
    returned recently (judged by the database clock that stamped `updated_at`), so sessions the
    chain stopped returning are left out."""
    stmt = select(tables.Showtime).where(
        tables.Showtime.movie_id == movie_id, tables.Showtime.theatre_id == theatre_id
    )
    if starts_from is not None:
        stmt = stmt.where(tables.Showtime.starts_at >= starts_from)
    if starts_before is not None:
        stmt = stmt.where(tables.Showtime.starts_at < starts_before)
    if fresh_within is not None:
        stmt = stmt.where(tables.Showtime.updated_at >= func.now() - fresh_within)
    rows = await session.scalars(stmt.order_by(tables.Showtime.starts_at, tables.Showtime.id))
    return [Showtime.model_validate(r) for r in rows]


async def record_snapshot(
    session: AsyncSession, chain_id: int, city_id: int, movie_count: int
) -> tuple[ChainSnapshot, bool]:
    """Store the result of one scrape and judge it.

    Returns the snapshot and whether it is the first good one for this chain and city (the
    caller stores movies but sends no alerts on a first run). A scrape is `suspect` when it
    returned no movies or fewer than half of the last good count; callers must not write
    movies or send alerts for a suspect scrape.
    """
    last_good = await session.scalar(
        select(tables.ChainSnapshot)
        .where(
            tables.ChainSnapshot.chain_id == chain_id,
            tables.ChainSnapshot.city_id == city_id,
            tables.ChainSnapshot.status == "good",
        )
        .order_by(tables.ChainSnapshot.id.desc())
        .limit(1)
    )
    suspect = movie_count == 0 or (
        last_good is not None and movie_count < last_good.movie_count * SUSPECT_BELOW_FRACTION
    )
    row = tables.ChainSnapshot(
        chain_id=chain_id,
        city_id=city_id,
        movie_count=movie_count,
        status="suspect" if suspect else "good",
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    first_run = not suspect and last_good is None
    return ChainSnapshot.model_validate(row), first_run
