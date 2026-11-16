"""Quick check that Playwright can launch a browser and load a page."""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)   # headless = no visible window
    page = browser.new_page()
    page.goto("https://example.com")
    print("Page title is:", page.title())
    browser.close()
