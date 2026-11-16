"""M3 parity check: does the new check_chain give the same answers as the legacy tracker?

    uv run python scripts/parity_check.py

Both sides are fed the same saved Now Showing page (tests/fixtures/harkins/now_showing.html),
with the same seed: all titles already seen except 2, and a watchlist holding 1 of those 2.
The legacy side runs on a temporary COPY of legacy/, so legacy/data is never touched. The new
side runs on a throwaway database (needs TEST_DATABASE_URL in the environment or in the local
settings file).

Compared: which movies are new, and which watches fire. (The new side sends one message to a
user who is both a watcher and a subscriber, where legacy sent two: that is a deliberate
improvement and is not part of the comparison.)
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))  # for the test helpers

from showwatch.chains.harkins.scraper import (  # noqa: E402
    extract_movie_links,
    listings_from_links,
)


async def parse_fixture() -> list[tuple[str, str]]:
    """(title, href) pairs from the saved page, parsed by the same code that parses the live site."""
    html = (ROOT / "tests/fixtures/harkins/now_showing.html").read_text(encoding="utf-8")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html)
            links = await extract_movie_links(page)
        finally:
            await browser.close()
    return [
        (m.title, "/movies/" + m.external_id) for m in listings_from_links(links, "now_showing")
    ]


def run_legacy(
    current: list[dict[str, str]], seen: list[str], watchlist: list[str]
) -> tuple[set[str], set[str]]:
    """The legacy tracker, on a temporary copy of legacy/ (so legacy/data is untouched)."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "legacy"
        shutil.copytree(
            ROOT / "legacy", copy, ignore=shutil.ignore_patterns(".venv", "__pycache__", "data")
        )
        (copy / "data").mkdir()
        (copy / "data" / "seen_movies.json").write_text(json.dumps(seen), encoding="utf-8")
        (copy / "data" / "watchlist.json").write_text(json.dumps(watchlist), encoding="utf-8")
        sys.path.insert(0, str(copy))
        try:
            import tracker  # the legacy module, from the copy

            new = {m["title"] for m in tracker.find_new_movies(current)}
            watched = {m["title"] for m in tracker.find_watchlisted_now_showing(current)}
        finally:
            sys.path.remove(str(copy))
            for name in ("tracker", "scraper", "paths"):
                sys.modules.pop(name, None)
        return new, watched


async def run_new(
    pairs: list[tuple[str, str]], seen: list[str], watched_title: str
) -> tuple[set[str], set[str]]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from dbtools import migrate, recreate_database, with_database_name
    from showwatch.core.settings import Settings
    from showwatch.db.repositories import catalog
    from showwatch.worker.handlers import check_chain
    from telegramfakes import FakeNotifier
    from workerfakes import FakeChain, add_watch, approved_user, make_worker_env

    base = os.environ.get("TEST_DATABASE_URL") or Settings().test_database_url  # type: ignore[call-arg]
    if not base:
        raise SystemExit("TEST_DATABASE_URL is not set")
    url = with_database_name(base, "showwatch_parity")
    await recreate_database(url)
    await migrate(url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        notifier = FakeNotifier()
        env = make_worker_env(factory, notifier, FakeChain())
        user = await approved_user(factory, 1)
        await add_watch(factory, user, watched_title)
        async with factory() as session:
            chain = await catalog.get_chain_by_code(session, "harkins")
            city = await catalog.get_city_by_code(session, "phx")
        assert chain and city

        baseline = listings_from_links([p for p in pairs if p[0] in seen], "now_showing")
        await check_chain.process_scrape(env, chain, city, baseline)  # the seeded "seen" state
        notifier.sent.clear()
        result = await check_chain.process_scrape(
            env, chain, city, listings_from_links(pairs, "now_showing")
        )
        fired = {title for title, stage in result.watch_events or [] if stage == "sessions"}
        return set(result.new_releases or []), fired
    finally:
        await engine.dispose()


async def main() -> int:
    pairs = await parse_fixture()
    current = [{"title": title, "url": "https://www.harkins.com" + href} for title, href in pairs]
    titles = sorted({title for title, _ in pairs})
    unseen, watched = titles[:2], titles[0]
    seen = [t for t in titles if t not in unseen]
    print(
        f"fixture: {len(titles)} movies; seen: {len(seen)}; unseen: {unseen}; watched: {watched!r}"
    )

    legacy_new, legacy_watched = run_legacy(current, seen, [watched])
    new_new, new_watched = await run_new(pairs, seen, watched)

    print(f"legacy -> new releases {sorted(legacy_new)}; watch fires {sorted(legacy_watched)}")
    print(f"new    -> new releases {sorted(new_new)}; watch fires {sorted(new_watched)}")
    ok = (
        legacy_new == new_new
        and legacy_watched == new_watched
        and len(legacy_new) == 2
        and len(legacy_watched) == 1
    )
    print("PARITY OK" if ok else "PARITY MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
