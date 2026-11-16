# Goldfish Annotated Review Report (Round 2)

Status: resolved (auto accept applied; 46 fixes accepted, 0 contested, 0 escalated, 1 declined, 0 unaddressed). The review's one-re-audit limit is reached, so no round 3 follows.

**Target:** `docs/plan.md` (lines 146-381, "Detailed Implementation Plan" to end of file)

**Source report:** `swirling-kindling-whale-implementation_readiness_round2.md`. Finding IDs match it; the full discrepancy text is there. This is the last round: the review allows one re-audit, so no round 3 follows.

## Tally

| Category | Count |
| --- | --- |
| PROPOSE FIX | 46 |
| DECLINE | 1 (style-2-11, no action beyond cd-2-1 and cd-2-6) |
| CONTEST | 0 |
| ESCALATE | 0 |

I wrote the plan, so I checked each finding against the plan text and the legacy code first. None rest on a misreading. The reviewers' claims about legacy code (line numbers, constants, the `find_matches` behaviour, `find_new_movies` always saving, `booker.py:4` unused import) all hold.

## New choices the fixes make (overrule any)

| # | Choice | Findings |
| --- | --- | --- |
| C8 | Harkins login inside the worker container uses noVNC: a compose profile `login` runs `x11vnc` plus noVNC on `127.0.0.1:6080` only while `scripts/harkins_login.py` runs. Needed only if M4 pre-check (a) says login is required. | ir-2-2 |
| C9 | The title-match port is faithful: fuzzy matches only when there are zero substring hits. The M2 test is changed to match. | ir-2-12 |
| C10 | `/watch` on a coming-soon title saves the watch only (no showtimes exist yet). `/watch` on an already-playing title offers dates and saves nothing, as legacy does. "Today" is the date in the city's time zone. | cd-2-2 |
| C11 | `movies` is unique on `(chain_id, city_id, external_id)`; `movies` gets a `city_id` column. | ir-2-3, ir-2-24 |
| C12 | `jobs` gets `chain_id`, `city_id`, `max_attempts` and `result` columns. | ir-2-1 |
| C13 | Schema also gains `parsed_requests` (M6, migration `0002`), `chain_snapshots.status`, `chains.blocked_until`, and `users.pending_notified`. | ir-2-3, ir-2-15, ir-2-19, ir-2-20 |
| C14 | Migrations run from a one-shot compose service `migrate` (`alembic upgrade head`); `bot` and `worker` wait for it to complete. | ir-2-6 |

## Prioritized Findings & AI Suggestions

### Blockers

#### [Issue 1] [`ir-2-1`] - Severity: Blocker
*   **Affected Scope:** plan lines 211, 214, 358-363 and the `jobs` table
*   **Discrepancy:** The per-chain concurrency rule cannot be enforced: `book` and `probe_signals` payloads carry no chain; `check_chain` is limited per chain plus city; `max_attempts` storage, result persistence and the back-off exponent are undefined.
*   ***AI Suggestion:*** PROPOSE FIX (choice C12)
*   ***Proposed Edit:*** Location: Appendix A and Appendix B; M1 tests.
    *   Add to `jobs`: `chain_id` (FK `chains`, nullable), `city_id` (FK `cities`, nullable), `max_attempts int not null`, `result jsonb null`. `enqueue()` fills `chain_id`/`city_id` from the payload and `max_attempts` from the catalogue.
    *   Skip predicate in `claim()`: skip a queued job when another `running` job has the same `kind`, the same `chain_id`, and (for `check_chain` only) the same `city_id`. This applies to `check_chain`, `book` and `probe_signals`; `search_showtimes` is unlimited.
    *   Back-off: next `run_after` = now + base × 2^(attempts − 1), where `attempts` counts failed runs (60 s base for `check_chain` and `probe_signals`, 30 s for `search_showtimes`).
    *   `result` stores the `book` outcome (`held`/`manual`/`failed` plus fields); other kinds leave it null.
    *   M1 test: a second `book` job for `harkins` is not claimed while one is `running`; a `book` job for another chain is.

