"""Booking-flow explorer: you navigate manually, it saves page snapshots."""
from playwright.sync_api import sync_playwright

PROFILE_DIR = "browser_profile"   # persistent profile — keeps you logged in


with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(PROFILE_DIR, headless=False)
    page = browser.pages[0]
    page.goto("https://www.harkins.com/movies")

    n = 0
    while True:
        cmd = input("Enter = save this page snapshot | q = quit: ").strip().lower()
        if cmd == "q":
            break
        n += 1
        print("URL:", page.url)
        fname = f"dump_{n}.html"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"Saved {fname}\n")

    browser.close()
