"""Compares current Harkins movies against ones we've already seen."""
import json
import os
from scraper import get_now_playing, get_coming_soon

SEEN_FILE = "seen_movies.json"
COMING_SOON_FILE = "coming_soon.json"
WATCHLIST_FILE = "watchlist.json"


def load_watchlist():
    if not os.path.exists(WATCHLIST_FILE):
        return []
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def refresh_coming_soon():
    """Scrape Coming Soon and save its titles to the cache file."""
    titles = sorted({m["title"] for m in get_coming_soon()})
    with open(COMING_SOON_FILE, "w", encoding="utf-8") as f:
        json.dump(titles, f, indent=2, ensure_ascii=False)
    return titles


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(json.load(f))


def save_seen(titles):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(titles), f, indent=2, ensure_ascii=False)


def find_new_movies(current=None):
    if current is None:
        current = get_now_playing()
    current_titles = {m["title"] for m in current}
    seen = load_seen()
    first_run = len(seen) == 0
    new_titles = current_titles - seen
    save_seen(current_titles)
    if first_run:
        print(f"First run: saved {len(current_titles)} movies as baseline. No alerts sent.")
        return []
    new_movies = [m for m in current if m["title"] in new_titles]
    return new_movies

def find_watchlisted_now_showing(current):
    """Return movies from the current Now Showing list that are on the watchlist."""
    watchlist = set(load_watchlist())
    return [m for m in current if m["title"] in watchlist]

def remove_from_watchlist(title):
    watchlist = [t for t in load_watchlist() if t != title]
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(watchlist), f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    new = find_new_movies()
    if new:
        print(f"{len(new)} NEW movie(s):")
        for m in new:
            print(" -", m["title"], "->", m["url"])
    else:
        print("No new movies since last check.")