#### [Issue 2] [`ir-2-2`] - Severity: Blocker
*   **Affected Scope:** plan lines 198, 290 (M0 Dockerfile, M4 pre-check)
*   **Discrepancy:** A headed login inside the worker container has no visible display under `xvfb-run`; no mechanism is given.
*   ***AI Suggestion:*** PROPOSE FIX (choice C8)
*   ***Proposed Edit:*** Location: M0 Dockerfile and compose bullets; M4 step 1.
    *   M0: install `x11vnc` and noVNC in the `worker` target. Add a compose profile `login` that starts `scripts/harkins_login.py` with `x11vnc` and noVNC published on `127.0.0.1:6080` only (never a public interface).
    *   M4 step 1(a): "If a hold needs a login, run `docker compose --profile login run --service-ports worker python scripts/harkins_login.py`, open `http://127.0.0.1:6080` in your browser, log in to Harkins, and press Enter in the terminal. The script saves the session in `/profiles/harkins` on `showwatch_profiles`; the file `/profiles/harkins/.login_ok` records the time."
    *   If no login is needed, skip the login profile and note it in `docs/decisions/`.

### Majors

#### [Issue 3] [`ir-2-3`] - Severity: Major
*   **Affected Scope:** lines 278, 280, Appendix A
*   **Discrepancy:** `chain_snapshots` has no suspect/good flag and no Appendix A definition; "new release" is undefined; suspect scrapes might still write `movies`; alert audience and city scoping are unstated.
*   ***AI Suggestion:*** PROPOSE FIX (choices C11, C13)
*   ***Proposed Edit:*** Location: M3 mapping table; Appendix A.
    *   Appendix A: `chain_snapshots(chain_id, city_id, taken_at, movie_count int, status text check in ('good','suspect'))`; `movies` gains `city_id` (FK `cities`) and is unique on `(chain_id, city_id, external_id)`.
    *   M3 rules: a scrape is suspect when it returns zero movies or fewer than 50% of the last `good` snapshot; no earlier `good` snapshot means first run (store, no alerts). A suspect scrape writes a snapshot row only; it never upserts `movies` or sends alerts.
    *   "New release" = a `movies` row inserted with `status = now_showing`, or a status change `coming_soon` → `now_showing` (a reappearing title that was already `now_showing` is not new).
    *   Alert audience: approved users whose `current_city_id` equals the movie's `city_id`.

#### [Issue 4] [`ir-2-4`] - Severity: Major
*   **Affected Scope:** lines 347-352, 239
*   **Discrepancy:** No column types, nullability or defaults for most columns; `normalized_title` rule, `watch_items` uniqueness and the admin `chat_id` source are undefined.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: Appendix A.
    *   Add a column table per entity (name, type, null?, default). Types: `*_id bigint`, `telegram_user_id bigint`, `chat_id bigint null`, text columns `text`, timestamps `timestamptz`, `payload`/`evidence`/`result jsonb`, `run_after timestamptz not null default now()`, `attempts int not null default 0`, `locked_by text null`, `locked_at timestamptz null`, `is_admin bool not null default false`.
    *   `normalized_title` = `casefold()`, punctuation removed, whitespace collapsed.
    *   `watch_items` unique on `(user_id, city_id, query)` (legacy refuses duplicates, `add_watch.py` 78-80).
    *   `users.chat_id` is set from the first `/start` update; the admin row is created at startup with `chat_id` null until the admin sends `/start`.

#### [Issue 5] [`ir-2-5`] - Severity: Major
*   **Affected Scope:** lines 168, 276 (plan lines 85-87)
*   **Discrepancy:** Domain types and the Chain Protocol have no fields; `list_showtimes` has no city/theatre parameter.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: Architecture "Chain interface" and M0 `domain/models.py` row.
    *   Frozen dataclasses: `MovieListing(external_id, title, url, status)`; `ShowtimeListing(external_session_id, theatre_external_id, starts_at: aware datetime, format, booking_url)`; `Signal(stage, evidence: dict)`; `BookingResult(kind: Literal["held","manual"], seats: list[str], url: str)`. `WatchItem`, `Movie`, `Theatre`, `City` and `Showtime` are pydantic models mirroring the tables.
    *   Signature becomes `list_showtimes(self, movie: Movie, theatre: Theatre, day: date)`; the search job loads the theatre from the user's city.

