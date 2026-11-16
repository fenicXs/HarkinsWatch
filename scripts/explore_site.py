"""Probe a public page with a headless browser, and optionally save it as a test fixture.

    uv run python scripts/explore_site.py URL [--save-html PATH] [--wait-selector CSS] [--links CSS] [--strip]

Prints the HTTP status, final URL, page title and (with --links) how many elements match a
selector. `--save-html` writes the rendered page, which is how tests/fixtures/<chain>/ pages
are captured. Load pages sparingly: one request per run, no loops (see the plan's request
budget). Never point this at pages that need a login.
"""

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

STRIP_JS = """() => { document.querySelectorAll('script, style, noscript, svg, link[rel=stylesheet], iframe').forEach(e => e.remove()); }"""


async def probe(
    url: str, save_to: Path | None, wait_selector: str | None, links: str | None, strip: bool
) -> int:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            response = await page.goto(url, wait_until="domcontentloaded")
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=15_000)
                except Exception:
                    print(f"selector never appeared: {wait_selector}")
            else:
                await page.wait_for_timeout(5_000)
            status = response.status if response else None
            print(f"status: {status}")
            print(f"final url: {page.url}")
            print(f"title: {await page.title()}")
            if links:
                print(f"{links!r} matches: {await page.locator(links).count()}")
            if save_to is not None:
                save_to.parent.mkdir(parents=True, exist_ok=True)
                if strip:  # keep the structure and classes the parsers read, drop the weight
                    await page.evaluate(STRIP_JS)
                html = await page.content()
                save_to.write_text(html, encoding="utf-8")
                print(f"saved {len(html):,} characters to {save_to}")
            return 0 if status is not None and status < 400 else 1
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("url")
    parser.add_argument("--save-html", type=Path, help="write the rendered page here")
    parser.add_argument(
        "--strip", action="store_true", help="remove scripts, styles and svg before saving"
    )
    parser.add_argument("--wait-selector", help="wait for this CSS selector instead of 5 seconds")
    parser.add_argument("--links", help="count the elements matching this CSS selector")
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(probe(args.url, args.save_html, args.wait_selector, args.links, args.strip))
    )


if __name__ == "__main__":
    main()
