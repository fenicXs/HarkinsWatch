# Module contracts and change impact

Source code is the truth; this file points at it. If this file and the code disagree, the code wins — fix this file.

## Data flow

```
Telegram ⇄ bot.py ─┬─ add_watch.find_matches ← data/seen_movies.json + data/coming_soon.json
                   ├─ tracker (every 30 min) ─ scraper.get_now_playing / get_coming_soon → data/*.json
                   ├─ scraper.get_showtimes (movie + date) → [{time, url, format}]
                   └─ booker.book_session(url) ─ seat_ai (fallback) → {seats, url} | {manual, url}
```

## Modules

| Module | Reads | Does | Writes / returns |
|---|---|---|---|
| `bot.py` | Telegram `getUpdates`; `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) | Poll loop; text → title match; buttons `pick:` `date:` `fmt:` `book:`; `run_watch_check` every `CHECK_INTERVAL` | Telegram messages; in-memory state (`sessions`, `current_movie_url`, `pending_movies`, `date_shows`) |
| `scraper.py` | harkins.com (headless Chromium) | `scrape_movies`, `get_now_playing`, `get_coming_soon`, `get_showtimes` (Arizona Mills only, skips passed shows, format via `FORMAT_JS`) | Lists of dicts; no files |
| `tracker.py` | `paths.SEEN_FILE`, `WATCHLIST_FILE`; scraper | `find_new_movies` (diff vs seen), `find_watchlisted_now_showing` (read-only), `refresh_coming_soon`, `remove_from_watchlist` (bot.py calls it after alerting) | `seen_movies.json`, `coming_soon.json`, `watchlist.json` |
| `add_watch.py` | `paths.*` JSON files | `find_matches` (substring, then `difflib`), `add_title`; also an interactive CLI | `watchlist.json` |
| `booker.py` | Showtime URL; `paths.PROFILE_DIR` (visible Chromium) | `_select_deterministic` (DOM seats, back-region center-out pairs) → reload → `seat_ai.pick_seats_by_vision`; `_finalize` sets ticket count | `{seats, url}` payment link or `{manual: True, url}` |
| `seat_ai.py` | Canvas screenshot; `.env` `GEMINI_API_KEY` | Gemini returns points; each click-tested; best adjacent pair near centroid | Selected seat ids or `None` |
| `telegram.py` | `.env` token/chat id | `send_telegram(message)` | One message. bot.py has its own `send`; booker.py imports `send_telegram` but never calls it |
| `paths.py` | — | Anchors all state paths to the repo root | Path constants |

## If you change this → what it hits

| Change | Hits | Does not hit |
|---|---|---|
| Harkins movie-list markup | `scraper.scrape_movies` → tracker caches → `find_matches` | booker |
| Harkins showtime markup / format CSS class | `scraper.get_showtimes`, `FORMAT_JS` → bot format picker | tracker |
| Harkins seat-map markup | `booker` selectors (`Select Seat`, `Continue to Checkout`, ticket picker), `seat_ai._selected`, canvas selector | scraper |
| A state file name/location | `paths.py`; `tools/explore_booking.py` (profile); `.gitignore` (untracked files would become tracked); `data/CONTEXT.md`; README file tree | module code |
| Ticket count | `booker.NUM_SEATS` (passed to seat_ai); bot message text says "2 seats" | scraper |
| Telegram callback prefixes | `bot.main` dispatch + every `send_buttons` caller | telegram.py |
| Gemini model / prompt | `seat_ai.MODEL`, `_PROMPT` | deterministic picker |
