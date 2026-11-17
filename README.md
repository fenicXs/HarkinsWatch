# ShowWatch (repo: HarkinsWatch)

Watches movie theatre chains, tells approved Telegram users the moment tickets open, and books seats where the chain allows it.

The app lives in `src/showwatch/` and follows [docs/plan.md](docs/plan.md) (milestones M0–M7; M0–M6 are built).

## Running the new stack

`docker compose up --build` starts Postgres, runs the migrations, and starts the `bot` and `worker` containers (each logs `ready`). Keys go in `.env` (names in `.env.example`) and the LLM keys in `.env.llm`; neither is committed.

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

## What it does

1. **Watches movies** for each approved user in their city (Phoenix: Harkins; Bengaluru: BookMyShow and District), and tells them the moment a watched title is listed and again when tickets open.
2. **Books seats at Harkins**: pick a movie, date and showtime in Telegram; the worker holds 2 seats and sends the checkout link (it never pays). Large seat maps are read by a vision model.
3. **Understands plain language**: "Dune IMAX Friday evening", in English, Hindi or Kannada, is read back for confirmation, then searched (Harkins) or watched (BookMyShow, District).
4. **Access control**: new users ask for access; the admin approves or denies in Telegram.

How it is built: [docs/architecture.md](docs/architecture.md). Decisions: [docs/decisions/](docs/decisions/). Plan and per-milestone notes: [docs/plan.md](docs/plan.md).

The original single-file bot was removed after M4. It is still in git history: `git show legacy-last --stat` (tag `legacy-last`). Its benchmark is kept below for comparison.

## Results

Original single-file bot (now deleted), live benchmark on 2026-11-14: 10 real `book_session()` runs against next-day Arizona Mills showtimes (7 standard/Digital and 3 IMAX). Each run held 2 seats and stopped at the payment page, so no payment was made.

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

### New stack (`showwatch`), M5 Bangalore watch-only chains, live runs on 2026-11-16

BookMyShow and District are read through a real browser window without login (decisions 019, 020). PVR INOX returned 403 Access Denied and was dropped (decision 021). Neither chain books: the alert carries a link and you book by hand.

| Check | Result |
|---|---|
| Plain HTTP or headless Chromium against BookMyShow | blocked (Cloudflare challenge); a headed browser works |
| BookMyShow movie list, installed Chrome on the PC | 20 movies (after scrolling to load the whole list) |
| BookMyShow, headed Chromium inside the worker container (Xvfb) | list of 11 movies, no block |
| District movie list | 64 movies |
| "Tickets open" on a movie that has sessions (Ramayana), both chains | detected as open on both |
| BookMyShow on a movie with no sessions (Avengers: Doomsday, 17 Dec) | page says "We couldn't find anything"; parsed as closed |
| District for the same unreleased movie | lists sessions for 17 Dec already, earlier than BookMyShow |
| End-to-end in the running worker: watch added, list check, probe | watch moved `listed` then `sessions` then `done` with no block. The Telegram send could not be checked: the database holds a placeholder admin ID (see below) |

### New stack (`showwatch`), M6 plain-language requests, live runs on 2026-11-17

The bot reads a free-text message with an LLM on the ASU RC gateway, shows what it understood with a Confirm button, and acts on the tap: a showtime search for Harkins (Phoenix), a watch for BookMyShow and District (Bengaluru).

| Check | Result |
|---|---|
| Text model comparison, the plan's 5 phrases (3 English, 1 Hindi, 1 Kannada) | `qwen36-35b-a3b` 4/5 strict at about 1.5 s (the miss was only "Ramayan" for the Hindi title, which title matching handles); `gemma4-31b-it` 5/5 at about 5 s; `glm-5-3-flash` 5/5 but 7-17 s; `mistral-large-4` 3/5 at 30+ s |
| Chosen | `qwen36-35b-a3b`, fallback `gemma4-31b-it` |
| Live, Kannada phrase "ನಾಳೆ ಸಂಜೆ ರಾಮಾಯಣ ಟಿಕೆಟ್ ಬೇಕು" in Bengaluru | read back as "Ramayana, Wed Nov 18, evening"; Confirm created the watch |
| Live, "two tickets for Ebenezer tomorrow evening" in Phoenix | read back correctly; Confirm ran a real Harkins lookup and offered only the evening show (6:10 PM) |
| Automated | 450 tests, 94% coverage |

### New stack (`showwatch`), format-aware watches, live runs on 2026-11-17

| Check | Result |
|---|---|
| Before: a watch for Avengers (wanting 2D) alerted on District's 3D-only sessions | the case that prompted this change |
| Plain-language request "Avengers Doomsday in Bangalore, only plain 2D or MS Infinity Vision, no 3D shows" | read back as "(2D or MS - Infinity Vision; no 3D)" and stored as the watch's preference |
| BookMyShow, plain page with that preference | closed; the 3D format page is skipped |
| BookMyShow, no preference | follows the format selector to the 3D page (46 sessions) |
| District with that preference | 40 sessions, all "MS INFINITY VISION 3D": rejected, no alert |
| Worker log | `sessions_not_wanted` for District; the watch stays `listed` |
| Automated | 488 tests, 94% coverage |
