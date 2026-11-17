# ShowWatch (repo: HarkinsWatch)

Watches movie theatre chains, tells approved Telegram users the moment tickets open, and books seats where the chain allows it.

**Revamp in progress.** The new app lives in `src/showwatch/` and follows [docs/plan.md](docs/plan.md) (milestones M0–M7). The original Harkins-only bot is kept in `legacy/` until the new one matches it (milestone M4).

## Running the new stack

M0 is the scaffold: Postgres, migrations (none yet), and `bot` and `worker` containers that log `ready`. Features arrive milestone by milestone.

```powershell
uv sync
copy .env.example .env      # then fill in the keys; see .env.example
docker compose up --build
```

Checks before every commit:

```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run mypy ; uv run pytest
```

More: [docs/architecture.md](docs/architecture.md), [docs/decisions/](docs/decisions/).

---

## Legacy bot (now in `legacy/`)

Everything below describes the original bot. Paths are relative to `legacy/`. It runs in its own venv (see "Setup (legacy bot)").

A personal automation bot for Harkins Theatres (Arizona Mills). It watches for movies you care about, tells you the moment they're bookable, and books your seats for you — all from a Telegram chat.

## What it does

1. **Tracks movies** — scrapes Harkins' Now Showing / Coming Soon pages and remembers what it's already seen.
2. **Watchlist** — text the bot a movie title; if it's not out yet, it's added to a watchlist and you're pinged the moment it's Now Showing.
3. **Booking flow** — pick a date, then (if the movie plays in more than one format) a screen type, then a showtime — all via Telegram buttons. The bot opens the seat map, auto-selects 2 seats near the center, and sends back the checkout link so you can review and pay on your phone.
4. **Notifications** — alerts go out over Telegram: new releases hitting Now Showing, and watchlisted movies becoming bookable.

### Example flow

**A watchlisted movie becomes bookable — the bot proactively reaches out:**
```
🎬 'Spider-Man: Un nuevo día [doblado en Español]' is now playing at Arizona Mills!
                                                          [🎟 Book seats]
```
Tapping "Book seats" → pick a date → tap a showtime → the bot books it:
```
Booking the best seats for you... hang on.
Seats G6 + G7 held! Pay here:
https://harkins.com/ticketing/theatre/16/cinema/.../ticketing/...
```

**Asking for a movie by name directly:**
```
You:  Ice cream man
Bot:  Reply with a number to add:
      1. Ice Cream Man
You:  1
Bot:  'Ice Cream Man' — pick a date:
      [Fri Aug 07] [Sat Aug 08] ... [Thu Aug 13]
      Tap a showtime to book 2 seats:
      [4:20 PM] [7:00 PM] [9:20 PM]
      Booking the best seats for you... hang on.
      Seats E5 + E6 held! Pay here:
      https://harkins.com/ticketing/theatre/16/cinema/.../ticketing/...
```
If a movie plays in more than one format (e.g. Standard, IMAX, CINÉ XL), the bot asks you to pick a screen type before showing times.

## How it works (high level)

```
Telegram chat
     │
     ▼
   bot.py  ──── scraper.py ──── harkins.com (Playwright, scrapes movies/showtimes)
     │
     ├──── tracker.py ──── seen_movies.json / coming_soon.json / watchlist.json
     │
     ├──── add_watch.py ──── watchlist.json
     │
     └──── booker.py ──── harkins.com (Playwright, opens seat map, books 2 seats)
                │
                └──── seat_ai.py ──── Gemini vision (fallback for hard/IMAX seat maps)
```

`bot.py` polls Telegram for messages/button taps in a loop, and every 30 minutes it re-scrapes Harkins to see if anything on your watchlist has opened for booking.

## File structure

