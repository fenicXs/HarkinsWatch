"""Repositories against a real database. Each test starts with empty mutable tables."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.db.repositories import alerts, catalog, movies, signals, users, watches
from showwatch.domain.models import MovieListing, ShowtimeListing


async def _phx(session: AsyncSession) -> int:
    city = await catalog.get_city_by_code(session, "phx")
    assert city
    return city.id


async def _harkins(session: AsyncSession) -> int:
    chain = await catalog.get_chain_by_code(session, "harkins")
    assert chain
    return chain.id


def _listing(slug: str, status: str = "now_showing", title: str | None = None) -> MovieListing:
    return MovieListing(
        external_id=slug,
        title=title or slug.title(),
        url=f"https://www.harkins.com/movies/{slug}",
        status=status,  # type: ignore[arg-type]
    )


# --- catalog ----------------------------------------------------------------------------------


async def test_catalog_returns_the_seeded_rows(session: AsyncSession) -> None:
    cities = await catalog.list_cities(session)
    assert [c.code for c in cities] == ["phx", "blr"]
    assert (await catalog.get_city(session, cities[1].id)) == cities[1]
    assert await catalog.get_city(session, 999_999) is None
    assert await catalog.get_city_by_code(session, "nowhere") is None
    assert await catalog.get_chain_by_code(session, "nowhere") is None
    assert [c.code for c in await catalog.list_enabled_chains(session)] == ["harkins"]
    theatres = await catalog.list_theatres(session, await _harkins(session), await _phx(session))
    assert [(t.external_id, t.name) for t in theatres] == [("16", "Arizona Mills")]
    assert await catalog.list_theatres(session, await _harkins(session), cities[1].id) == []


# --- users ------------------------------------------------------------------------------------


async def test_create_pending_is_idempotent(session: AsyncSession) -> None:
    user, created = await users.create_pending(
        session, telegram_user_id=111, chat_id=111, display_name="Ann"
    )
    assert created
    assert (user.status, user.is_admin, user.pending_notified) == ("pending", False, False)
    again, created_again = await users.create_pending(
        session, telegram_user_id=111, chat_id=111, display_name="Ann"
    )
    assert not created_again
    assert again.id == user.id
    assert await users.get(session, user.id) == again
    assert await users.get_by_telegram_id(session, 111) == again
    assert await users.get(session, 999_999) is None
    assert await users.get_by_telegram_id(session, 222) is None


async def test_upsert_admin_creates_then_promotes(session: AsyncSession) -> None:
    admin = await users.upsert_admin(session, telegram_user_id=42, display_name="Root")
    assert (admin.status, admin.is_admin, admin.chat_id) == ("approved", True, None)
    pending, _ = await users.create_pending(
        session, telegram_user_id=43, chat_id=43, display_name="Bo"
    )
    promoted = await users.upsert_admin(session, telegram_user_id=43)
    assert promoted.id == pending.id
    assert (promoted.status, promoted.is_admin) == ("approved", True)


async def test_status_chat_city_and_notified_updates(session: AsyncSession) -> None:
    user, _ = await users.create_pending(
        session, telegram_user_id=5, chat_id=None, display_name=None
    )
    approved = await users.set_status(session, user.id, "approved")
    assert approved.status == "approved"
    await users.set_chat_id(session, user.id, 555)
    await users.mark_pending_notified(session, user.id)
    phx = await _phx(session)
    await users.set_current_city(session, user.id, phx)
    fresh = await users.get(session, user.id)
    assert fresh
    assert (fresh.chat_id, fresh.pending_notified, fresh.current_city_id) == (555, True, phx)
    for call in (
        users.set_status(session, 999_999, "denied"),
        users.set_chat_id(session, 999_999, 1),
        users.mark_pending_notified(session, 999_999),
        users.set_current_city(session, 999_999, phx),
    ):
        with pytest.raises(LookupError):
            await call


async def test_list_approved_in_city_only_returns_approved_users_of_that_city(
    session: AsyncSession,
) -> None:
    phx = await _phx(session)
    blr = (await catalog.get_city_by_code(session, "blr")).id  # type: ignore[union-attr]
    ids = {}
    for tg, status, city in [(1, "approved", phx), (2, "pending", phx), (3, "approved", blr)]:
        user, _ = await users.create_pending(
            session, telegram_user_id=tg, chat_id=tg, display_name=None
        )
        await users.set_status(session, user.id, status)  # type: ignore[arg-type]
        await users.set_current_city(session, user.id, city)
        ids[tg] = user.id
    listed = await users.list_approved_in_city(session, phx)
    assert [u.id for u in listed] == [ids[1]]


# --- watches ----------------------------------------------------------------------------------


async def _user(session: AsyncSession, tg: int = 1) -> int:
    user, _ = await users.create_pending(
        session, telegram_user_id=tg, chat_id=tg, display_name=None
    )
    return user.id


async def test_a_watch_is_saved_once_per_user_city_and_query(session: AsyncSession) -> None:
    uid, phx = await _user(session), await _phx(session)
    first, created = await watches.add(session, user_id=uid, city_id=phx, query="dune")
    assert created
    assert (first.stage, first.movie_id, first.fast_until) == ("waiting", None, None)
    again, created_again = await watches.add(session, user_id=uid, city_id=phx, query="dune")
    assert not created_again
    assert again.id == first.id
    assert await watches.get(session, first.id) == first
    assert await watches.get(session, 999_999) is None
    assert [w.id for w in await watches.list_for_user(session, uid)] == [first.id]


async def test_users_only_see_and_delete_their_own_watches(session: AsyncSession) -> None:
    ann, bo, phx = await _user(session, 1), await _user(session, 2), await _phx(session)
    watch, _ = await watches.add(session, user_id=ann, city_id=phx, query="dune")
    assert await watches.list_for_user(session, bo) == []
    assert not await watches.delete_for_user(session, bo, watch.id)  # not Bo's watch
    assert await watches.get(session, watch.id) is not None
    assert await watches.delete_for_user(session, ann, watch.id)
    assert await watches.get(session, watch.id) is None


async def test_stage_movie_and_listing_queries(session: AsyncSession) -> None:
    uid, phx, chain = await _user(session), await _phx(session), await _harkins(session)
    movie, _ = await movies.upsert_movie(session, chain, phx, _listing("dune"))
    waiting, _ = await watches.add(session, user_id=uid, city_id=phx, query="dune")
    other, _ = await watches.add(session, user_id=uid, city_id=phx, query="odyssey")

    assert {w.id for w in await watches.list_unmatched(session, phx)} == {waiting.id, other.id}
    await watches.set_movie(session, waiting.id, movie.id)
    assert [w.id for w in await watches.list_unmatched(session, phx)] == [other.id]

    soon = datetime(2026, 11, 20, tzinfo=UTC)
    updated = await watches.set_stage(session, waiting.id, "listed", fast_until=soon)
    assert (updated.stage, updated.fast_until, updated.movie_id) == ("listed", soon, movie.id)
    kept = await watches.set_stage(session, waiting.id, "dates")  # fast_until is kept
    assert kept.fast_until == soon

    assert [w.id for w in await watches.list_by_stage(session, ["dates", "venues"])] == [waiting.id]
    assert await watches.list_by_stage(session, ["dates"], city_id=999_999) == []
    done = await watches.set_stage(session, other.id, "done")
    assert done.stage == "done"
    assert other.id not in {w.id for w in await watches.list_unmatched(session, phx)}

    with pytest.raises(LookupError):
        await watches.set_movie(session, 999_999, movie.id)
    with pytest.raises(LookupError):
        await watches.set_stage(session, 999_999, "done")


async def test_deleting_a_watch_removes_its_signals(session: AsyncSession) -> None:
    uid, phx = await _user(session), await _phx(session)
    watch, _ = await watches.add(session, user_id=uid, city_id=phx, query="dune")
    assert await signals.record(session, watch.id, "listed", {"url": "x"})
    await watches.delete_for_user(session, uid, watch.id)
    assert await signals.stages_seen(session, watch.id) == []


# --- movies and showtimes -----------------------------------------------------------------------


async def test_upsert_movie_reports_what_changed(session: AsyncSession) -> None:
    phx, chain = await _phx(session), await _harkins(session)
    movie, change = await movies.upsert_movie(
        session, chain, phx, _listing("dune", "coming_soon", "DUNE: Part Three")
    )
    assert change == "inserted"
    assert (movie.status, movie.normalized_title) == ("coming_soon", "dune part three")

    same, change = await movies.upsert_movie(
        session, chain, phx, _listing("dune", "coming_soon", "DUNE: Part Three")
    )
    assert (change, same.id) == ("unchanged", movie.id)

    released, change = await movies.upsert_movie(
        session, chain, phx, _listing("dune", "now_showing")
    )
    assert (change, released.status) == ("status_changed", "now_showing")

    # Seen again on the Coming Soon page: stays now_showing and is not a new release.
    kept, change = await movies.upsert_movie(session, chain, phx, _listing("dune", "coming_soon"))
    assert (change, kept.status) == ("unchanged", "now_showing")


async def test_a_movie_inserted_as_now_showing_is_a_new_release(session: AsyncSession) -> None:
    phx, chain = await _phx(session), await _harkins(session)
    movie, change = await movies.upsert_movie(session, chain, phx, _listing("moana"))
    assert (change, movie.status) == ("inserted", "now_showing")
    assert await movies.get(session, movie.id) == movie
    assert await movies.get(session, 999_999) is None


async def test_movies_are_listed_per_city_and_status(session: AsyncSession) -> None:
    phx, chain = await _phx(session), await _harkins(session)
    blr = (await catalog.get_city_by_code(session, "blr")).id  # type: ignore[union-attr]
    await movies.upsert_movie(session, chain, phx, _listing("dune", "coming_soon"))
    await movies.upsert_movie(session, chain, phx, _listing("moana"))
    await movies.upsert_movie(session, chain, blr, _listing("moana"))  # same slug, other city
    assert [m.external_id for m in await movies.list_for_city(session, phx)] == ["dune", "moana"]
    assert [
        m.external_id for m in await movies.list_for_city(session, phx, status="now_showing")
    ] == ["moana"]
    assert len(await movies.list_for_city(session, blr)) == 1


async def test_showtimes_are_updated_not_duplicated(session: AsyncSession) -> None:
    phx, chain = await _phx(session), await _harkins(session)
    theatre = (await catalog.list_theatres(session, chain, phx))[0]
    movie, _ = await movies.upsert_movie(session, chain, phx, _listing("moana"))
    start = datetime(2026, 11, 17, 19, 0, tzinfo=UTC)
    listings = [
        ShowtimeListing("568953", "16", start, "Standard", "https://h/568953"),
        ShowtimeListing("568954", "16", start + timedelta(hours=3), "IMAX", "https://h/568954"),
    ]
    stored = await movies.upsert_showtimes(session, movie.id, theatre.id, listings)
    assert [(s.external_session_id, s.format) for s in stored] == [
        ("568953", "Standard"),
        ("568954", "IMAX"),
    ]

    changed = [ShowtimeListing("568953", "16", start, "CINÉ XL", "https://h/568953")]
    again = await movies.upsert_showtimes(session, movie.id, theatre.id, changed)
    assert len(again) == 2  # nothing duplicated
    assert {s.external_session_id: s.format for s in again}["568953"] == "CINÉ XL"

    late = await movies.list_showtimes(
        session, movie.id, theatre.id, starts_from=start + timedelta(hours=1)
    )
    assert [s.external_session_id for s in late] == ["568954"]
    early = await movies.list_showtimes(
        session, movie.id, theatre.id, starts_before=start + timedelta(hours=1)
    )
    assert [s.external_session_id for s in early] == ["568953"]


# --- snapshots ----------------------------------------------------------------------------------


async def test_snapshots_flag_first_runs_and_broken_scrapes(session: AsyncSession) -> None:
    phx, chain = await _phx(session), await _harkins(session)

    empty, first_run = await movies.record_snapshot(session, chain, phx, 0)
    assert (empty.status, first_run) == ("suspect", False)  # zero movies is never a baseline

    baseline, first_run = await movies.record_snapshot(session, chain, phx, 60)
    assert (baseline.status, first_run) == ("good", True)

    normal, first_run = await movies.record_snapshot(session, chain, phx, 55)
    assert (normal.status, first_run) == ("good", False)

    half, _ = await movies.record_snapshot(session, chain, phx, 27)  # below 50% of 55
    assert half.status == "suspect"
    edge, _ = await movies.record_snapshot(session, chain, phx, 28)  # 28 >= 27.5
    assert edge.status == "good"

    # A suspect scrape is not used as the comparison point for the next one.
    await movies.record_snapshot(session, chain, phx, 1)
    after, _ = await movies.record_snapshot(session, chain, phx, 30)
    assert after.status == "good"  # compared with 28, not with 1


# --- alerts and signals -------------------------------------------------------------------------


async def test_an_alert_is_recorded_once_per_user_and_key(session: AsyncSession) -> None:
    ann, bo = await _user(session, 1), await _user(session, 2)
    assert await alerts.try_record(session, ann, "new_release:7")
    assert not await alerts.try_record(session, ann, "new_release:7")  # duplicate: skip
    assert await alerts.try_record(session, bo, "new_release:7")  # another user
    assert await alerts.try_record(session, ann, "new_release:8")  # another alert


async def test_each_signal_stage_is_stored_once(session: AsyncSession) -> None:
    uid, phx = await _user(session), await _phx(session)
    watch, _ = await watches.add(session, user_id=uid, city_id=phx, query="dune")
    assert await signals.record(session, watch.id, "listed", {"page": "a"})
    assert not await signals.record(session, watch.id, "listed", {"page": "b"})
    assert await signals.record(session, watch.id, "dates", {"count": 3})
    assert await signals.stages_seen(session, watch.id) == ["listed", "dates"]


async def test_list_pending_returns_only_users_still_waiting(session: AsyncSession) -> None:
    waiting, _ = await users.create_pending(
        session, telegram_user_id=1, chat_id=1, display_name=None
    )
    approved, _ = await users.create_pending(
        session, telegram_user_id=2, chat_id=2, display_name=None
    )
    denied, _ = await users.create_pending(
        session, telegram_user_id=3, chat_id=3, display_name=None
    )
    await users.set_status(session, approved.id, "approved")
    await users.set_status(session, denied.id, "denied")
    assert [u.id for u in await users.list_pending(session)] == [waiting.id]
