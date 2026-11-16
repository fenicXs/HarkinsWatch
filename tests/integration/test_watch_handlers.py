"""/watch, /list, /unwatch and /city against a real database, with a fake notifier."""

import pytest

from showwatch.bot.handlers import Env, Sender, watch
from showwatch.db.repositories import catalog, movies, users, watches
from showwatch.domain.models import MovieListing
from telegramfakes import FakeNotifier


def _sender(telegram_user_id: int) -> Sender:
    return Sender(telegram_user_id=telegram_user_id, chat_id=telegram_user_id, display_name="Ann")


async def _approved_user(env: Env, telegram_user_id: int, *, city: str | None = "phx") -> int:
    """Create an approved user (optionally with a city). Returns the user's database id."""
    async with env.session_factory() as session, session.begin():
        user, _ = await users.create_pending(
            session, telegram_user_id=telegram_user_id, chat_id=telegram_user_id, display_name="Ann"
        )
        await users.set_status(session, user.id, "approved")
        if city:
            found = await catalog.get_city_by_code(session, city)
            assert found
            await users.set_current_city(session, user.id, found.id)
        return user.id


async def _add_movie(
    env: Env, slug: str, status: str, *, city: str = "phx", title: str | None = None
):  # type: ignore[no-untyped-def]
    async with env.session_factory() as session, session.begin():
        chain = await catalog.get_chain_by_code(session, "harkins")
        found = await catalog.get_city_by_code(session, city)
        assert chain and found
        movie, _ = await movies.upsert_movie(
            session,
            chain.id,
            found.id,
            MovieListing(slug, title or slug.title(), f"https://example.test/{slug}", status),  # type: ignore[arg-type]
        )
        return movie


async def _watches(env: Env, user_id: int):  # type: ignore[no-untyped-def]
    async with env.session_factory() as session:
        return await watches.list_for_user(session, user_id)


# --- /city ------------------------------------------------------------------------------------


async def test_city_offers_every_city_and_the_pick_is_remembered(
    env: Env, notifier: FakeNotifier
) -> None:
    uid = await _approved_user(env, 1, city=None)
    await watch.handle_city(env, _sender(1))
    assert notifier.buttons_to(1) == [("Phoenix", "city:phx"), ("Bangalore", "city:blr")]

    await watch.handle_city_pick(env, _sender(1), "blr")
    assert notifier.to(1)[-1] == "City set to Bangalore."
    async with env.session_factory() as session:
        user = await users.get(session, uid)
        blr = await catalog.get_city_by_code(session, "blr")
    assert user is not None
    assert blr is not None
    assert user.current_city_id == blr.id


async def test_an_unknown_city_code_is_refused(env: Env, notifier: FakeNotifier) -> None:
    await _approved_user(env, 1)
    await watch.handle_city_pick(env, _sender(1), "atlantis")
    assert notifier.to(1) == ["That city is not available."]


# --- /watch -----------------------------------------------------------------------------------


async def test_watch_without_a_title_explains_the_command(env: Env, notifier: FakeNotifier) -> None:
    await _approved_user(env, 1)
    await watch.handle_watch(env, _sender(1), "   ")
    assert notifier.to(1) == ["Usage: /watch <movie title>"]


async def test_watch_refuses_a_very_long_title(env: Env, notifier: FakeNotifier) -> None:
    await _approved_user(env, 1)
    await watch.handle_watch(env, _sender(1), "x" * (watch.MAX_QUERY_LENGTH + 1))
    assert notifier.to(1) == ["Please use a shorter title."]


async def test_watch_needs_a_city_first(env: Env, notifier: FakeNotifier) -> None:
    uid = await _approved_user(env, 1, city=None)
    await watch.handle_watch(env, _sender(1), "dune")
    assert notifier.to(1) == [watch.NEED_CITY]
    assert await _watches(env, uid) == []


async def test_watch_offers_the_matching_movies_and_saves_nothing_yet(
    env: Env, notifier: FakeNotifier
) -> None:
    uid = await _approved_user(env, 1)
    dune = await _add_movie(env, "dune", "coming_soon", title="DUNE: Part Three")
    imax = await _add_movie(env, "dune-imax", "coming_soon", title="DUNE: Part Three (IMAX 70mm)")
    await _add_movie(env, "moana", "now_showing")

    await watch.handle_watch(env, _sender(1), "dune")

    assert notifier.to(1) == ["Which one?"]
    assert [data for _, data in notifier.buttons_to(1)] == [
        f"watchpick:{dune.id}",
        f"watchpick:{imax.id}",
        "watchfor:dune",  # "None of these": watch for the typed words instead
    ]
    assert await _watches(env, uid) == []


