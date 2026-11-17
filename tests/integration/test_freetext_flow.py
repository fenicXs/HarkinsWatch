"""Plain-language requests end to end: message -> read-back -> Confirm -> the right action."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text

from llmfakes import FakeLLM
from showwatch.bot import keyboards
from showwatch.bot.handlers import Env, Sender, freetext
from showwatch.db.repositories import catalog, movies, requests, signals, users, watches
from showwatch.domain.formats import FormatPref
from showwatch.domain.models import Job
from showwatch.llm.tasks import intent
from showwatch.worker.handlers import WorkerEnv, search_showtimes
from telegramfakes import FakeNotifier
from workerfakes import FakeChain, add_watch, approved_user, listing, make_worker_env, showtime

# 02:00 UTC on the 17th is 19:00 on the 16th in Phoenix: "today" there is Nov 16.
CLOCK = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)
HELP = "help text"
DUNE = "Dune IMAX Friday evening, 2 seats"
DUNE_JSON = (
    '{"title": "Dune", "date_from": "2026-11-20", "date_to": "2026-11-20", '
    '"part_of_day": "evening", "format": "IMAX", "seats": 2, "confidence": 0.95}'
)


def _sender(telegram_user_id: int) -> Sender:
    return Sender(telegram_user_id=telegram_user_id, chat_id=telegram_user_id, display_name="Ann")


def _with(env: Env, llm: FakeLLM | None) -> Env:
    return replace(env, llm=llm, clock=lambda: CLOCK)


async def _movie(
    env: Env, chain: str, city: str, slug: str, title: str, status: str = "now_showing"
) -> int:
    async with env.session_factory() as session, session.begin():
        found_chain = await catalog.get_chain_by_code(session, chain)
        found_city = await catalog.get_city_by_code(session, city)
        assert found_chain and found_city
        movie, _ = await movies.upsert_movie(
            session, found_chain.id, found_city.id, listing(slug, status, title)
        )
        return movie.id


async def _jobs(env: Env) -> list[dict]:  # type: ignore[type-arg]
    async with env.session_factory() as session:
        rows = (
            await session.execute(
                text("select payload from jobs where kind = 'search_showtimes' order by id")
            )
        ).all()
    return [r.payload for r in rows]


async def _pending_requests(env: Env) -> int:
    async with env.session_factory() as session:
        return int(await session.scalar(text("select count(*) from parsed_requests")))


def _confirm_id(notifier: FakeNotifier, chat: int) -> int:
    (label, data), *_ = [b for b in notifier.buttons_to(chat) if b[0] == "Confirm"][-1:]
    parsed = keyboards.parse_callback(data)
    assert parsed and parsed[0] == keyboards.INTENT
    return int(parsed[1])


# --- Phoenix: a chain that lists sessions -------------------------------------------------------


async def test_a_request_for_a_harkins_movie_queues_filtered_showtime_searches(
    env: Env, notifier: FakeNotifier
) -> None:
    env = _with(env, FakeLLM(intents={DUNE: DUNE_JSON}))
    user = await approved_user(env.session_factory, 1)
    movie_id = await _movie(env, "harkins", "phx", "dune", "Dune")

    await freetext.handle_text(env, _sender(1), DUNE, HELP)

    (message,) = notifier.to(1)
    assert message.startswith("Dune, Fri Nov 20, evening, IMAX.")
    assert "look up showtimes" in message
    assert await _jobs(env) == []  # nothing is done until Confirm
    request_id = _confirm_id(notifier, 1)

    await freetext.handle_intent(env, _sender(1), request_id)

    assert notifier.to(1)[-1] == "Looking up showtimes for 1 day…"
    assert await _jobs(env) == [
        {
            "user_id": user.id,
            "movie_id": movie_id,
            "day": "2026-11-20",
            "part_of_day": "evening",
            "format": "IMAX",
        }
    ]
    assert await _pending_requests(env) == 0


async def test_a_date_range_queues_one_search_per_day_and_skips_duplicates(
    env: Env, notifier: FakeNotifier
) -> None:
    reply = (
        '{"title": "Dune", "date_from": "2026-11-20", "date_to": "2026-11-22", "confidence": 0.9}'
    )
    env = _with(env, FakeLLM(intents={"dune weekend": reply}))
    await approved_user(env.session_factory, 1)
    await _movie(env, "harkins", "phx", "dune", "Dune")

    for _ in range(2):  # the same request twice: the second adds nothing
        await freetext.handle_text(env, _sender(1), "dune weekend", HELP)
        await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    assert [j["day"] for j in await _jobs(env)] == ["2026-11-20", "2026-11-21", "2026-11-22"]
    assert all("part_of_day" not in j and "format" not in j for j in await _jobs(env))


async def test_without_a_date_the_date_picker_is_shown(env: Env, notifier: FakeNotifier) -> None:
    reply = '{"title": "Dune", "confidence": 0.9}'
    env = _with(env, FakeLLM(intents={"dune": reply}))
    await approved_user(env.session_factory, 1)
    await _movie(env, "harkins", "phx", "dune", "Dune")

    await freetext.handle_text(env, _sender(1), "dune", HELP)
    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    assert notifier.to(1)[-1] == "Dune: pick a date."
    assert await _jobs(env) == []


async def test_the_worker_shows_only_the_asked_part_of_day_and_format(
    env: Env, notifier: FakeNotifier
) -> None:
    env = _with(env, FakeLLM(intents={DUNE: DUNE_JSON}))
    await approved_user(env.session_factory, 1)
    await _movie(env, "harkins", "phx", "dune", "Dune")
    await freetext.handle_text(env, _sender(1), DUNE, HELP)
    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    chain = FakeChain()
    day = date(2026, 11, 20)
    chain.showtimes = [
        showtime("s1", 13, "IMAX", day),  # right format, afternoon
        showtime("s2", 19, "IMAX", day),  # right format, evening
        showtime("s3", 20, "IMAX", day),  # right format, evening
        showtime("s4", 19, "Digital", day),  # evening, wrong format
    ]
    worker = make_worker_env(env.session_factory, notifier, chain)
    async with env.session_factory() as session:
        row = (await session.execute(text("select * from jobs limit 1"))).one()
    job = Job.model_validate(dict(row._mapping))

    result = await search_showtimes.run(worker, job)

    assert result == {"showtimes": 2}
    labels = [label for label, data in notifier.buttons_to(1) if data.startswith("book:")]
    assert labels == ["7:00 PM", "8:00 PM"]


async def test_when_nothing_fits_the_user_is_told(env: Env, notifier: FakeNotifier) -> None:
    env = _with(env, FakeLLM(intents={DUNE: DUNE_JSON}))
    await approved_user(env.session_factory, 1)
    await _movie(env, "harkins", "phx", "dune", "Dune")
    await freetext.handle_text(env, _sender(1), DUNE, HELP)
    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))
    chain = FakeChain()
    chain.showtimes = [showtime("s1", 13, "Digital", date(2026, 11, 20))]
    worker = make_worker_env(env.session_factory, notifier, chain)
    async with env.session_factory() as session:
        row = (await session.execute(text("select * from jobs limit 1"))).one()

    await search_showtimes.run(worker, Job.model_validate(dict(row._mapping)))

    assert notifier.to(1)[-1] == "Nothing that day matches what you asked for."


# --- Bangalore: a watch-only chain --------------------------------------------------------------


async def test_a_request_for_a_bookmyshow_movie_becomes_a_watch(
    env: Env, notifier: FakeNotifier
) -> None:
    reply = '{"title": "Ramayana", "date_from": "2026-11-20", "confidence": 0.9}'
    env = _with(env, FakeLLM(intents={"ramayana friday": reply}))
    user = await approved_user(env.session_factory, 1, city="blr")
    movie_id = await _movie(env, "bms", "blr", "ET1", "Ramayana", "coming_soon")

    await freetext.handle_text(env, _sender(1), "ramayana friday", HELP)
    assert "message you with a link" in notifier.to(1)[0]
    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    assert notifier.to(1)[-1].startswith("Watching Ramayana.")
    async with env.session_factory() as session:
        (item,) = await watches.list_for_user(session, user.id)
    assert (item.query, item.movie_id) == ("Ramayana", movie_id)
    assert await _jobs(env) == []  # no showtime search for a watch-only chain


async def test_a_kannada_title_resolves_through_the_model(env: Env, notifier: FakeNotifier) -> None:
    kannada = "ನಾಳೆ ಸಂಜೆ ರಾಮಾಯಣ ಟಿಕೆಟ್ ಬೇಕು"
    reply = '{"title": "ರಾಮಾಯಣ", "date_from": "2026-11-17", "confidence": 0.9}'
    llm = FakeLLM(intents={kannada: reply}, matches={"ರಾಮಾಯಣ": "[2]"})
    env = _with(env, llm)
    await approved_user(env.session_factory, 1, city="blr")
    await _movie(env, "bms", "blr", "ET9", "Scene", "coming_soon")
    await _movie(env, "bms", "blr", "ET1", "Ramayana", "coming_soon")

    await freetext.handle_text(env, _sender(1), kannada, HELP)

    assert notifier.to(1)[0].startswith("Ramayana.")


async def test_a_title_nothing_lists_becomes_a_watch_for_the_words(
    env: Env, notifier: FakeNotifier
) -> None:
    reply = '{"title": "Brand New Film", "confidence": 0.9}'
    env = _with(env, FakeLLM(intents={"brand new film please": reply}))
    user = await approved_user(env.session_factory, 1, city="blr")
    await _movie(env, "bms", "blr", "ET1", "Ramayana", "coming_soon")

    await freetext.handle_text(env, _sender(1), "brand new film please", HELP)
    assert "isn't listed yet" in notifier.to(1)[0]
    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    async with env.session_factory() as session:
        (item,) = await watches.list_for_user(session, user.id)
    assert (item.query, item.movie_id) == ("Brand New Film", None)


# --- guards -------------------------------------------------------------------------------------


async def test_without_an_llm_the_help_text_is_sent(env: Env, notifier: FakeNotifier) -> None:
    await approved_user(env.session_factory, 1)
    await freetext.handle_text(_with(env, None), _sender(1), "dune friday", HELP)
    assert notifier.to(1) == [HELP]


async def test_an_unsure_or_failed_parse_asks_for_the_watch_command(
    env: Env, notifier: FakeNotifier
) -> None:
    await approved_user(env.session_factory, 1)
    unsure = FakeLLM(intents={"hm": '{"title": "Dune", "confidence": 0.3}'})
    await freetext.handle_text(_with(env, unsure), _sender(1), "hm", HELP)
    await freetext.handle_text(_with(env, FakeLLM(fail=True)), _sender(1), "dune", HELP)
    assert notifier.to(1) == [freetext.NOT_CAUGHT, freetext.NOT_CAUGHT]
    assert await _pending_requests(env) == 0


async def test_a_city_is_needed_first(env: Env, notifier: FakeNotifier) -> None:
    await approved_user(env.session_factory, 1, city=None)
    await freetext.handle_text(_with(env, FakeLLM()), _sender(1), "dune", HELP)
    assert notifier.to(1) == ["Choose your city first with /city."]


async def test_a_request_cannot_be_replayed_taken_over_or_used_late(
    env: Env, notifier: FakeNotifier
) -> None:
    llm = FakeLLM(intents={DUNE: DUNE_JSON})
    env = _with(env, llm)
    await approved_user(env.session_factory, 1)
    await approved_user(env.session_factory, 2)
    await _movie(env, "harkins", "phx", "dune", "Dune")
    await freetext.handle_text(env, _sender(1), DUNE, HELP)
    request_id = _confirm_id(notifier, 1)

    await freetext.handle_intent(env, _sender(2), request_id)  # someone else's button
    assert notifier.to(2) == [freetext.EXPIRED]
    assert await _jobs(env) == []

    await freetext.handle_intent(env, _sender(1), request_id)
    await freetext.handle_intent(env, _sender(1), request_id)  # a second tap
    assert notifier.to(1)[-1] == freetext.EXPIRED
    assert len(await _jobs(env)) == 1

    await freetext.handle_text(env, _sender(1), DUNE, HELP)  # a fresh one, but an hour later
    late = replace(env, clock=lambda: CLOCK + timedelta(hours=1, minutes=1))
    await freetext.handle_intent(late, _sender(1), _confirm_id(notifier, 1))
    assert notifier.to(1)[-1] == freetext.EXPIRED


async def test_changing_city_before_confirming_stops_the_request(
    env: Env, notifier: FakeNotifier
) -> None:
    env = _with(env, FakeLLM(intents={DUNE: DUNE_JSON}))
    user = await approved_user(env.session_factory, 1)
    await _movie(env, "harkins", "phx", "dune", "Dune")
    await freetext.handle_text(env, _sender(1), DUNE, HELP)
    async with env.session_factory() as session, session.begin():
        blr = await catalog.get_city_by_code(session, "blr")
        assert blr
        await users.set_current_city(session, user.id, blr.id)

    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    assert notifier.to(1)[-1] == freetext.CITY_CHANGED
    assert await _jobs(env) == []


async def test_expired_requests_can_be_purged(env: Env) -> None:
    user = await approved_user(env.session_factory, 1)
    async with env.session_factory() as session, session.begin():
        await requests.create(session, user.id, {"x": 1}, CLOCK - timedelta(minutes=1))
        await requests.create(session, user.id, {"x": 2}, CLOCK + timedelta(hours=1))
        assert await requests.purge_expired(session, CLOCK) == 1


def test_the_parse_module_is_wired_to_the_same_floor() -> None:
    assert intent.CONFIDENCE_FLOOR == 0.6
    assert WorkerEnv  # imported for typing parity with the other flow tests


# --- review fixes -------------------------------------------------------------------------------


async def test_a_time_of_day_without_a_date_looks_at_the_next_days_with_the_filters(
    env: Env, notifier: FakeNotifier
) -> None:
    reply = '{"title": "Dune", "part_of_day": "evening", "format": "IMAX", "confidence": 0.9}'
    env = _with(env, FakeLLM(intents={"dune imax evening": reply}))
    await approved_user(env.session_factory, 1)
    await _movie(env, "harkins", "phx", "dune", "Dune")

    await freetext.handle_text(env, _sender(1), "dune imax evening", HELP)
    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    jobs = await _jobs(env)
    assert [j["day"] for j in jobs] == ["2026-11-16", "2026-11-17", "2026-11-18"]
    assert all(j["part_of_day"] == "evening" and j["format"] == "IMAX" for j in jobs)


async def test_a_watch_read_back_does_not_promise_a_day_or_time(
    env: Env, notifier: FakeNotifier
) -> None:
    reply = (
        '{"title": "Ramayana", "date_from": "2026-11-20", "date_to": "2026-11-20", '
        '"part_of_day": "evening", "confidence": 0.9}'
    )
    env = _with(env, FakeLLM(intents={"ramayana fri eve": reply}))
    await approved_user(env.session_factory, 1, city="blr")
    await _movie(env, "bms", "blr", "ET1", "Ramayana", "coming_soon")

    await freetext.handle_text(env, _sender(1), "ramayana fri eve", HELP)

    assert notifier.to(1)[0].startswith("Ramayana.\n")
    assert "evening" not in notifier.to(1)[0]


async def test_naming_another_city_stops_the_request(env: Env, notifier: FakeNotifier) -> None:
    reply = '{"title": "Dune", "city": "Bengaluru", "confidence": 0.9}'
    env = _with(env, FakeLLM(intents={"dune in bengaluru": reply}))
    await approved_user(env.session_factory, 1)  # Phoenix
    await _movie(env, "harkins", "phx", "dune", "Dune")

    await freetext.handle_text(env, _sender(1), "dune in bengaluru", HELP)

    assert "another city" in notifier.to(1)[0]
    assert "/city" in notifier.to(1)[0]
    assert await _pending_requests(env) == 0


async def test_naming_the_current_city_or_an_unknown_one_is_fine(
    env: Env, notifier: FakeNotifier
) -> None:
    same = '{"title": "Dune", "city": "Phoenix", "confidence": 0.9}'
    odd = '{"title": "Dune", "city": "Atlantis", "confidence": 0.9}'
    env = _with(env, FakeLLM(intents={"a": same, "b": odd}))
    await approved_user(env.session_factory, 1)
    await _movie(env, "harkins", "phx", "dune", "Dune")

    await freetext.handle_text(env, _sender(1), "a", HELP)
    await freetext.handle_text(env, _sender(1), "b", HELP)

    assert await _pending_requests(env) == 2


async def test_plain_language_messages_are_rate_limited_per_person(
    env: Env, notifier: FakeNotifier
) -> None:
    llm = FakeLLM(intents={"dune": '{"title": "Dune", "confidence": 0.9}'})
    env = _with(env, llm)
    await approved_user(env.session_factory, 1)
    await approved_user(env.session_factory, 2)
    await _movie(env, "harkins", "phx", "dune", "Dune")

    for _ in range(freetext.MAX_TEXTS_PER_MINUTE + 3):
        await freetext.handle_text(env, _sender(1), "dune", HELP)

    assert notifier.to(1).count(freetext.SLOW_DOWN) == 3
    assert len(llm.calls) <= freetext.MAX_TEXTS_PER_MINUTE * 2  # no model calls once limited
    await freetext.handle_text(env, _sender(2), "dune", HELP)  # someone else is unaffected
    assert freetext.SLOW_DOWN not in notifier.to(2)

    later = replace(env, clock=lambda: CLOCK + timedelta(minutes=2))
    await freetext.handle_text(later, _sender(1), "dune", HELP)  # a minute later it works again
    assert notifier.to(1)[-1] != freetext.SLOW_DOWN


async def test_old_requests_are_purged_as_new_ones_arrive(env: Env, notifier: FakeNotifier) -> None:
    env = _with(env, FakeLLM(intents={"dune": '{"title": "Dune", "confidence": 0.9}'}))
    user = await approved_user(env.session_factory, 1)
    await _movie(env, "harkins", "phx", "dune", "Dune")
    async with env.session_factory() as session, session.begin():
        await requests.create(session, user.id, {"x": 1}, CLOCK - timedelta(hours=2))

    await freetext.handle_text(env, _sender(1), "dune", HELP)

    assert await _pending_requests(env) == 1  # only the new one


# --- final review fixes -------------------------------------------------------------------------


async def test_a_coming_soon_harkins_title_becomes_a_watch_not_a_search(
    env: Env, notifier: FakeNotifier
) -> None:
    env = _with(env, FakeLLM(intents={DUNE: DUNE_JSON}))
    user = await approved_user(env.session_factory, 1)
    await _movie(env, "harkins", "phx", "dune", "Dune", "coming_soon")

    await freetext.handle_text(env, _sender(1), DUNE, HELP)
    assert "message you with a link" in notifier.to(1)[0]
    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    assert await _jobs(env) == []
    async with env.session_factory() as session:
        assert len(await watches.list_for_user(session, user.id)) == 1


async def test_a_date_beyond_the_week_is_said_out_loud(env: Env, notifier: FakeNotifier) -> None:
    far = (
        '{"title": "Dune", "date_from": "2026-12-25", "part_of_day": "evening", "confidence": 0.9}'
    )
    env = _with(env, FakeLLM(intents={"dune xmas": far}))
    await approved_user(env.session_factory, 1)
    await _movie(env, "harkins", "phx", "dune", "Dune")

    await freetext.handle_text(env, _sender(1), "dune xmas", HELP)
    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    assert notifier.to(1)[-1] == freetext.OUT_OF_RANGE
    assert await _jobs(env) == []


async def test_confirming_cannot_queue_more_than_the_open_lookup_cap(
    env: Env, notifier: FakeNotifier
) -> None:
    week = (
        '{"title": "Dune", "date_from": "2026-11-16", "date_to": "2026-11-22", "confidence": 0.9}'
    )
    env = _with(env, FakeLLM(intents={"dune all week": week}))
    await approved_user(env.session_factory, 1)
    await _movie(env, "harkins", "phx", "dune", "Dune")

    await freetext.handle_text(env, _sender(1), "dune all week", HELP)
    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))
    assert len(await _jobs(env)) == 7

    await freetext.handle_text(env, _sender(1), "dune all week", HELP)  # a different request id
    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))
    assert len(await _jobs(env)) == 7  # nothing more while those are open
    assert notifier.to(1)[-1] == freetext.STILL_LOOKING


async def test_a_person_cannot_watch_more_than_the_listed_maximum(
    env: Env, notifier: FakeNotifier
) -> None:
    from showwatch.bot.handlers import watch

    user = await approved_user(env.session_factory, 1)
    for n in range(watch.MAX_LISTED):
        await add_watch(env.session_factory, user, f"film {n}")

    await watch.handle_watch(env, _sender(1), "one more film nobody lists")

    assert notifier.to(1)[-1] == watch.TOO_MANY
    async with env.session_factory() as session:
        assert len(await watches.list_for_user(session, user.id)) == watch.MAX_LISTED


async def test_a_failed_showtime_lookup_tells_the_person_who_asked(
    env: Env, notifier: FakeNotifier
) -> None:
    from showwatch.db import queue

    user = await approved_user(env.session_factory, 1)
    movie_id = await _movie(env, "harkins", "phx", "dune", "Dune")
    chain = FakeChain()
    chain.error = RuntimeError("harkins.com returned HTTP 500")
    worker = make_worker_env(env.session_factory, notifier, chain)
    async with env.session_factory() as session, session.begin():
        found = await movies.get(session, movie_id)
        assert found
        job = await queue.enqueue(
            session,
            "search_showtimes",
            {"user_id": user.id, "movie_id": movie_id, "day": "2026-11-20"},
            chain_id=found.chain_id,
        )

    result = await search_showtimes.run(worker, job)

    assert result == {"failed": "RuntimeError"}
    assert notifier.to(1) == [search_showtimes.LOOKUP_FAILED]


async def test_the_sweep_survives_a_job_kind_that_no_longer_exists(env: Env) -> None:
    from showwatch.db import queue

    async with env.session_factory() as session, session.begin():
        await session.execute(
            text(
                "insert into jobs (kind, payload, status, max_attempts, locked_by, locked_at) "
                "values ('retired_kind', '{}', 'running', 3, 'w', now() - interval '2 hours')"
            )
        )
    async with env.session_factory() as session, session.begin():
        swept = await queue.sweep(session, 900)

    assert [(j.kind, j.status) for j in swept] == [("retired_kind", "failed")]


# --- format preferences ---------------------------------------------------------------------------

AVENGERS = (
    '{"title": "Avengers", "formats_wanted": ["2D", "MS - Infinity Vision"], '
    '"formats_excluded": ["3D"], "confidence": 0.9}'
)


async def test_a_watch_keeps_the_formats_the_person_asked_for(
    env: Env, notifier: FakeNotifier
) -> None:
    env = _with(env, FakeLLM(intents={"avengers 2d no 3d": AVENGERS}))
    user = await approved_user(env.session_factory, 1, city="blr")
    await _movie(env, "bms", "blr", "ET1", "Avengers: Doomsday", "coming_soon")

    await freetext.handle_text(env, _sender(1), "avengers 2d no 3d", HELP)
    assert notifier.to(1)[0].startswith("Avengers: Doomsday (2D or MS - Infinity Vision; no 3D).\n")
    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    assert notifier.to(1)[-1].startswith(
        "Watching Avengers: Doomsday in 2D or MS - Infinity Vision; no 3D."
    )
    async with env.session_factory() as session:
        (item,) = await watches.list_for_user(session, user.id)
    assert FormatPref.from_json(item.format_pref) == FormatPref.of(
        ["2D", "MS - Infinity Vision"], ["3D"]
    )


async def test_asking_again_updates_the_formats_of_an_existing_watch(
    env: Env, notifier: FakeNotifier
) -> None:
    only_2d = '{"title": "Avengers", "formats_wanted": ["2D"], "confidence": 0.9}'
    env = _with(env, FakeLLM(intents={"first": AVENGERS, "second": only_2d}))
    user = await approved_user(env.session_factory, 1, city="blr")
    await _movie(env, "bms", "blr", "ET1", "Avengers: Doomsday", "coming_soon")

    for message in ("first", "second"):
        await freetext.handle_text(env, _sender(1), message, HELP)
        await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    assert notifier.to(1)[-1].startswith("Updated your watch for Avengers: Doomsday in 2D.")
    async with env.session_factory() as session:
        (item,) = await watches.list_for_user(session, user.id)
    assert FormatPref.from_json(item.format_pref) == FormatPref.of(["2D"])


async def test_a_title_nothing_lists_yet_keeps_the_formats_too(
    env: Env, notifier: FakeNotifier
) -> None:
    env = _with(
        env, FakeLLM(intents={"new film 2d": AVENGERS.replace("Avengers", "Brand New Film")})
    )
    user = await approved_user(env.session_factory, 1, city="blr")

    await freetext.handle_text(env, _sender(1), "new film 2d", HELP)
    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    async with env.session_factory() as session:
        (item,) = await watches.list_for_user(session, user.id)
    assert (item.query, FormatPref.from_json(item.format_pref).include) == (
        "Brand New Film",
        ("2D", "MS - Infinity Vision"),
    )


async def test_asking_again_restarts_a_watch_that_had_already_finished(
    env: Env, notifier: FakeNotifier
) -> None:
    env = _with(env, FakeLLM(intents={"again": AVENGERS}))
    user = await approved_user(env.session_factory, 1, city="blr")
    movie_id = await _movie(env, "bms", "blr", "ET1", "Avengers: Doomsday", "coming_soon")
    async with env.session_factory() as session, session.begin():
        old, _ = await watches.add(
            session,
            user_id=user.id,
            city_id=movie_id and (await _blr_id(session)),
            query="Avengers: Doomsday",
            movie_id=movie_id,
        )
        await signals.record(session, old.id, "sessions", {"url": "u"}, source="district")
        await watches.set_stage(session, old.id, "done")

    await freetext.handle_text(env, _sender(1), "again", HELP)
    await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    async with env.session_factory() as session:
        (item,) = await watches.list_for_user(session, user.id)
        assert item.id != old.id  # a fresh watch, with no memory of the earlier opening
        assert item.stage == "waiting"
        assert await signals.sessions_sources(session, [item.id]) == {item.id: set()}
    assert notifier.to(1)[-1].startswith("Updated your watch for Avengers: Doomsday")


async def _blr_id(session) -> int:  # type: ignore[no-untyped-def]
    city = await catalog.get_city_by_code(session, "blr")
    assert city
    return city.id


async def test_asking_again_for_a_title_not_listed_yet_replaces_its_watch(
    env: Env, notifier: FakeNotifier
) -> None:
    only_2d = '{"title": "Brand New Film", "formats_wanted": ["2D"], "confidence": 0.9}'
    no_3d = '{"title": "Brand New Film", "formats_excluded": ["3D"], "confidence": 0.9}'
    env = _with(env, FakeLLM(intents={"one": only_2d, "two": no_3d}))
    user = await approved_user(env.session_factory, 1, city="blr")

    for message in ("one", "two"):
        await freetext.handle_text(env, _sender(1), message, HELP)
        await freetext.handle_intent(env, _sender(1), _confirm_id(notifier, 1))

    async with env.session_factory() as session:
        (item,) = await watches.list_for_user(session, user.id)
    assert FormatPref.from_json(item.format_pref) == FormatPref.of([], ["3D"])
