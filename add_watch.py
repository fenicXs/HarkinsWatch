"""Add a movie to the watchlist after confirming its Harkins title (reads cached files)."""
import sys
import json
import os
from difflib import get_close_matches

WATCHLIST_FILE = "watchlist.json"
SEEN_FILE = "seen_movies.json"
COMING_SOON_FILE = "coming_soon.json"


def load_json_list(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_watchlist(titles):
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(titles), f, indent=2, ensure_ascii=False)


def main():
    if len(sys.argv) < 2:
        print('Usage: python add_watch.py "movie name"')
        return
    query = " ".join(sys.argv[1:])

    now_showing = load_json_list(SEEN_FILE)
    coming_soon = load_json_list(COMING_SOON_FILE)
    all_titles = now_showing + coming_soon
    if not all_titles:
        print("Caches are empty. Run 'python bot.py' first to build them.")
        return

    title_by_lower = {t.lower(): t for t in all_titles}
    q = query.lower()
    matches = [low for low in title_by_lower if q in low]        # substring hits only
    if not matches:                                              # fallback for typos
        matches = get_close_matches(q, list(title_by_lower.keys()), n=5, cutoff=0.3)
    matches = matches[:5]

    if not matches:
        print(f"No Harkins movie found matching '{query}'.")
        return

    print("Closest matches:")
    for i, low in enumerate(matches, 1):
        print(f"  {i}. {title_by_lower[low]}")
    choice = input("Pick a number to add (Enter to cancel): ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(matches)):
        print("Cancelled.")
        return
    best = title_by_lower[matches[int(choice) - 1]]

    watchlist = load_json_list(WATCHLIST_FILE)
    if best in watchlist:
        print(f"'{best}' is already on your watchlist.")
        return
    watchlist.append(best)
    save_watchlist(watchlist)

    # Report status based on which cache the title is in
    if best in now_showing:
        print(f"Added '{best}'. It's NOW SHOWING — it will be booked on the next run.")
    else:
        print(f"Added '{best}'. It's COMING SOON — it will be booked once it hits Now Showing.")

def find_matches(query):
    all_titles = load_json_list(SEEN_FILE) + load_json_list(COMING_SOON_FILE)
    title_by_lower = {t.lower(): t for t in all_titles}
    q = query.lower()
    matches = [low for low in title_by_lower if q in low]
    if not matches:
        matches = get_close_matches(q, list(title_by_lower.keys()), n=5, cutoff=0.3)
    return [title_by_lower[m] for m in matches[:5]]


def add_title(title):
    watchlist = load_json_list(WATCHLIST_FILE)
    if title in watchlist:
        return f"'{title}' is already on your watchlist."
    watchlist.append(title)
    save_watchlist(watchlist)
    if title in load_json_list(SEEN_FILE):
        return f"Added '{title}' — NOW SHOWING, will book on next run."
    return f"Added '{title}' — COMING SOON, will book on release."


if __name__ == "__main__":
    main()