async def test_watch_only_searches_the_users_own_city(env: Env, notifier: FakeNotifier) -> None:
    uid = await _approved_user(env, 1, city="blr")
    await _add_movie(env, "dune", "coming_soon", city="phx")  # listed in Phoenix only

    await watch.handle_watch(env, _sender(1), "dune")

    assert notifier.buttons_to(1) == []  # not offered: the user is in Bangalore
    (item,) = await _watches(env, uid)  # saved as a query to watch for instead
    assert item.query == "dune"


async def test_watch_with_no_listed_match_keeps_watching_for_the_title(
    env: Env, notifier: FakeNotifier
) -> None:
    uid = await _approved_user(env, 1)
    await watch.handle_watch(env, _sender(1), "  Some   Unannounced  Film ")

    (item,) = await _watches(env, uid)
    assert (item.query, item.movie_id, item.stage) == ("Some Unannounced Film", None, "waiting")
    assert "keep watching" in notifier.to(1)[0]

    await watch.handle_watch(env, _sender(1), "some unannounced FILM")  # same words, other case
    assert notifier.to(1)[-1] == "You're already watching for 'some unannounced FILM'."
    assert len(await _watches(env, uid)) == 1


async def test_picking_a_coming_soon_movie_saves_a_watch(env: Env, notifier: FakeNotifier) -> None:
    uid = await _approved_user(env, 1)
    dune = await _add_movie(env, "dune", "coming_soon", title="DUNE: Part Three")

    await watch.handle_watch_pick(env, _sender(1), dune.id)
    assert notifier.to(1) == ["Watching DUNE: Part Three."]
    (item,) = await _watches(env, uid)
    assert (item.movie_id, item.query, item.stage) == (dune.id, "DUNE: Part Three", "waiting")

    await watch.handle_watch_pick(env, _sender(1), dune.id)  # a second tap
    assert notifier.to(1)[-1] == "You're already watching DUNE: Part Three."
    assert len(await _watches(env, uid)) == 1


async def test_picking_a_movie_that_is_already_playing_saves_nothing(
    env: Env, notifier: FakeNotifier
) -> None:
    uid = await _approved_user(env, 1)
    moana = await _add_movie(env, "moana", "now_showing")
    await watch.handle_watch_pick(env, _sender(1), moana.id)
    assert "already playing" in notifier.to(1)[0]
    assert await _watches(env, uid) == []


async def test_picking_a_movie_from_another_city_or_a_missing_one_is_refused(
    env: Env, notifier: FakeNotifier
) -> None:
    uid = await _approved_user(env, 1, city="blr")
    elsewhere = await _add_movie(env, "dune", "coming_soon", city="phx")
    await watch.handle_watch_pick(env, _sender(1), elsewhere.id)
    await watch.handle_watch_pick(env, _sender(1), 999_999)
    assert notifier.to(1) == ["That movie is no longer available."] * 2
    assert await _watches(env, uid) == []


# --- /list and /unwatch -----------------------------------------------------------------------


async def test_list_shows_only_the_callers_watches_with_their_stage(
    env: Env, notifier: FakeNotifier
) -> None:
    ann = await _approved_user(env, 1)
    await _approved_user(env, 2)
    dune = await _add_movie(env, "dune", "coming_soon", title="DUNE: Part Three")
    await watch.handle_watch_pick(env, _sender(1), dune.id)
    await watch.handle_watch(env, _sender(1), "an unlisted film")
    await watch.handle_watch(env, _sender(2), "bo's film")

    async with env.session_factory() as session, session.begin():
        (first, *_) = await watches.list_for_user(session, ann)
        await watches.set_stage(session, first.id, "dates")
    notifier.sent.clear()
    await watch.handle_list(env, _sender(1))

    (text,) = notifier.to(1)
    assert text == (
        "Your watches:\n"
        "1. DUNE: Part Three: dates are out\n"
        "2. an unlisted film: waiting for it to be listed"
    )
    assert "bo's film" not in text


async def test_list_and_unwatch_say_so_when_there_is_nothing(
    env: Env, notifier: FakeNotifier
) -> None:
    await _approved_user(env, 1)
    await watch.handle_list(env, _sender(1))
    await watch.handle_unwatch(env, _sender(1))
    assert notifier.to(1) == ["You're not watching anything."] * 2


async def test_unwatch_offers_the_callers_watches_by_id_and_removes_the_one_tapped(
    env: Env, notifier: FakeNotifier
) -> None:
    uid = await _approved_user(env, 1)
    await watch.handle_watch(env, _sender(1), "film a")
    await watch.handle_watch(env, _sender(1), "film b")
    first, second = await _watches(env, uid)
    notifier.sent.clear()

    await watch.handle_unwatch(env, _sender(1))
    assert notifier.buttons_to(1) == [
        ("film a", f"unwatch:{first.id}"),
        ("film b", f"unwatch:{second.id}"),
    ]

    await watch.handle_unwatch_pick(env, _sender(1), first.id)
    assert notifier.to(1)[-1] == "Removed film a."
    assert [w.id for w in await _watches(env, uid)] == [second.id]

    await watch.handle_unwatch_pick(env, _sender(1), first.id)  # an old button, tapped again
    assert notifier.to(1)[-1] == "That watch is already gone."