```
legacy/
├── bot.py                 # Main entry point — the Telegram bot (run this)
├── scraper.py               # Scrapes Harkins "Now Showing"/"Coming Soon" + a movie's showtimes (with screen format)
├── tracker.py                 # Compares scraped movies against what's already been seen; manages the watchlist file
├── add_watch.py                 # Fuzzy-matches a typed movie title against known titles, adds it to the watchlist
├── booker.py                      # Opens a showtime's seat map and books 2 center seats (deterministic + AI fallback)
├── seat_ai.py                       # Gemini-vision fallback: reads the seat map as an image when clicking blind fails
├── telegram.py                        # Thin wrapper for sending a Telegram message
├── paths.py                             # Single place where all data-file paths are defined
│
├── data/                          # Runtime state (see data/CONTEXT.md)
│   ├── watchlist.json               # Movies you're waiting on (not yet Now Showing)
│   ├── seen_movies.json             # Cache of movies already seen in "Now Showing" (used to detect new ones)
│   ├── coming_soon.json             # Cache of the "Coming Soon" list
│   └── browser_profile/             # Persistent Chromium profile (keeps you logged into Harkins between runs)
│
├── tools/                         # Dev-only scripts, not used by the bot
│   ├── explore.py                   # One-off script used to explore the Harkins site's selectors
│   ├── explore_booking.py           # Manual tool: opens a real browser window, saves HTML snapshots as you click around
│   └── test_setup.py                # Sanity check that Playwright/Chromium is installed correctly
│
├── CONTEXT.md                     # Module contracts for AI coding agents
├── requirements.txt               # Python dependencies (playwright, python-dotenv, requests)
└── .env                           # Secrets for the legacy bot: create by hand, never commit
```

The build log of the original bot is [docs/procedure.md](docs/procedure.md) (repo root).

### Files not part of the app (debug/scratch output)
`dump_1.html`, `dump_2.html`, `diag2.png` are one-off snapshots saved while debugging the seat map — safe to delete, they're recorded page states, not code.

## Module walkthrough

### `bot.py` — the Telegram bot (run this)
Runs an infinite polling loop against the Telegram `getUpdates` API. Handles:
- Free text → fuzzy-matches it to a Harkins movie title (`add_watch.find_matches`) and asks you to confirm with a number.
- Confirming a match → if the movie is Now Showing, starts the booking flow immediately; if it's Coming Soon, adds it to the watchlist for later.
- Every 30 minutes → re-checks Harkins; any brand-new Now Showing title gets a plain "🆕 ..." alert, and any watchlisted movie that's now showing gets a "🎬 ... 🎟 Book seats" push.
- Date picker (`offer_dates`) → next 7 days as buttons.
- Screen-type picker (`handle_format`) → only shown if a movie plays in more than one format that day.
- Showtime picker (`_send_time_buttons`) → tapping a time calls `handle_book()`, which runs `booker.book_session()` and replies with the seats held + payment link, or a manual-pick link if auto-booking couldn't find a spot.

### `scraper.py`
Uses Playwright to load `harkins.com/movies` and `harkins.com/movies/coming-soon`, pulling movie titles/links from anchor tags. `get_showtimes()` loads a movie's page for a given date, skips showtimes that have already passed, and extracts each Arizona Mills (theatre ID 16) showtime's time, URL, and screen format (Standard/IMAX/CINÉ XL/etc.).

### `tracker.py`
Pure bookkeeping: diffs the current Now Showing list against `seen_movies.json` to find new releases, and checks which watchlisted titles have become Now Showing (`find_watchlisted_now_showing`), removing them from the watchlist once found.

### `add_watch.py`
Matches a user-typed movie name against cached titles (substring match, falling back to `difflib.get_close_matches` for typos). Used both as a CLI tool and imported by `bot.py`.

