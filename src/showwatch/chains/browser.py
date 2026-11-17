"""One polite page fetch through a real browser, for sites that refuse plain HTTP clients.

BookMyShow and District serve their pages to a normal browser window without login. Each fetch
spends the chain's request budget first. A 403, 429 or challenge page is recorded as a block (the
chain backs off, see `politeness.py`) and raises `BlockedError`. Challenges are never solved and
nothing here hides that this is an automated browser.
"""

import re
from types import TracebackType
from typing import Self

import structlog
from playwright.async_api import Browser, Playwright, async_playwright

from showwatch.chains.base import BlockedError
from showwatch.chains.politeness import Politeness

CHALLENGE_TITLE = re.compile(
    r"access denied|just a moment|attention required|pardon our interruption|request unsuccessful",
    re.IGNORECASE,
)
BLOCKING_STATUSES = frozenset({403, 429})
READY_TIMEOUT_MS = 15_000
SCROLL_PAUSE_MS = 1_000

log = structlog.get_logger(__name__)


def is_blocked(status: int | None, title: str) -> bool:
    return status in BLOCKING_STATUSES or bool(CHALLENGE_TITLE.search(title))


class PageFetcher:
    """Open once, fetch a few pages, close. Use as `async with PageFetcher(...) as fetcher`."""

    def __init__(
        self,
        chain_code: str,
        politeness: Politeness,
        *,
        channel: str | None = None,
        headless: bool = False,
    ) -> None:
        self._chain = chain_code
        self._politeness = politeness
        self._channel = channel or None
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> Self:
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless, channel=self._channel
            )
        except BaseException:
            await self._playwright.stop()  # `__aexit__` never runs when entering fails
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
        finally:
            if self._playwright is not None:
                await self._playwright.stop()

    async def get(self, url: str, *, ready_js: str | None = None, scroll_passes: int = 0) -> str:
        """The rendered HTML of `url`. `ready_js` is a JavaScript expression that turns truthy
        once the page has drawn what we read; if it never does we read the page as it is.
        `scroll_passes` scrolls to the bottom that many times, for lists that load as you scroll."""
        assert self._browser is not None, "use PageFetcher as an async context manager"
        await self._politeness.before_request(self._chain)
        page = await self._browser.new_page()
        try:
            response = await page.goto(url, wait_until="domcontentloaded")
            status = response.status if response else None
            if ready_js and not is_blocked(status, await page.title()):
                try:
                    await page.wait_for_function(ready_js, timeout=READY_TIMEOUT_MS)
                except Exception:
                    log.info("page_not_ready", chain=self._chain, url=url)
            for _ in range(scroll_passes):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(SCROLL_PAUSE_MS)
            title = await page.title()
            if is_blocked(status, title):
                until = await self._politeness.record_blocked(self._chain)
                raise BlockedError(
                    f"{self._chain} stopped us (status {status}); backing off until {until}"
                )
            if status is not None and status >= 400:
                raise RuntimeError(f"{self._chain} returned HTTP {status} for {url}")
            html = await page.content()
        finally:
            await page.close()
        await self._politeness.record_ok(self._chain)
        return html
