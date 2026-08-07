"""Throwaway script: find the movie-link pattern on the Harkins page."""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("https://www.harkins.com/movies", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)   # wait 5s for the movie list to load

    links = page.locator("a").all()
    print("TOTAL links on page:", len(links))
    print("--- links whose href mentions 'movie' ---")

    seen = set()                                  # avoid printing duplicates
    for link in links:
        href = link.get_attribute("href") or ""
        if "movie" in href.lower() and href not in seen:
            seen.add(href)
            text = (link.inner_text() or "").strip().replace("\n", " ")
            print(repr(href), "::", text[:50])

    browser.close()