#### [Issue 6] [`ir-2-6`] - Severity: Major
*   **Affected Scope:** lines 199, 209
*   **Discrepancy:** Nothing says who runs `alembic upgrade head`; `alembic.ini` and `script.py.mako` are missing from M1.
*   ***AI Suggestion:*** PROPOSE FIX (choice C14)
*   ***Proposed Edit:*** Location: M0 compose bullet; M1 file table.
    *   Compose: add a one-shot service `migrate` (bot image, command `alembic upgrade head`, `depends_on` postgres healthy); `bot` and `worker` use `depends_on: migrate: condition: service_completed_successfully`.
    *   M1 table: add `alembic.ini` and `migrations/script.py.mako` (Create).

#### [Issue 7] [`ir-2-7`] - Severity: Major
*   **Affected Scope:** lines 186, 199
*   **Discrepancy:** DSN host override, driver prefix, Postgres variables, healthcheck, volume path and required-versus-optional settings are unspecified.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: M0 settings and compose bullets.
    *   `.env` holds `DATABASE_URL=postgresql+asyncpg://showwatch:<password>@localhost:5433/showwatch`; compose overrides it for `bot`, `worker` and `migrate` with `environment: DATABASE_URL: postgresql+asyncpg://showwatch:${POSTGRES_PASSWORD}@postgres:5432/showwatch`.
    *   `postgres` service: `POSTGRES_USER=showwatch`, `POSTGRES_DB=showwatch`, `POSTGRES_PASSWORD` from `.env`; healthcheck `pg_isready -U showwatch -d showwatch`; volume `showwatch_pgdata` mounted at `/var/lib/postgresql` (the path Postgres 18 images expect).
    *   Required for `ready`: `DATABASE_URL`, plus `TELEGRAM_BOT_TOKEN` and `ADMIN_TELEGRAM_USER_ID` for `bot` only. All `LLM_*` keys are optional until M4.

#### [Issue 8] [`ir-2-8`] - Severity: Major
*   **Affected Scope:** line 181
*   **Discrepancy:** The hand-copy list omits `.env`; legacy `load_dotenv()` would fall back to the root `.env` that holds the new bot's token.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: M0 "Legacy" bullet.
    *   Add: "Create `legacy/.env` by hand with the three legacy keys (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`); copy the values from the old root `.env` without printing them. The new root `.env` then holds only new-stack keys."

#### [Issue 9] [`ir-2-9`] - Severity: Major
*   **Affected Scope:** lines 159-177, 301-304, 319
*   **Discrepancy:** Missing `__init__.py` files, `tests/conftest.py`, `.dockerignore`, `alembic.ini`, tool settings, Dockerfile steps and decision filenames; the M4 table omits `README.md`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: M0 and M4 file tables.
    *   M0 adds: `__init__.py` in every `src/showwatch/` subpackage, `tests/conftest.py` (async DB fixture using `TEST_DATABASE_URL`), `.dockerignore` (`.git`, `.venv`, `legacy`, `tests`).
    *   `pyproject.toml` settings: pytest `asyncio_mode = "auto"`, `testpaths = ["tests"]`; mypy strict on `src/`; ruff default rules plus `I`.
    *   Dockerfile: `uv sync --frozen --no-dev`, non-root user `app`, entrypoints `python -m showwatch.bot` and `python -m showwatch.worker`.
    *   Decision files: `docs/decisions/NNN-<slug>.md` with slugs `001-growth-path`, `002-bangalore-scope`, `003-language`, `004-repo-home`, `005-rebuild-approach`, `006-llm-layer`, `007-restructure-base`, `008-ownership`, `009-docker`, `010-database`, `011-postgres-container`, `012-process-split`, `013-layout`, `014-bangalore-order`, `015-insider-detection`, `016-access-control`, `017-testing`, `018-milestones`.
    *   M4 table: add `README.md` (Modify: IMAX results in the Results table).

