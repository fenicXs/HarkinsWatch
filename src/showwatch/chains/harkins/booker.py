"""Hold two seats on a Harkins session and return the page where the user pays.

Async port of `legacy/booker.py`. Order of attempts: pick central adjacent seats from the page's
seat buttons (fast); if that fails, reload and ask a vision model where the free seats are on the
canvas; if that fails too, hand back the seat-map link for the user to choose themselves. It
never pays: it stops at the checkout page. Harkins needs no login for this (docs/decisions/022).
"""

import re
from collections.abc import Awaitable, Callable
from pathlib import Path

import structlog
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import FloatRect, Page, async_playwright

from showwatch.chains.base import RetryLater
from showwatch.chains.harkins import seatmap
from showwatch.domain.models import BookingResult
from showwatch.llm.tasks.seat_vision import Point

NUM_SEATS = 2

SEAT_BUTTON = 'button[aria-label^="Select Seat"]'
FREE_SEAT_BUTTON = f"{SEAT_BUTTON}:not([disabled])"
REMOVE_BUTTON = 'button[aria-label^="Remove Seat"]'
CANVAS = 'canvas[aria-label="Theatre seat map"]'
TICKET_ADD = '[data-testid^="ticketing-type-picker-add-"]:not([disabled])'
CHECKOUT_BUTTON = 'button:has-text("Continue to Checkout")'

SEAT_MAP_TIMEOUT_MS = 60_000
HYDRATION_WAIT_MS = 3_000
CHECKOUT_URL_TIMEOUT_MS = 30_000
CHECKOUT_POLLS = 15
CLICK_RETRIES = 3
PAIR_ATTEMPTS = 8
MAX_FAILED_SELECTIONS = 3  # legacy gave up after 2; seats another visitor holds also fail to click
CLEAR_ROUNDS = 8
VISION_ROUNDS = 2

# What Chromium says when another process still holds the profile directory.
PROFILE_IN_USE = re.compile(
    r"ProcessSingleton|already in use|existing browser session", re.IGNORECASE
)

log = structlog.get_logger(__name__)

# Given a screenshot of the seat canvas, return free-seat points as (y, x) on a 0-1000 scale.
VisionPicker = Callable[[bytes], Awaitable[list[Point]]]


async def _open_seat_map(page: Page, url: str) -> bool:
    """Load the session page and wait until the seat map is usable."""
    await page.goto(url)
    try:
        await page.wait_for_selector(SEAT_BUTTON, timeout=SEAT_MAP_TIMEOUT_MS)
    except PlaywrightError:
        return False
    await page.wait_for_timeout(HYDRATION_WAIT_MS)  # React hydration: the map is not clickable yet
    return True


async def _finalize(page: Page, seats: int) -> bool:
    """Set the ticket count to `seats`; True once "Continue to Checkout" is enabled."""
    checkout = page.locator(CHECKOUT_BUTTON)
    add = page.locator(TICKET_ADD).first
    if await add.count() and not await checkout.is_enabled():
        for _ in range(seats):
            await add.click()
            await page.wait_for_timeout(200)
    for _ in range(CHECKOUT_POLLS):
        if await checkout.is_enabled():
            return True
        await page.wait_for_timeout(200)
    return await checkout.is_enabled()


async def _select_seat(page: Page, seat_id: str) -> bool:
    """Click a seat by its id (forced: the canvas overlay blocks plain clicks); confirm it took."""
    button = page.locator(f"button#{seat_id}")
    for _ in range(CLICK_RETRIES):
        try:
            await button.click(force=True, timeout=3000)
        except PlaywrightError:
            try:
                await button.dispatch_event("click")
            except PlaywrightError:
                return False
        await page.wait_for_timeout(300)
        try:
            if await button.get_attribute("aria-pressed") == "true":
                return True
        except PlaywrightError:
            return False
    return False


async def _clear_selection(page: Page) -> None:
    """Deselect every selected seat."""
    for _ in range(CLEAR_ROUNDS):
        pressed = page.locator('button[aria-pressed="true"]')
        if await pressed.count() == 0:
            return
        await pressed.first.dispatch_event("click")
        await page.wait_for_timeout(200)


async def _select_deterministic(page: Page, seats: int) -> list[str] | None:
    """Choose central adjacent seats from the page's seat buttons; ids with tickets set, or None."""
    raw = await page.eval_on_selector_all(
        FREE_SEAT_BUTTON,
        "els => els.map(e => ({id: e.id, x: parseFloat(e.getAttribute('x')), "
        "y: parseFloat(e.getAttribute('y'))}))",
    )
    parsed = (seatmap.parse_seat(item["id"], item["x"], item["y"]) for item in raw)
    available = [seat for seat in parsed if seat is not None]

    failed_selections = 0
    for pair in seatmap.candidate_pairs(available)[:PAIR_ATTEMPTS]:
        await _clear_selection(page)
        picked = [await _select_seat(page, seat["id"]) for seat in pair]
        if not all(picked):
            failed_selections += 1
            if failed_selections >= MAX_FAILED_SELECTIONS:  # clicks are not landing on this map
                await _clear_selection(page)
                return None
            continue
        if await _finalize(page, seats):
            return [seat["id"] for seat in pair]
    return None


