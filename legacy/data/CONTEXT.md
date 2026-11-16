# data/ — runtime state

Everything the bot remembers between runs. Paths are defined only in `../paths.py`.

| File | Written by | Read by | In git? |
|---|---|---|---|
| `watchlist.json` | `add_watch.save_watchlist` (via `add_title` / CLI `main`), `tracker.remove_from_watchlist` (called by `bot.run_watch_check`) | `tracker.find_watchlisted_now_showing`, `add_watch` (duplicate check) | yes |
| `coming_soon.json` | `tracker.refresh_coming_soon` (every watch check) | `add_watch.find_matches`, CLI `main` | yes |
| `seen_movies.json` | `tracker.find_new_movies` (every watch check) | `tracker`, `add_watch.find_matches` / `add_title` / CLI `main` | no (gitignored) |
| `browser_profile/` | Chromium via `booker.py`, `tools/explore_booking.py` | same | no (gitignored; holds the Harkins login) |

Human check: `watchlist.json` is a plain sorted JSON list of exact Harkins titles — safe to hand-edit while the bot is stopped.
