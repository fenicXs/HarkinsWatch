"""Manual live check of the Harkins booker. Holds 2 seats, prints the outcome, never pays.

    uv run python scripts/smoke_book.py --showtime-url https://www.harkins.com/ticketing/theatre/16/movie/<id>/session/<id>/date/<day>

Needs the LLM_VISION_* settings in the local env files only if the vision step is reached.
`--force-vision` skips the fast seat-button picker, to test vision on its own. `--save-map PATH`
saves a screenshot of the seat canvas (for calibration) and exits without booking.
Never run in CI: it drives a visible browser against the live site.
"""

import argparse
import asyncio
import tempfile
from functools import partial
from pathlib import Path

from playwright.async_api import async_playwright

from showwatch.chains.harkins import booker
from showwatch.core.settings import get_settings
from showwatch.llm.client import LLMClient, vision_endpoints
from showwatch.llm.tasks.seat_vision import find_points


async def save_map(url: str, path: Path) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        try:
            page = await browser.new_page()
            if not await booker._open_seat_map(page, url):
                raise SystemExit("the seat map did not load")
            await page.locator(booker.CANVAS).screenshot(path=str(path))
            print(f"saved {path}")
        finally:
            await browser.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--showtime-url", required=True)
    parser.add_argument("--profile-dir", type=Path, default=None)
    parser.add_argument("--no-vision", action="store_true")
    parser.add_argument("--force-vision", action="store_true")
    parser.add_argument("--save-map", type=Path, default=None)
    args = parser.parse_args()

    if args.save_map:
        await save_map(args.showtime_url, args.save_map)
        return

    client = LLMClient(vision_endpoints(get_settings()))
    vision = None if args.no_vision or not client.configured else partial(find_points, client)
    profile = args.profile_dir or Path(tempfile.mkdtemp(prefix="showwatch-smoke-"))
    print(f"vision configured: {vision is not None}; profile: {profile}")
    result = await booker.book_session(
        args.showtime_url,
        profile_dir=profile,
        vision=vision,
        skip_deterministic=args.force_vision,
    )
    print({"kind": result.kind, "seats": result.seats, "url": result.url})


if __name__ == "__main__":
    asyncio.run(main())