async def _selected_ids(page: Page) -> set[str]:
    ids = await page.eval_on_selector_all(REMOVE_BUTTON, "els => els.map(e => e.id)")
    return {i for i in ids if i}


async def _confirm_points(
    page: Page, box: FloatRect, points: list[Point]
) -> dict[str, seatmap.ConfirmedSeat]:
    """Click each point; keep those that selected exactly one seat (then deselect it again)."""
    confirmed: dict[str, seatmap.ConfirmedSeat] = {}
    before = await _selected_ids(page)
    for y, x in points:
        screen_x = box["x"] + (x / 1000) * box["width"]
        screen_y = box["y"] + (y / 1000) * box["height"]
        await page.mouse.click(screen_x, screen_y)
        await page.wait_for_timeout(250)
        new = (await _selected_ids(page)) - before
        if len(new) == 1:
            seat_id = next(iter(new))
            match = seatmap.SEAT_ID.match(seat_id)
            if match:
                confirmed[seat_id] = seatmap.ConfirmedSeat(
                    match.group(1), int(match.group(2)), screen_x, screen_y
                )
            await page.mouse.click(screen_x, screen_y)  # deselect, so the next point starts clean
            await page.wait_for_timeout(100)
        before = await _selected_ids(page)
    return confirmed


async def _select_by_vision(page: Page, vision: VisionPicker, seats: int) -> list[str] | None:
    """Ask the vision model for free-seat points, click each to learn which seat it is, then
    select the best adjacent run. Returns the seat ids left selected, or None."""
    canvas = page.locator(CANVAS)
    if not await canvas.count():
        return None
    box = await canvas.bounding_box()
    if box is None:
        return None
    run = None
    for _ in range(VISION_ROUNDS):  # the model's aim varies from ask to ask: one more try is cheap
        points = await vision(await canvas.screenshot())
        confirmed = await _confirm_points(page, box, points)
        run = seatmap.best_run(confirmed, seats)
        if run is not None:
            break
    if run is None:
        return None
    for _, screen_x, screen_y in run:
        await page.mouse.click(screen_x, screen_y)
        await page.wait_for_timeout(250)
    ids = [seat_id for seat_id, _, _ in run]
    selected = await _selected_ids(page)
    return ids if all(i in selected for i in ids) else None


async def book_session(
    session_url: str,
    *,
    profile_dir: Path,
    vision: VisionPicker | None,
    seats: int = NUM_SEATS,
    skip_deterministic: bool = False,
) -> BookingResult:
    """Hold `seats` seats. `held` carries the seat ids and the checkout page; `manual` carries the
    seat-map link. Raises `RetryLater` if the browser profile is still locked by another process;
    any other error propagates (the handler turns it into "booking failed")."""
    manual = BookingResult(kind="manual", seats=[], url=session_url)
    async with async_playwright() as playwright:
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(profile_dir), headless=False
            )
        except PlaywrightError as error:
            if PROFILE_IN_USE.search(str(error)):
                raise RetryLater("browser profile is in use", delay_s=60) from error
            raise
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            if not await _open_seat_map(page, session_url):
                log.warning("seat_map_not_loaded")
                return manual

            by_vision: list[str] | None = None
            selected = None if skip_deterministic else await _select_deterministic(page, seats)
            if not selected and vision is not None:
                if not skip_deterministic:
                    # Failed clicks leave the canvas in a bad state: reload for a clean slate.
                    if not await _open_seat_map(page, session_url):
                        return manual
                by_vision = await _select_by_vision(page, vision, seats)
                if by_vision and await _finalize(page, seats):
                    selected = by_vision
            if not selected:
                log.warning("no_seats_picked", vision=vision is not None)
                await _clear_selection(page)
                return manual
            log.info("seats_picked", seats=selected, by="vision" if by_vision else "buttons")

            await page.locator(CHECKOUT_BUTTON).click()
            try:
                await page.wait_for_url("**/session/*/ticketing/*", timeout=CHECKOUT_URL_TIMEOUT_MS)
            except PlaywrightError:  # the seats were probably taken as we continued
                log.warning("checkout_not_reached", seats=selected)
                return manual
            return BookingResult(kind="held", seats=selected, url=page.url)
        finally:
            await context.close()