#### [Issue 10] [`ir-2-10`] - Severity: Major
*   **Affected Scope:** lines 142-144, 198, 200, 266
*   **Discrepancy:** Dependencies and the pinning rule are open; no OpenAI-compatible client library is named; CI has no Chromium install; the fixture test's HTML loading is unstated.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: Stack paragraph; M0 CI bullet; M3 tests.
    *   Pinning rule: `pyproject.toml` pins exact versions chosen at M0 (the latest stable release of each on that day), and the worker image tag uses the same Playwright version.
    *   LLM calls use `httpx` directly (OpenAI-style chat JSON); no extra client library or Pillow.
    *   CI adds `uv run playwright install --with-deps chromium` before `pytest`; browser-dependent tests carry a `browser` marker.
    *   M3 fixture test loads saved HTML with `page.set_content(html)` in async Playwright.

#### [Issue 11] [`ir-2-11`] - Severity: Major
*   **Affected Scope:** lines 200, 228
*   **Discrepancy:** The 85% gate applies from M0 over modules that have no tests yet; include/omit mechanism and the handler-test database use are unstated.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: M0 CI bullet; M2-M4 tests.
    *   The gate starts at M1. `pyproject.toml` lists the measured modules in `[tool.coverage.run] source`/`include`; each milestone adds its modules (M1: `domain/`, `db/`; M2: `bot/handlers/access.py`, `bot/handlers/watch.py`; M3: `worker/scheduler.py`; M4: `chains/harkins/seatmap.py`, `llm/`).
    *   Handler tests use a fake Telegram bot and real repositories on Postgres (`TEST_DATABASE_URL`).
    *   Add tests: `tests/unit/test_search_handlers.py` (M3), `tests/unit/test_llm_client.py` (M4).