async def test_one_user_cannot_remove_another_users_watch(env: Env, notifier: FakeNotifier) -> None:
    ann = await _approved_user(env, 1)
    await _approved_user(env, 2)
    await watch.handle_watch(env, _sender(1), "ann's film")
    (item,) = await _watches(env, ann)

    await watch.handle_unwatch_pick(env, _sender(2), item.id)  # Bo taps Ann's button id

    assert notifier.to(2) == ["That watch is already gone."]
    assert len(await _watches(env, ann)) == 1  # still there


# --- people who are not approved --------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        lambda env, s: watch.handle_watch(env, s, "dune"),
        lambda env, s: watch.handle_watch_pick(env, s, 1),
        lambda env, s: watch.handle_list(env, s),
        lambda env, s: watch.handle_unwatch(env, s),
        lambda env, s: watch.handle_unwatch_pick(env, s, 1),
        lambda env, s: watch.handle_city_pick(env, s, "phx"),
    ],
)
async def test_handlers_do_nothing_for_someone_who_is_not_approved(
    env: Env,
    notifier: FakeNotifier,
    action,  # type: ignore[no-untyped-def]
) -> None:
    async with env.session_factory() as session, session.begin():
        await users.create_pending(session, telegram_user_id=5, chat_id=5, display_name="Eve")
    await action(env, _sender(5))  # pending user
    await action(env, _sender(6))  # not even known
    assert notifier.sent == []


# --- "None of these", and list limits ---------------------------------------------------------


async def test_none_of_these_watches_for_the_typed_words(env: Env, notifier: FakeNotifier) -> None:
    uid = await _approved_user(env, 1)
    await watch.handle_watch_for(env, _sender(1), "  Dune   Messiah ")
    (item,) = await _watches(env, uid)
    assert (item.query, item.movie_id, item.stage) == ("Dune Messiah", None, "waiting")
    assert "keep watching" in notifier.to(1)[0]

    await watch.handle_watch_for(env, _sender(1), "dune messiah")  # a second tap, other case
    assert notifier.to(1)[-1] == "You're already watching for 'dune messiah'."
    assert len(await _watches(env, uid)) == 1


async def test_none_of_these_needs_a_city_and_a_sensible_title(
    env: Env, notifier: FakeNotifier
) -> None:
    no_city = await _approved_user(env, 1, city=None)
    await watch.handle_watch_for(env, _sender(1), "dune")
    assert notifier.to(1) == [watch.NEED_CITY]
    assert await _watches(env, no_city) == []

    uid = await _approved_user(env, 2)
    await watch.handle_watch_for(env, _sender(2), "x" * (watch.MAX_QUERY_LENGTH + 1))
    await watch.handle_watch_for(env, _sender(2), "   ")
    assert notifier.to(2) == ["Please use a shorter title."] * 2
    assert await _watches(env, uid) == []


async def test_a_long_title_gets_choices_but_no_none_of_these_button(
    env: Env, notifier: FakeNotifier
) -> None:
    """The button carries the title in 64 bytes of callback data; a long one cannot fit."""
    await _approved_user(env, 1)
    long_title = "A Very Long Film Title " * 3  # 69 characters
    await _add_movie(env, "long", "coming_soon", title=long_title.strip())
    await watch.handle_watch(env, _sender(1), long_title.strip())
    assert [d for _, d in notifier.buttons_to(1)][0].startswith("watchpick:")
    assert not any(d.startswith("watchfor:") for _, d in notifier.buttons_to(1))


async def test_list_and_unwatch_are_capped_so_telegram_accepts_them(
    env: Env, notifier: FakeNotifier
) -> None:
    uid = await _approved_user(env, 1)
    total = watch.MAX_LISTED + 5
    async with env.session_factory() as session, session.begin():
        phx = await catalog.get_city_by_code(session, "phx")
        assert phx
        for n in range(total):
            await watches.add(session, user_id=uid, city_id=phx.id, query=f"film {n:02d}")

    await watch.handle_list(env, _sender(1))
    text = notifier.to(1)[-1]
    assert text.count("\n") == watch.MAX_LISTED + 1  # header + 30 items + the "more" line
    assert text.endswith("...and 5 more.")

    await watch.handle_unwatch(env, _sender(1))
    assert len(notifier.buttons_to(1)) == watch.MAX_LISTED


async def test_a_stale_movie_button_without_a_city_asks_for_a_city(
    env: Env, notifier: FakeNotifier
) -> None:
    await _approved_user(env, 1, city=None)
    dune = await _add_movie(env, "dune", "coming_soon")
    await watch.handle_watch_pick(env, _sender(1), dune.id)
    assert notifier.to(1) == [watch.NEED_CITY]