### `booker.py`
The seat-booking logic. Given a showtime URL:
1. Opens the seat map in a (visible) Chromium browser using the persistent `browser_profile`.
2. **Deterministic pass** (`_select_deterministic`) — reads every seat button's position from the DOM, splits the auditorium into back/front regions by the aisle gap, and ranks adjacent seat pairs by closeness to the center (back rows preferred). Tries clicking pairs in that order until the "Continue to Checkout" button enables.
3. **Vision fallback** (`seat_ai.pick_seats_by_vision`) — if the deterministic pass fails (common on large IMAX-style canvases where the DOM buttons don't reliably reflect availability), the page is reloaded fresh and a screenshot of the seat map canvas is sent to Gemini, which returns candidate available-seat coordinates. Those are click-tested, and the best adjacent pair nearest the center is chosen.
4. Returns either `{"seats": ..., "url": checkout_link}` on success, or `{"manual": True, "url": session_url}` if nothing could be auto-selected, so you can pick seats yourself.

### `seat_ai.py`
Wraps the Google Gemini API (free tier, `gemini-flash-latest`). Sends a screenshot of the seat-map canvas with a prompt describing which seat icons are available vs. taken, gets back normalized coordinates, click-tests each to confirm it actually selects a real seat, then hands the confirmed set back to `booker.py` to pick the best adjacent pair.

### `telegram.py`
Small, single-purpose sender reading the bot token/chat id from `.env`.

## Results

Live benchmark on 2026-11-14: 10 real `book_session()` runs against next-day Arizona Mills showtimes (7 standard/Digital and 3 IMAX). Each run held 2 seats and stopped at the payment page, so no payment was made.

| Metric | Result |
|---|---|
| Standard screens booked end-to-end (seats held + payment link) | **7 / 7 (100%)** |
| Time per booking, standard (seat map → payment page) | **median 12.9 s** (range 11.9–15.3 s) |
| Seat-map strategy on standard screens | deterministic DOM picker on every run (0 Gemini calls) |
| IMAX screens | 0 / 3 auto-booked. The Gemini vision fallback timed out (60 s, retried once), so each run safely returned the manual seat-pick link after ~144 s |
| Runs that returned a usable link (payment or manual fallback) | **10 / 10** |
| Now Showing scrape | 65 movies in 9–12 s |
| Showtime scrape (one movie + date) | median ~7.7 s |

Earlier manual run: The Odyssey (IMAX 70mm) was booked through the Gemini vision path and reached the payment page in ~45 s.

### New stack (`showwatch`), M4 live runs on 2026-11-16

Real `book_session()` runs through `scripts/smoke_book.py` against Arizona Mills sessions; each held 2 seats and stopped at the checkout page (no payment). The browser had no login (see `docs/decisions/022-harkins-guest-hold.md`).

| Metric | Result |
|---|---|
| Standard screens, 5 different sessions | **5 / 5 held**, all by the seat-button picker (about 30 s per run including browser start) |
| IMAX, 3 different sessions, vision `qwen36-35b-a3b` on the ASU RC gateway | **2 / 3 held** by vision (the third fell back to the manual seat-map link). An earlier run of the same 3 sessions with the first build held 3 / 3 |
| Vision-only run on a standard map | 1 of 2 runs found seats before a second ask was added; held after it |
| Vision point accuracy (first 10 points landing on a seat, one map) | `qwen36-35b-a3b` 9/10 in 4.4 s; `qwen38-27b` 8/10; `gemma4-31b-it` 4/10; `qwen3-vl-32b` and `llama4-scout-17b` returned nothing usable; `qwen36-35b-a3b` with thinking on timed out |

## Setup (legacy bot)

See [docs/procedure.md](docs/procedure.md) for the full step-by-step history of how this was built. Short version:

```powershell
cd legacy
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

The legacy venv must not install `python-telegram-bot`: `legacy/telegram.py` would conflict with its `telegram` package.

Create `legacy/.env` with:
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
GEMINI_API_KEY=...
```
(Get a free Gemini key at aistudio.google.com/apikey — used only as a fallback for hard-to-click seat maps.)

Run the bot:
```powershell
python bot.py
```