#### [Issue 12] [`ir-2-12`] - Severity: Major
*   **Affected Scope:** lines 227, 248
*   **Discrepancy:** The title-match port contradicts legacy (`difflib` runs only when there are zero substring hits) and has no signature.
*   ***AI Suggestion:*** PROPOSE FIX (choice C9)
*   ***Proposed Edit:*** Location: M2 `title_match.py` row and tests; M6 step 2.
    *   Signature: `find_matches(query: str, titles: list[str]) -> list[str]` (pure function; the repository supplies the titles of `movies` rows for the user's city, both statuses).
    *   Behaviour: lowercase substring matches; only if there are none, `difflib.get_close_matches(n=5, cutoff=0.3)`; at most 5 results.
    *   Tests: substring hits are returned alone when any exist; fuzzy hits are returned only when there are no substring hits; at most 5 results.

#### [Issue 13] [`ir-2-13`] - Severity: Major
*   **Affected Scope:** lines 263, 286
*   **Discrepancy:** The parity test would overwrite `legacy/data` (`find_new_movies` always saves), cannot feed saved HTML to `scrape_movies`, has no script path, seeding or alert-capture description, and needs parse separated from navigation.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: M3 file table, scraper row, and done-when.
    *   Scraper port exposes parse-only functions: `parse_movie_list(html: str) -> list[dict]` and `parse_showtimes(html: str) -> list[dict]`; navigation stays in separate functions. Fixtures come from a manual capture with `scripts/explore_site.py --save-html`.
    *   Add `scripts/parity_check.py` (Create) to the table.
    *   Done-when, numbered: (1) Copy `legacy/` to a temp folder, so `legacy/data` is untouched. (2) Parse `now_showing.html` into `[{"title","url"}]` dicts and pass them to the temp copy's `tracker.find_new_movies` and `find_watchlisted_now_showing`, with a seen-set missing 2 titles and a watchlist holding 1 of them. (3) Run the new `check_chain` on the same parsed list with the same seed (a seeded `movies` table, one user, one watch item) and capture alerts through a fake `notify`. (4) Both sides produce 2 new-release alerts and 1 watch alert, matched by type plus title. (5) A scrape with zero movies, once a `good` snapshot exists, is marked suspect and sends no alerts.

#### [Issue 14] [`ir-2-14`] - Severity: Major
*   **Affected Scope:** lines 211, 259, 191 (plan lines 94, 106)
*   **Discrepancy:** Scheduler pairs, duplicate-job avoidance, restart behaviour, jitter, sweep schedule, swept `book` jobs, stage transitions and `fast_until` are undefined.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: M3 (new "Scheduler spec" block).
    *   Every 60 s tick the scheduler: calls `sweep()`; for each enabled chain × city that has a `theatres` row, enqueues `check_chain` if no `queued` or `running` job exists for the pair and the last finished `check_chain` for it is older than `CHECK_INTERVAL_S` × (1 ± jitter). Due time is derived from the `jobs` table, so it survives restarts; jitter is applied to `run_after`.
    *   For each watch with `stage` in `listed|dates|venues` and `fast_until > now`, it enqueues `probe_signals` the same way at `FAST_INTERVAL_S`.
    *   A swept `book` job (max 1 attempt) becomes `failed` with `last_error = "worker crashed"` and the user gets the "Booking failed" message.
    *   Stage transitions: `waiting → listed → dates → venues → sessions → done`. `fast_until = now + FAST_WINDOW_H` when the stage first reaches `listed`. `movie_id` is matched on each `check_chain` for unmatched watches using `find_matches`. Harkins (no SIGNALS): `coming_soon` title → `listed`; `now_showing` → `sessions`, alert, then `done`.

#### [Issue 15] [`ir-2-15`] - Severity: Major
*   **Affected Scope:** lines 222, 232-239
*   **Discrepancy:** "One reply on first `/start`" needs persisted state; non-`/start` messages from unknown users, approve/deny callback formats and the status filter are unspecified.
*   ***AI Suggestion:*** PROPOSE FIX (choice C13)
*   ***Proposed Edit:*** Location: M2 handler spec; Appendix A.
    *   Add `users.pending_notified bool not null default false`; the one "Access requested" reply sets it.
    *   Any message from an unknown user is treated as `/start` (creates the `pending` row).
    *   Callback data: `approve:<user_id>`, `deny:<user_id>` (admin only; checked by `is_admin`).
    *   `bot/app.py` registers a `TypeHandler` in group -1 that raises `ApplicationHandlerStop` unless the sender is `approved`, or the update is `/start`/a first message from an unknown user.

#### [Issue 16] [`ir-2-16`] - Severity: Major
*   **Affected Scope:** line 282 (plan line 112)
*   **Discrepancy:** `fmt:<movie_id>:<day>:<format>` embeds a free-text format name and can exceed Telegram's 64-byte limit.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: M3 search flow (d); M3 tests.
    *   Callback becomes `fmt:<movie_id>:<day>:<n>`, where `n` is the position of the format in the alphabetical list of distinct formats stored for that movie and day (the tap re-reads that list).
    *   Test: every callback string built by `keyboards.py` is at most 64 bytes.

#### [Issue 17] [`ir-2-17`] - Severity: Major
*   **Affected Scope:** lines 291, 309, 360
*   **Discrepancy:** A locked-profile re-queue contradicts `max_attempts = 1`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: M4 step 2; Appendix B.
    *   "If launching the persistent context fails because the profile is in use, the handler calls `requeue(delay=60 s)`, which does not count as an attempt. Any other failure ends the job as `failed`."
    *   Appendix B `book` row note: lock re-queues do not count toward `max_attempts`.

#### [Issue 18] [`ir-2-18`, also `style`] - Severity: Major
*   **Affected Scope:** lines 195, 308, 317
*   **Discrepancy:** Per-model `coord_order`/`scale` has no settings shape; no `seat_vision` or `llm_client` test cases; fallback and timeout wording conflict; calibration has no acceptance rule or trigger.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: M0 settings table; Architecture "LLM layer"; M4 step 5 and tests.
    *   Settings add `LLM_VISION_COORD_ORDER` (`yx` or `xy`, default `yx`), `LLM_VISION_COORD_SCALE` (default 1000), and the same two with a `FALLBACK_` infix for the fallback provider.
    *   Wording: "one fallback provider" (not a list); `LLM_VISION_TIMEOUT_S` (default 45) applies to each provider.
    *   Calibration: run the 3 saved screenshots through the primary model; the order/scale values are changed only if the converted points of a response do not land on seat icons. Acceptance: for each screenshot, at least 7 of the first 10 points land on a seat icon when overlaid on the image (user checks the overlay).
    *   Tests, `tests/unit/test_seat_vision.py`: regex parse of a `[[y, x], …]` array; `xy`→`yx` and scale conversion; 2 attempts then the fallback provider. `tests/unit/test_llm_client.py`: routing by task, timeout, fallback on error.

#### [Issue 19] [`ir-2-19`] - Severity: Major
*   **Affected Scope:** lines 194, 325 (plan line 52)
*   **Discrepancy:** The request budget and 403/429 back-off exist only in M5, though the constraint covers every chain.
*   ***AI Suggestion:*** PROPOSE FIX (choice C13)
*   ***Proposed Edit:*** Location: M3 file table and steps; M5 step 3.
    *   M3 creates `src/showwatch/chains/politeness.py`: an in-memory token bucket per chain (`CHAIN_REQUEST_BUDGET_PER_MIN`) and back-off state persisted in `chains.blocked_until`, so it survives restarts. Back-off: 5 minutes, doubling to a 60-minute cap, reset after one clean response. Harkins uses it from M3. Add `tests/unit/test_politeness.py`.
    *   M5 step 3 points to this module and drops its own numbers.

#### [Issue 20] [`ir-2-20`] - Severity: Major
*   **Affected Scope:** lines 333-337, 359
*   **Discrepancy:** M6 needs a table for stored requests, a payload mapping, a free-text handler file, and a midnight rule.
*   ***AI Suggestion:*** PROPOSE FIX (choice C13)
*   ***Proposed Edit:*** Location: M6.
    *   Add a file table: `migrations/versions/0002_parsed_requests.py` (table `parsed_requests`: `id`, `user_id`, `payload jsonb`, `created_at`, `expires_at`; 1-hour expiry), `src/showwatch/bot/handlers/freetext.py` (free-text messages that are not commands), `src/showwatch/llm/tasks/intent.py`, `src/showwatch/llm/tasks/title_match.py`, and the tests.
    *   Mapping: confirming creates one `search_showtimes` job per day from `date_from` to `date_to` (at most 7); `part_of_day` and `format` filter the showtimes shown.
    *   Night rule: "night" matches 21:00-23:59 on the chosen day and 00:00-02:00 of the next day.

#### [Issue 21] [`ir-2-21`] - Severity: Major
*   **Affected Scope:** lines 323-325, 329, 341
*   **Discrepancy:** M5-M7 file tables are deferred; M5's done-when depends on a real opening that cannot be scheduled.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: plan header paragraph under "Detailed Implementation Plan"; M5 done-when.
    *   Header: "M5-M7 file tables are written at each milestone's re-plan checkpoint, after the site probe; the user approves them before coding. This is the one planned exception to the exact-file-list rule."
    *   M5 done-when adds a fallback: "If no real opening occurs within 7 days of coding, replay a recorded fixture sequence (listed → dates → venues → sessions pages) through the scheduler; alerts arrive in that order, once each."

#### [Issue 22] [`cd-2-1`] - Severity: Major
*   **Affected Scope:** lines 295-296
*   **Discrepancy:** "Behaviour unchanged" omits hardcoded constants and branches in `booker.py`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: M4, new "Constants carried over" table after step 4.
    *   Table (source: `legacy/booker.py`): first 8 pairs from `candidate_pairs`; abort the deterministic path after 2 failed seat clicks; seat id regex `([A-Z]+)(\d+)`; selectors `button[aria-label^="Select Seat"]:not([disabled])`, `aria-pressed`, `[data-testid^="ticketing-type-picker-add-"]`, `button:has-text("Continue to Checkout")`; waits 60 s for the seat map, 3000 ms hydration, 30 s `wait_for_url("**/session/*/ticketing/*")`, 15 × 200 ms checkout polling, 3 click retries, 8 iterations in `_clear_selection`; after a deterministic failure, reload the page before vision, and a failed reload returns the manual outcome.

#### [Issue 23] [`cd-2-2`, also `ir`] - Severity: Major
*   **Affected Scope:** lines 243, 280, 282
*   **Discrepancy:** The legacy watch and booking flow changes without a stated decision; "today" source is unstated.
*   ***AI Suggestion:*** PROPOSE FIX (choice C10)
*   ***Proposed Edit:*** Location: M2 `/watch` row; M3 search flow (a).
    *   `/watch` on a `coming_soon` title saves the watch only. `/watch` on a `now_showing` title offers dates (`pick:`) and saves nothing, as legacy does. The 7 date buttons start from today's date in the city's time zone.

#### [Issue 24] [`style-2-1`] - Severity: Major
*   **Affected Scope:** lines 155, 286
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: Step 0 item 2. Replace with: "The EGM Stage 9 reports are copied to `docs/review/` (round 1, round 2 and both annotated reports). The user approves the plan before M0 starts." Drop the counts and the "I edit" sentence.

#### [Issue 25] [`style-2-2`] - Severity: Major
*   **Affected Scope:** lines 148, 154, 155
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: section intro and Step 0. Use imperative or passive voice: "Run `ruff`, `mypy` and `pytest` locally before each commit"; "Open a PR to `main` at the end of each milestone, after the user confirms"; "Commit `docs/plan.md`; move the `data/CONTEXT.md` and `tools/CONTEXT.md` routing files into `legacy/` with their folders."

#### [Issue 26] [`style-2-3`] - Severity: Major
*   **Affected Scope:** lines 286, 319, 329, 337
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** Location: done-when of M3, M4, M5, M6.
    *   M3: replaced by the numbered list in Issue 13.
    *   M4: separate bullets: "5 of 5 live standard-screen holds succeed"; "3 IMAX attempts recorded in the README Results table. The result decides whether the plan keeps vision as the IMAX path or lists IMAX as manual-link only; baseline: 0 of 3 in the original benchmark".
    *   M5: bullets for "stage alerts arrive in order", "no duplicate alerts", "no back-off during the two runs", plus the fixture fallback (Issue 21).
    *   M6: "the parse test passes on 5 recorded phrases (Dune IMAX Friday evening 2 seats; two other English phrases; one Hindi; one Kannada)"; "the live confirm-then-book step shows a booking offer for a Harkins movie with showtimes that day"; "a Kannada title variant and a Hindi title variant each resolve to the right BMS movie in tests".

### Nits

#### [Issue 27] [`ir-2-22`]
*   ***AI Suggestion:*** PROPOSE FIX. M0 done-when: use `python -m compileall -q legacy` (no shell glob).

#### [Issue 28] [`ir-2-23`]
*   ***AI Suggestion:*** PROPOSE FIX. Section intro: "`revamp` stays the long-lived branch. Each milestone PR is merged into `main` with a merge commit, then `main` is merged back into `revamp`." Step 0 gets a Path | Action table (`docs/plan.md` Create; `docs/review/*.md` Create).

#### [Issue 29] [`ir-2-24`]
*   ***AI Suggestion:*** PROPOSE FIX. M3 rules: exclude the `/movies/coming-soon` link from the movie-link selector; a title on both pages stays `now_showing`; keep the `FORMAT_JS` fallback chain (`img[alt]`, then `h3/h4`, then `data-testid`); replace the fixed 5000 ms wait with waiting up to 15 s for the link selector (intentional change); the `{movie_url}/{date}` URL is built in the search handler, not the scraper (as in `bot.py:83`).

#### [Issue 30] [`ir-2-25`]
*   ***AI Suggestion:*** PROPOSE FIX. M3 search flow (d): empty result replies "No showtimes at Arizona Mills that day." (legacy `bot.py:85`); time button label = the showtime text.

#### [Issue 31] [`ir-2-26`]
*   ***AI Suggestion:*** PROPOSE FIX. M3: add a stub `book:` handler replying "Booking arrives in a later update."; M4 replaces it.

#### [Issue 32] [`ir-2-27`]
*   ***AI Suggestion:*** PROPOSE FIX. M0 Legacy bullet: "the legacy venv must not install `python-telegram-bot`, because `legacy/telegram.py` would conflict with its `telegram` package". M4 booker row: "drop the unused `from telegram import send_telegram` import".

#### [Issue 33] [`ir-2-28`]
*   ***AI Suggestion:*** PROPOSE FIX. M4 table: `scripts/smoke_book.py --showtime-url <url>` prints the outcome dict (`held`/`manual`) and never pays.

#### [Issue 34] [`ir-2-29`]
*   ***AI Suggestion:*** PROPOSE FIX. M0 logging bullet: key matching is case-insensitive; add a test with `Telegram_Token` and `API_KEY` keys.

#### [Issue 35] [`cd-2-3`]
*   ***AI Suggestion:*** PROPOSE FIX. M4: keep the legacy name `NUM_SEATS = 2`, not `SEATS` (3 places).

#### [Issue 36] [`cd-2-4`]
*   ***AI Suggestion:*** PROPOSE FIX. M0 Legacy bullet: pin `requests==2.34.2` in `legacy/requirements.txt`; the legacy venv uses Python 3.14 (the version that produced the existing `__pycache__`).

#### [Issue 37] [`cd-2-5`]
*   ***AI Suggestion:*** PROPOSE FIX. M0 table: replace the grouped `legacy/` row with exact moves (Action "Move"): `bot.py`, `booker.py`, `scraper.py`, `seat_ai.py`, `tracker.py`, `add_watch.py`, `telegram.py`, `paths.py`, `requirements.txt`, `CONTEXT.md`, `data/`, `tools/explore.py`, `tools/explore_booking.py`, `tools/test_setup.py`, `tools/CONTEXT.md` → under `legacy/`. M4 table: list `legacy/` Delete as that exact set.

#### [Issue 38] [`cd-2-6`]
*   ***AI Suggestion:*** PROPOSE FIX. M4 step 5: "The 45 s timeout is a change from legacy's hardcoded 60 s; retry sleeps of 4 s and 2 s are kept; the 20-point cap lives only in the prompt text; read the model text from `choices[0].message.content`."

#### [Issue 39] [`cd-2-7`]
*   ***AI Suggestion:*** PROPOSE FIX. M4 outcomes: "Two intentional changes from legacy: any exception becomes the `failed` outcome (legacy let it escape the polling loop, and its `result is None` check never fires); the 'Booking the best seats for you… hang on.' message is kept as the reply sent when the job is enqueued."

#### [Issue 40] [`style-2-4`]
*   ***AI Suggestion:*** PROPOSE FIX. Keep the pre-commit and PR statement once (section intro); delete the repeated lines in Verification; drop "Every milestone lists its files as…", "Old bot kept runnable" and "Details:".

#### [Issue 41] [`style-2-5`]
*   ***AI Suggestion:*** PROPOSE FIX. M0 done-when adds: "the `worker` container launches headed Chromium under `xvfb-run` and prints a page title (`docker compose run worker xvfb-run python -c "…"`)".

#### [Issue 42] [`style-2-6`]
*   ***AI Suggestion:*** PROPOSE FIX. M4 step 2: replace "makes this rare" with "expected only after a crashed job left the profile locked".

#### [Issue 43] [`style-2-7`]
*   ***AI Suggestion:*** PROPOSE FIX. Risks table: "Checked in M4 step 1" for the login and RC rows; drop the "Seat holds for other than 2 seats" row (scope is in C1) and keep the phase C row.

#### [Issue 44] [`style-2-8`]
*   ***AI Suggestion:*** PROPOSE FIX. M5 step 1: "at most 1 hour".

#### [Issue 45] [`style-2-9`]
*   ***AI Suggestion:*** PROPOSE FIX. One notation everywhere: "base × 2^(attempts − 1), capped at 60 minutes" (Appendix B, M3 politeness, M5).

#### [Issue 46] [`style-2-10`]
*   ***AI Suggestion:*** PROPOSE FIX. Covered by the numbered list in Issue 13; delete the old semicolon sentence.

#### [Issue 47] [`style-2-11`]
*   ***AI Suggestion:*** DECLINE. The reviewer's own note says no separate action beyond cd-2-1 and cd-2-6, which Issues 22 and 38 cover.

## After auto accept

*   **Applied:** the 46 PROPOSE FIX items, as one batch of edits to the plan file (the plan grows by roughly 120 lines).
*   **Not applied:** Issue 47.
*   **No further review round:** the one-re-audit limit is reached. Any gaps these edits introduce are handled at each milestone's check.
