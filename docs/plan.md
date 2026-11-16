# ShowWatch revamp plan

## Context

HarkinsWatch is a ~1,260-line flat Python Telegram bot that watches one Harkins theatre (Arizona Mills) and holds 2 seats via Playwright, with Gemini vision as a fallback for canvas seat maps. The original author handed it over on 2026-11-16; the user now owns it (`github.com/fenicXs/HarkinsWatch`) and wants it rebuilt as a professionally structured, multi-city app.

Problems in the current code that drive the rebuild (found by reading, not yet reproduced live):

- Single-user global state: stale Telegram buttons map to whatever showtime was listed last, so an old button can book the wrong show (`bot.py` `sessions`, `current_movie_url`).
- No sender check: any Telegram user who finds the bot can trigger searches and bookings.
- A booking (12–144 s) blocks the whole polling loop.
- A failed scrape overwrites the seen-movies baseline (`tracker.find_new_movies`).
- IMAX booking failed 0/3 in the owner's benchmark because the Gemini call timed out.
- One theatre, one city, one site; no tests; no database; `requests` missing from `requirements.txt`.

Intended outcome: a new package, `showwatch`, that watches several theatre chains in Phoenix and Bangalore, alerts approved users the moment tickets open, books seats where the chain allows it, and is structured so a public web app (phase C) can be added later without a rewrite.

### Decisions already made (grilling session, 2026-11-16)

| # | Decision |
|---|---|
| 1 | Growth path: personal (A) → small multi-user (B) → public product (C). Build for B from day one. |
| 2 | Bangalore: watch and notify only at first; booking added later per site. |
| 3 | Language: Python. |
| 4, 5, 7, 8 | Same repo, rename later; user owns it; clean rebuild that ports proven logic; restructure commit `083cf1c` is the base. |
| 6 | LLM layer over the OpenAI-compatible API. ASU RC gateway for phases A/B; a commercial key (Gemini) as fallback and as the phase C default. Uses: seat-map vision, plain-language requests, cross-language title matching. |
| 9 | Docker from day one; runs on the user's Windows PC. Hosting revisited in phase B. |
| 10–11 | PostgreSQL in its own Compose container (`postgres:18-alpine`, already pulled), host port 5433 (native PostgreSQL 18 already holds 5432). Start with an empty database. |
| 12 | Two containers from one codebase: `bot` and `worker`, communicating through a Postgres job table. No Redis. Telegram long polling. |
| 13 | `src/` layout below; working package name `showwatch`. |
| 14–15 | Bangalore order: BookMyShow, then District, then PVR INOX. "Insider" detection = earliest detection of public signals with per-movie polling that speeds up near opening; no use of non-public endpoints, no CAPTCHA solving, no stealth fingerprinting, no proxy rotation. User accepts the ban risk of BookMyShow's terms. |
| 16 | Access: admin Telegram user ID from settings; others request access and the admin approves or denies with buttons. |
| 17 | Tests: unit, fixture (saved pages), integration (Postgres through `TEST_DATABASE_URL`), manual live smoke; CI on GitHub Actions; ≥85% coverage on logic modules. |
| 18 | Milestone order M0–M7 below. |

### Choices made while resolving the EGM review (overrule any)

| # | Choice |
|---|---|
| C1 | Seat count is fixed at 2 for M4; the bot rejects other counts. A picker for other counts is future work. |
| C2 | The new stack uses its own Telegram bot token (a second bot made in BotFather), so legacy and new can poll side by side. |
| C3 | `testcontainers` is dropped. Integration tests use `TEST_DATABASE_URL`: the CI Postgres service container in CI, the Compose Postgres on port 5433 locally. |
| C4 | "Healthy" means Postgres healthy plus `bot` and `worker` running and logged `ready`. |
| C5 | The IMAX step records results only, with no pass mark. A failed hold falls back to the manual seat-pick link. |
| C6 | A scrape is "suspect" when it returns zero movies or fewer than 50% of the last good snapshot's movie count. |
| C7 | M5's "no back-off" test runs 2 hours at the 30-minute base interval, plus one 10-minute run at the 1-minute fast interval. |
| C8 | Harkins login inside the worker container uses noVNC through a compose profile `login`, published on `127.0.0.1:6080` only. Needed only if the M4 pre-check says a hold requires a login. |
| C9 | The title-match port is faithful to legacy: fuzzy matching runs only when there are zero substring hits. |
| C10 | `/watch` on a coming-soon title saves the watch only. `/watch` on an already-playing title offers dates and saves nothing, as legacy does. "Today" is the date in the city's time zone. |
| C11 | `movies` has a `city_id` and is unique on `(chain_id, city_id, external_id)`. |
| C12 | `jobs` has `chain_id`, `city_id`, `max_attempts` and `result` columns. |
| C13 | The schema also has `parsed_requests` (M6, migration `0002`), `chain_snapshots.status`, `chains.blocked_until` and `users.pending_notified`. |
| C14 | Migrations run from a one-shot compose service `migrate`; `bot` and `worker` wait for it. |

### Hard constraints

- Only public data. A chain module may call an endpoint only if the site's own public pages call it without login.
- Polite polling: jittered intervals, exponential back-off on HTTP 403/429 or a challenge page, and a per-chain request budget (`chains/politeness.py`, M3).
- Never print, log or commit secrets. Settings come from `.env`; the repo ships `.env.example` with key names only. The repo is public.
- No push without the user's per-push confirmation. Work happens on branch `revamp`.

## Architecture

### Processes

```
Telegram ⇄ bot container ──writes──▶ jobs table ◀──claims── worker container ──▶ chain sites (Playwright / HTTP)
               │                         ▲                        │
               └──────── Postgres (users, watches, movies, showtimes, signals, alerts) ────────┘
                                                                  └──▶ Telegram Bot API (sends alerts and booking results)
```

- `bot`: handles Telegram updates only. It never scrapes or opens a browser. User actions that take time (search showtimes, book) become rows in `jobs`.
- `worker`: runs a scheduler loop (enqueues chain-check jobs on intervals) and a job runner (claims jobs with `SELECT … FOR UPDATE SKIP LOCKED`). It sends results straight to Telegram through a shared `notify` module, so no reply channel back to the bot is needed.
- Per-chain concurrency limits are enforced inside `claim()` (Appendix B), because a chain's persistent browser profile cannot be opened twice.

### Chain interface

Each chain is a module under `showwatch/chains/<code>/` that declares what it can do:

```python
class Capability(Flag):
    WATCH = auto()  # list movies / coming-soon for a city
    SHOWTIMES = auto()  # list sessions for a movie and date
    SIGNALS = auto()  # report pre-opening signals for insider detection
    BOOK = auto()  # hold seats and return a payment link


class Chain(Protocol):
    code: str
    capabilities: Capability

    async def list_movies(self, city: City) -> list[MovieListing]: ...
    async def list_showtimes(
        self, movie: Movie, theatre: Theatre, day: date
    ) -> list[ShowtimeListing]: ...
    async def probe_signals(self, watch: WatchItem) -> list[Signal]: ...
    async def book(self, showtime: Showtime, seats: int) -> BookingResult: ...
```

Calling a method whose capability is missing raises `CapabilityNotSupported`. Harkins = WATCH | SHOWTIMES | BOOK. BookMyShow, District and PVR INOX start as WATCH | SHOWTIMES | SIGNALS.

Domain types (`domain/models.py`). Frozen dataclasses: `MovieListing(external_id, title, url, status)`; `ShowtimeListing(external_session_id, theatre_external_id, starts_at: aware datetime, format, booking_url)`; `Signal(stage, evidence: dict)`; `BookingResult(kind: Literal["held","manual"], seats: list[str], url: str)`. `City`, `Theatre`, `Movie`, `Showtime`, `WatchItem` and `User` are pydantic models mirroring the tables.

### Insider detection (signal ladder)

For a watched movie in a city, a chain reports signals in stages: `listed` (movie page exists) → `dates` (a date list appears) → `venues` (theatres appear) → `sessions` (bookable sessions appear). Each new stage is stored in `signals` and alerted once per user. While a watch's `stage` is `listed`, `dates` or `venues`, it is polled by `probe_signals` jobs at the fast interval (`FAST_INTERVAL_S`, 60 s) until `sessions` is reached or `FAST_WINDOW_H` (72 h) expires; other watches are covered by the base `check_chain` job (`CHECK_INTERVAL_S`, 1800 s). All intervals get ±20% jitter.

### Data model

Tables: `users`, `cities`, `chains`, `theatres`, `movies`, `showtimes`, `watch_items`, `signals`, `alerts`, `jobs`, `chain_snapshots` (first migration), and `parsed_requests` (M6). Appendix A holds the column-level spec.

Telegram button data carries record IDs (for example `book:<showtime_id>`), never list positions, and every callback string is at most 64 bytes.

### LLM layer

`showwatch/llm/client.py` wraps the OpenAI-compatible chat API over `httpx`. Each task names its own endpoint, model, key and timeout in settings (for example `LLM_VISION_BASE_URL`, `LLM_VISION_MODEL`, `LLM_VISION_TIMEOUT_S`), with one fallback provider per task. Default plan: vision = RC `qwen36-35b-a3b` (tagged fast + vision), fallback Gemini via its OpenAI-compatible endpoint; intent and title matching = an RC fast general model, fallback Gemini. Reasoning models are not used on any user-facing path, because they took 3–7 minutes per call in earlier trials.

### Repository layout

```
<repo>/
├─ src/showwatch/
│  ├─ core/        settings.py (pydantic-settings), logging.py, db.py (async engine/session)
│  ├─ domain/      models.py (types, Chain Protocol, Capability, CapabilityNotSupported), title_match.py
│  ├─ db/          tables.py (SQLAlchemy), repositories/{users,watches,movies,signals,alerts}.py, queue.py
│  ├─ chains/      base.py, registry.py, politeness.py, harkins/{scraper,booker,seatmap}.py, bms/… (M5)
│  ├─ llm/         client.py, tasks/{seat_vision,intent,title_match}.py
│  ├─ notify.py    send Telegram messages/buttons (used by bot and worker)
│  ├─ bot/         app.py, keyboards.py, handlers/{access,watch,search,freetext}.py, __main__.py
│  └─ worker/      scheduler.py, runner.py, handlers/{check_chain,search_showtimes,book,probe_signals}.py, __main__.py
├─ migrations/     Alembic (alembic.ini at the repo root)
├─ tests/          conftest.py, unit/, integration/, fixtures/<chain>/
├─ scripts/        explore_site.py, parity_check.py, smoke_book.py, harkins_login.py
├─ docker/Dockerfile   targets: bot (python slim), worker (Playwright Python image)
├─ docs/           architecture.md, decisions/NNN-*.md, review/, procedure.md
├─ legacy/         the current bot, runnable until M4, then deleted
├─ compose.yaml, pyproject.toml, uv.lock, .env.example, .dockerignore, CLAUDE.md, README.md
```

No empty folders: `api/` and each Bangalore chain module are created only in the milestone that fills them.

### Stack

Python 3.12 (matches the Playwright Ubuntu image), `uv`, `pydantic-settings`, `python-telegram-bot` (async), SQLAlchemy 2 async + `asyncpg`, Alembic, Playwright (async API), `httpx`, `structlog`, `pytest` + `pytest-asyncio` + `pytest-cov`, `ruff`, `mypy`. Pinning rule: `pyproject.toml` pins exact versions chosen at M0 (the latest stable release of each on that day); the worker image tag uses the same Playwright version. No OpenAI client library and no Pillow.

## Detailed Implementation Plan

M5–M7 file tables are written at each milestone's re-plan checkpoint, after the site probe, and the user approves them before coding. This is the one planned exception to the exact-file-list rule.

Every other milestone lists its files as Path | Action (Create, Modify, Move, Delete) | Purpose, with repo-relative paths. Each milestone ends with a stop for the user's check.

- Run `ruff`, `mypy` and `pytest` locally before each commit. Commit only after the milestone's verification passes.
- Push only after the user confirms; CI runs after each confirmed push.
- `revamp` stays the long-lived branch. At the end of each milestone, open a PR to `main` after the user confirms (its description holds that milestone's results), merge it with a merge commit, then merge `main` back into `revamp`.

### Step 0 — Branch and plan

| Path | Action | Purpose |
|---|---|---|
| `docs/plan.md` | Create | Copy of this plan, committed on `revamp` (created from `restructure/icm-layout`) |
| `docs/review/*.md` | Create | The EGM Stage 9 reports (round 1, round 2, and both annotated reports) |

`docs/procedure.md` stays where it is. The user approves the plan before M0 starts.

### M0 — Scaffold

| Path | Action | Purpose |
|---|---|---|
| `bot.py`, `booker.py`, `scraper.py`, `seat_ai.py`, `tracker.py`, `add_watch.py`, `telegram.py`, `paths.py`, `requirements.txt`, `CONTEXT.md`, `data/`, `tools/explore.py`, `tools/explore_booking.py`, `tools/test_setup.py`, `tools/CONTEXT.md` | Move (`git mv`) to the same names under `legacy/` | Old bot stays runnable; `legacy/paths.py` anchors to its own folder |
| `legacy/requirements.txt` | Modify | Add `requests==2.34.2` (imported by `bot.py`, `seat_ai.py`, `telegram.py`, missing today) |
| `pyproject.toml`, `uv.lock` | Create | uv project, `src` layout; pytest `asyncio_mode = "auto"`, `testpaths = ["tests"]`; mypy strict on `src/`; ruff default rules plus `I`; `legacy/` excluded from ruff, mypy and coverage |
| `src/showwatch/__init__.py` and an `__init__.py` in each subpackage | Create | Package markers; `__version__` |
| `src/showwatch/core/settings.py` | Create | One `Settings` class (keys below) |
| `src/showwatch/core/logging.py` | Create | structlog JSON logging plus redaction |
| `src/showwatch/core/db.py` | Create | Async engine and session factory |
| `src/showwatch/core/lifecycle.py` | Create | Shared start-up for `bot` and `worker`: check the database, log `ready`, wait for SIGINT/SIGTERM (added during M0) |
| `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/.gitkeep` | Create | Alembic scaffold with no revisions, so the `migrate` service exits 0 (moved from M1 during M0) |
| `tests/unit/test_docker_pin.py` | Create | Fails if the Dockerfile's `PLAYWRIGHT_VERSION` differs from the playwright pin in `pyproject.toml` (added during M0) |
| `src/showwatch/domain/models.py` | Create | Domain types, `Capability`, the `Chain` Protocol, `CapabilityNotSupported` |
| `src/showwatch/bot/__main__.py`, `src/showwatch/worker/__main__.py` | Create | Start, log `ready`, handle shutdown signals (no features yet) |
| `docker/Dockerfile`, `.dockerignore` | Create | Two targets (below); ignore `.git`, `.venv`, `legacy`, `tests` |
| `compose.yaml` | Create | `postgres`, `migrate`, `bot`, `worker`, `login` profile, two volumes |
| `.github/workflows/ci.yml` | Create | CI steps (below) |
| `.env.example` | Create | Key names only |
| `.gitignore` | Modify | Keep existing entries; add `.venv/`, `legacy/.venv/`, `*.egg-info` |
| `CLAUDE.md`, `README.md` | Modify | Routing file for the new layout; "revamp in progress" note with how to run legacy and new |
| `docs/architecture.md` | Create | Architecture summary |
| `docs/decisions/NNN-<slug>.md` | Create | One short file per grilling question: `001-growth-path`, `002-bangalore-scope`, `003-language`, `004-repo-home`, `005-rebuild-approach`, `006-llm-layer`, `007-restructure-base`, `008-ownership`, `009-docker`, `010-database`, `011-postgres-container`, `012-process-split`, `013-layout`, `014-bangalore-order`, `015-insider-detection`, `016-access-control`, `017-testing`, `018-milestones` |
| `tests/conftest.py` | Create | Async DB fixture using `TEST_DATABASE_URL` |
| `tests/unit/test_smoke.py`, `tests/unit/test_settings.py`, `tests/unit/test_logging.py` | Create | Package imports; settings load from env; redaction cases |

**Legacy.** Create a separate venv for legacy (`legacy/.venv`, ignored) on Python 3.14, the version that produced the existing `__pycache__`. Copy any existing `seen_movies.json` and `browser_profile/` into `legacy/data/` by hand, because `git mv` skips ignored files. Create `legacy/.env` by hand with the three legacy keys (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`), copying values from the old root `.env` without printing them; the new root `.env` then holds only new-stack keys. Legacy runs only as scripts in its own venv; nothing imports `legacy.*`. The legacy venv must not install `python-telegram-bot`, because `legacy/telegram.py` would conflict with its `telegram` package.

**Settings keys** (`core/settings.py`, mirrored in `.env.example`):

| Name | Type | Default | Read by |
|---|---|---|---|
| `DATABASE_URL` | DSN, `postgresql+asyncpg://showwatch:<password>@127.0.0.1:5433/showwatch` in `.env` (127.0.0.1, not localhost: on Windows `localhost` tries IPv6 first and each connection waits about 2 s) | none (required) | bot, worker, migrate |
| `TEST_DATABASE_URL` | DSN | none | integration tests |
| `POSTGRES_PASSWORD` | secret | none | compose |
| `TELEGRAM_BOT_TOKEN` | secret | none; a different bot from legacy's (C2); required for `bot` | bot, `notify.py` |
| `ADMIN_TELEGRAM_USER_ID` | int | none; required for `bot` | bot |
| `LOG_LEVEL` | str | `INFO` | all |
| `CHECK_INTERVAL_S` / `FAST_INTERVAL_S` / `FAST_WINDOW_H` | int | 1800 / 60 / 72 | scheduler |
| `POLL_JITTER_PCT` | int | 20 | scheduler |
| `JOB_LOCK_TIMEOUT_S` | int | 900 | queue |
| `CHAIN_REQUEST_BUDGET_PER_MIN` | int | 10 | chains |
| `LLM_VISION_` + `BASE_URL`, `MODEL`, `API_KEY`, `TIMEOUT_S` (default 45), `COORD_ORDER` (`yx` or `xy`, default `yx`), `COORD_SCALE` (default 1000), and the same keys with a `FALLBACK_` infix | str / secret / int | none (optional until M4) | llm |
| `LLM_TEXT_*` (intent and title matching, same shape without the coordinate keys) | str / secret / int | none (optional until M6) | llm |

**Logging.** Set the `httpx` and `httpcore` loggers to WARNING. The redaction processor matches keys case-insensitively and masks values of keys that equal or end with `token`, `secret`, `password` or `api_key` (whole-name match, so `dedupe_key` is untouched), and masks any string matching `bot\d+:[A-Za-z0-9_-]+`. Tests in `tests/unit/test_logging.py`, including keys named `Telegram_Token` and `API_KEY`.

**Dockerfile.** Multi-stage, `uv sync --frozen --no-dev`, non-root user `app`, entrypoints `python -m showwatch.bot` and `python -m showwatch.worker`. `bot` target on `python:3.12-slim`. `worker` target on `mcr.microsoft.com/playwright/python:v<pinned Playwright version>-noble`, starts Xvfb (`DISPLAY=:99`) and then execs python, so a headed Chromium works and SIGTERM reaches the worker (`xvfb-run` sits between the two and swallows it), with `x11vnc` and noVNC installed for the `login` profile.

**Compose.**
- `postgres`: `postgres:18-alpine`, `POSTGRES_USER=showwatch`, `POSTGRES_DB=showwatch`, `POSTGRES_PASSWORD` from `.env`, healthcheck `pg_isready -U showwatch -d showwatch`, host port 5433, volume `showwatch_pgdata` mounted at `/var/lib/postgresql` (the path Postgres 18 images expect).
- `migrate`: one-shot, bot image, command `alembic upgrade head`, waits for a healthy `postgres`.
- `bot` and `worker`: `env_file: .env`, `environment: DATABASE_URL: postgresql+asyncpg://showwatch:${POSTGRES_PASSWORD}@postgres:5432/showwatch` (overrides the host-side value), `depends_on: migrate: condition: service_completed_successfully`; `worker` mounts `showwatch_profiles`.
- service `login` (compose profile `login`, so it never starts with `up`): runs Xvfb, `x11vnc` and noVNC published on `127.0.0.1:6080` only, then `scripts/harkins_login.py`. Defined in M0, first run in M4 (the script and the Dockerfile `COPY scripts` line arrive then).

**CI.** `uv sync` → `ruff` → `mypy` → `uv run playwright install --with-deps chromium` → `pytest --cov` (a Postgres service container supplies `TEST_DATABASE_URL`; tests that need a browser carry a `browser` marker) → `docker build` of both targets without pushing. The coverage gate `--cov-fail-under=85` starts at M1 and measures the modules listed in `[tool.coverage.run]`; each milestone adds its own (M1: `domain/`, `db/`; M2: `bot/handlers/access.py`, `bot/handlers/watch.py`; M3: `worker/scheduler.py`, `chains/politeness.py`, `bot/handlers/search.py`; M4: `chains/harkins/seatmap.py`, `llm/`).

Done when:
- `docker compose up` shows `postgres` healthy, `migrate` exited 0, and `bot` and `worker` running and each having logged `ready`.
- The `worker` container launches headed Chromium on its Xvfb display and prints a page title (`docker compose exec worker python -c "…"`), and `docker compose stop` makes both `bot` and `worker` log `stopping`.
- CI is green on the branch after a confirmed push.
- `python -m compileall -q legacy` passes in the legacy venv (it runs no code).

### M1 — Data layer

| Path | Action | Purpose |
|---|---|---|
| `src/showwatch/db/tables.py` | Create | SQLAlchemy tables per Appendix A |
| `migrations/env.py` | Modify | Set `target_metadata` to the tables' metadata (the Alembic scaffold itself exists from M0) |
| `migrations/versions/0001_initial.py` | Create | Schema and seed rows |
| `src/showwatch/db/repositories/users.py`, `watches.py`, `movies.py`, `signals.py`, `alerts.py` | Create | The only code that writes SQL |
| `src/showwatch/db/repositories/catalog.py` | Create | Read-only lookups for cities, chains and theatres (added during M1; M2's `/city` and M3's scheduler need them) |
| `src/showwatch/domain/title_match.py`, `src/showwatch/domain/models.py` | Create / Modify | `normalize_title` (needed by `movies.upsert_movie`; M2 adds `find_matches` to the same file) and the `Job`, `ChainInfo`, `ChainSnapshot` row models |
| `tests/dbtools.py`, `tests/unit/test_queue_rules.py`, `tests/unit/test_title_normalize.py` | Create | Test helpers (create a database, run Alembic); catalogue and title rules that need no database |
| `src/showwatch/db/queue.py` | Create | `enqueue`, `claim(worker_id)` with `FOR UPDATE SKIP LOCKED` and the skip predicate (Appendix B), `complete`, `fail` with back-off, `requeue(delay)` (no attempt counted), `sweep()` |
| `tests/integration/test_migrations.py`, `test_queue.py`, `test_repositories.py` | Create | Cases below |

Tests: the migration applies on an empty database and the seed rows exist; `claim` respects `run_after`; 2 workers making 50 concurrent claim attempts never get the same job; a second `book` job for `harkins` is not claimed while one is `running`, while a `book` job for another chain is; `sweep()` sets `running` jobs with `locked_at` older than `JOB_LOCK_TIMEOUT_S` back to `queued`; `fail` retries with back-off and stops at `max_attempts`.

Done when: all integration tests pass against Postgres, including "no duplicate claims across 50 concurrent claim attempts by 2 workers".

### M2 — Bot shell

| Path | Action | Purpose |
|---|---|---|
| `src/showwatch/bot/app.py` | Create | python-telegram-bot `Application`, long polling, status filter |
| `src/showwatch/bot/keyboards.py` | Create | Inline-button builders (callback data carries record IDs) |
| `src/showwatch/bot/handlers/access.py` | Create | `/start`, admin approve/deny callbacks |
| `src/showwatch/bot/handlers/watch.py` | Create | `/watch`, `/list`, `/unwatch`, `/city` |
| `src/showwatch/notify.py` | Create | Send messages and buttons; used by bot and worker |
| `src/showwatch/domain/title_match.py` | Modify | Add the port of `legacy/add_watch.find_matches` (the file exists since M1) |
| `src/showwatch/bot/handlers/__init__.py` | Create | `Sender` and `Env` (database session factory, notifier, admin id), shared by the handler modules (added during M2) |
| `src/showwatch/bot/__main__.py` | Modify | Runs `app.run_polling`; logs a crash through structlog so the token is redacted (the bot no longer uses `core/lifecycle.py`) |
| `tests/unit/test_title_match.py`, `tests/unit/test_keyboards.py`, `tests/unit/test_notify.py` | Create | Matcher, button data and notifier (no database) |
| `tests/integration/test_access.py`, `test_watch_handlers.py`, `test_bot_app.py` | Create | Handler tests need Postgres, so they live under `integration/` (moved from `unit/`). `tests/telegramfakes.py` holds the fake notifier, a fake python-telegram-bot `Bot` and update builders; `test_bot_app.py` runs real PTB routing end to end |

**Title matching.** `find_matches(query: str, titles: list[str]) -> list[str]` is a pure function; the repository supplies the titles of `movies` rows for the user's city (both statuses). It returns lowercase substring matches; only if there are none, `difflib.get_close_matches(n=5, cutoff=0.3)`; at most 5 results.

**Handler spec.**

| User status | Behaviour |
|---|---|
| unknown (any message, treated as `/start`) | Create a `pending` row; send the admin an Approve/Deny message (`approve:<user_id>`, `deny:<user_id>`, admin only) |
| `pending` | One reply, "Access requested; waiting for approval" (sets `users.pending_notified`). Silence after |
| `denied` | Silence |
| `approved` | Full access. A repeated `/start` replies "You're set up." |

On startup the bot upserts the admin: `telegram_user_id = ADMIN_TELEGRAM_USER_ID`, `status = approved`, `is_admin = true`, `chat_id` null until the admin sends `/start`. `bot/app.py` registers a `TypeHandler` in group -1 that raises `ApplicationHandlerStop` unless the sender is `approved`, or the update is `/start` or a first message from an unknown user. `users.chat_id` is set from the first `/start` update.

| Command | Inputs | Database effect | Reply |
|---|---|---|---|
| `/watch <title>` | free text | Matches with `find_matches`; shows up to 5 buttons `watchpick:<movie_id>`. Tap on a `coming_soon` title saves a `watch_items` row; tap on a `now_showing` title offers dates (M3 `pick:` flow) and saves nothing (C10) | "Watching <title>" |
| `/list` | none | none | The caller's rows, one line each |
| `/unwatch` | none | Buttons `unwatch:<watch_item_id>`; the tap deletes the row | "Removed <title>" |
| `/city` | none | Buttons `city:<code>`; the tap sets `users.current_city_id` | "City set to <name>" |

**Added during M2 (not in the first plan):** `/watch` with no listed match saves a query-only watch (`movie_id` null, `stage waiting`) and says so, so a title can be watched before any chain lists it (the insider case); `/list` and `/unwatch` scope every id to the caller; any other text or an unknown command gets a one-line help message; updates from groups and channels are ignored. When listed titles are offered, a last button "None of these" (`watchfor:<typed words>`, left out if the words exceed 64 bytes of callback data) saves the query-only watch instead, because close spellings are not always the right film. The gate fails closed: if it raises, the update is dropped (python-telegram-bot would otherwise carry on to the next handler). The admin is asked about every pending request again at start-up and whenever the admin sends `/start`, because the first request can be lost (the admin cannot be messaged until they have started the bot). The admin cannot change their own access; `/list` and `/unwatch` show at most 30 watches. Without a real `TELEGRAM_BOT_TOKEN` the bot container exits (Telegram rejects the token) and Compose keeps restarting it.

Tests (handlers use a fake Telegram bot and real repositories on Postgres): a callback from an unapproved user is dropped; admin approval flips `pending` to `approved`; `/list` shows only the caller's rows; `find_matches` returns substring hits alone when any exist, fuzzy hits only when there are none, and at most 5 results.

Done when: using the new bot (its own token, C2) while the legacy bot is also running, the admin approves a second Telegram account; each account sees only its own watchlist; updates from a pending or denied account get no reply beyond the one `pending` message; the handler tests pass.

### M3 — Harkins watching

| Path | Action | Purpose |
|---|---|---|
| `src/showwatch/chains/base.py`, `registry.py` | Create | Chain lookup by code; capability checks |
| `src/showwatch/chains/politeness.py` | Create | Request budget and 403/429 back-off for all chains |
| `src/showwatch/chains/harkins/scraper.py` | Create | Async port of `legacy/scraper.py`: `get_now_playing`, `get_coming_soon`, `get_showtimes`, `FORMAT_JS`, plus parse-only `parse_movie_list(html)` and `parse_showtimes(html)` |
| `src/showwatch/worker/scheduler.py`, `runner.py` | Create | Scheduler spec below; claims jobs and dispatches |
| `src/showwatch/worker/handlers/check_chain.py`, `search_showtimes.py` | Create | Scrape, snapshot guard, store, alert; showtime lookup |
| `src/showwatch/bot/handlers/search.py` | Create | `pick:`, `date:`, `fmt:` callbacks and a stub `book:` handler replying "Booking arrives in a later update." |
| `scripts/explore_site.py` | Create | Probe a site; `--save-html` saves pages as fixtures |
| `scripts/parity_check.py` | Create | The M3 parity test |
| `tests/fixtures/harkins/now_showing.html`, `coming_soon.html`, `movie_showtimes.html` | Create | Saved live pages, captured with `scripts/explore_site.py --save-html` |
| `tests/unit/test_harkins_parse.py`, `test_scheduler.py`, `test_politeness.py`, `test_search_handlers.py`, `tests/integration/test_check_chain.py` | Create | Cases below |

**Parsing rules carried over from legacy.** Skip links with no `aria-label`; exclude the `/movies/coming-soon` link from the movie-link selector; a title on both pages stays `now_showing`; skip showtimes whose link class contains `disabled`; format defaults to `Standard`; keep only links under `/ticketing/theatre/16/`; keep the `FORMAT_JS` fallback chain (`img[alt]`, then `h3/h4`, then `data-testid`). `FORMAT_JS` depends on the generated class `sc-d9066650-9`, so its fixture test (loads the saved HTML with `page.set_content`) must fail loudly if it stops matching. The fixed 5000 ms wait is replaced by waiting up to 15 s for the link selector (intentional change). The `{movie_url}/{YYYY-MM-DD}` URL is built in the search handler, not the scraper.

**Mapping to the schema.**

| Column | Derivation |
|---|---|
| `movies.external_id` | Last path segment of the movie URL (`/movies/<slug>`) |
| `movies.status` | `now_showing` if the title came from `/movies`, `coming_soon` if from `/movies/coming-soon` |
| `showtimes.external_session_id` | The number after `/session/` in the booking URL (example: `…/movie/HO00015450/session/568953/date/2026-07-23`) |
| `showtimes.starts_at` | The page's date plus the time text, read in `America/Phoenix`, stored as UTC |
| theatre | `list_showtimes` takes the theatre of the user's city (Arizona Mills, external_id 16, is a `theatres` row) |
| `alerts.dedupe_key` | `new_release:<movie_id>` or `watch_open:<watch_item_id>:<stage>` |

**Snapshot and alert rules.** A scrape is suspect when it returns zero movies or fewer than 50% of the last `good` `chain_snapshots.movie_count`; no earlier `good` snapshot means first run (store, no alerts). A suspect scrape writes a snapshot row only; it never upserts `movies` and never alerts. A "new release" is a `movies` row inserted with `status = now_showing`, or a status change `coming_soon` → `now_showing` (a title that was already `now_showing` and reappears is not new). The audience of an alert is the approved users whose `current_city_id` equals the movie's `city_id`.

**Scheduler spec.** Every 60 s tick the scheduler calls `sweep()`. For each enabled chain × city with a `theatres` row, it enqueues `check_chain` if no `queued` or `running` job exists for the pair and the last finished `check_chain` for it is older than `CHECK_INTERVAL_S` × (1 ± jitter); due time comes from the `jobs` table, so it survives restarts, and jitter is applied to `run_after`. For each watch with `stage` in `listed|dates|venues` and `fast_until > now`, it enqueues `probe_signals` the same way at `FAST_INTERVAL_S`. A swept `book` job (1 attempt) becomes `failed` with `last_error = "worker crashed"` and the user gets the "Booking failed" message. Stage transitions: `waiting → listed → dates → venues → sessions → done`; `fast_until = now + FAST_WINDOW_H` when the stage first reaches `listed`; `movie_id` is matched on each `check_chain` for unmatched watches using `find_matches`. Harkins has no SIGNALS: a `coming_soon` title moves the watch to `listed`; `now_showing` moves it to `sessions` (alert), then `done`. `chains/politeness.py` keeps an in-memory token bucket per chain (`CHAIN_REQUEST_BUDGET_PER_MIN`) and persists back-off in `chains.blocked_until`: after a 403, 429 or challenge page, back off 5 minutes, doubling to a 60-minute cap, reset after one clean response.

**Search flow.**
1. A watched title becoming Now Showing sends an alert with a "Book seats" button, callback `pick:<movie_id>`; the same callback follows a `/watch` match for a title that is already playing.
2. `pick:` sends 7 date buttons, `date:<movie_id>:<YYYY-MM-DD>`, starting from today's date in the city's time zone.
3. `date:` enqueues a `search_showtimes` job and replies "Looking up showtimes…".
4. The handler calls `chain.list_showtimes`, stores rows in `showtimes`, then sends format buttons `fmt:<movie_id>:<day>:<n>` (only when more than one format exists; `n` is the position in the alphabetical list of distinct formats for that movie and day, re-read on the tap) or time buttons `book:<showtime_id>` labelled with the showtime text. An empty result replies "No showtimes at Arizona Mills that day."

Tests: the fixture parse returns the expected titles, times and formats; a suspect snapshot sends no alert; a duplicate alert is suppressed by `dedupe_key`; the first check sends no alerts; jitter stays within ±20%; every callback string built by `keyboards.py` is at most 64 bytes; the request budget blocks the 11th request in a minute; a 429 sets `blocked_until`.

Done when (parity test):
1. Copy `legacy/` to a temp folder, so `legacy/data` is untouched.
2. Parse `now_showing.html` into `[{"title","url"}]` dicts and pass them to the temp copy's `tracker.find_new_movies` and `find_watchlisted_now_showing`, with a seen-set missing 2 titles and a watchlist holding 1 of them.
3. Run the new `check_chain` on the same parsed list with the same seed (a seeded `movies` table, one user, one watch item), capturing alerts through a fake `notify`.
4. Both sides produce 2 new-release alerts and 1 watch alert, matched by type plus title (`scripts/parity_check.py` prints the comparison).
5. A scrape with zero movies, once a `good` snapshot exists, is marked suspect and sends no alerts.

### M4 — Harkins booking and LLM vision

1. **Pre-checks, before any porting.** (a) Does a Harkins seat hold need a logged-in profile? If yes, run `docker compose --profile login run --service-ports login`, open `http://127.0.0.1:6080` in the browser, log in to Harkins, and press Enter in the terminal. The script saves the session in `/profiles/harkins` on `showwatch_profiles` and writes `/profiles/harkins/.login_ok` with the time (a Windows Chromium profile cannot be reused on Linux). If no login is needed, note it in `docs/decisions/`. (b) Is the RC gateway reachable from inside a container off the ASU network?
2. **Browser handling.** One browser context per job, closed in a `finally` block. Each chain's persistent profile lives at `/profiles/<chain code>` on the `showwatch_profiles` volume. If launching the context fails because the profile is in use, the handler calls `requeue(delay=60 s)`, which does not count as an attempt (expected only after a crashed job left the profile locked); any other failure ends the job as `failed`.

| Path | Action | Purpose |
|---|---|---|
| `src/showwatch/chains/harkins/seatmap.py` | Create | Port of `_split_regions`, `_region_pairs`, `candidate_pairs`, behaviour unchanged |
| `src/showwatch/chains/harkins/booker.py` | Create | Async port of `book_session`, `_select_deterministic`, `_finalize`, `_select_seat`, `_clear_selection`; `NUM_SEATS = 2`; drop the unused `send_telegram` import |
| `src/showwatch/llm/client.py` | Create | OpenAI-compatible client with per-task endpoints and one fallback |
| `src/showwatch/llm/tasks/seat_vision.py` | Create | Port of `seat_ai.pick_seats_by_vision` |
| `src/showwatch/worker/handlers/book.py` | Create | `book` job handler |
| `src/showwatch/bot/handlers/search.py` | Modify | Replace the stub with the `book:<showtime_id>` callback; enqueue a `book` job and reply "Booking the best seats for you… hang on." |
| `scripts/smoke_book.py`, `scripts/harkins_login.py` | Create | Manual live checks; `smoke_book.py --showtime-url <url>` prints the outcome dict (`held` or `manual`) and never pays |
| `tests/unit/test_seatmap.py`, `test_seat_vision.py`, `test_llm_client.py`, `test_book_handler.py` | Create | Cases below |
| `tests/fixtures/harkins/seatmap_1.png`, `seatmap_2.png`, `seatmap_3.png` | Create | Calibration for vision coordinates |
| `README.md` | Modify | IMAX results in the Results table |
| `bot.py`, `booker.py`, `scraper.py`, `seat_ai.py`, `tracker.py`, `add_watch.py`, `telegram.py`, `paths.py`, `requirements.txt`, `CONTEXT.md`, `data/`, `tools/` (all under `legacy/`) | Delete | After the done-when checks pass |

3. **Seat count.** `NUM_SEATS = 2` is a constant in `booker.py` (the legacy name); the `book` job payload carries `seats`, and the handler rejects any value other than 2.
4. **Seat-map tests** (`tests/unit/test_seatmap.py`, made-up seat lists): `_split_regions` returns one region when fewer than 3 rows exist; a row gap above 1.6 × the median gap splits back region first, then front; `_region_pairs` keeps only consecutive same-row seats, nearest the centre first; `candidate_pairs` lists back-region pairs before front-region pairs; a gap in seat numbers (A3, A5) produces no pair.
5. **Constants carried over from `legacy/booker.py`:**

| Item | Value |
|---|---|
| Pairs tried | first 8 from `candidate_pairs`; abort the deterministic path after 2 failed seat clicks |
| Seat id regex | `([A-Z]+)(\d+)` |
| Selectors | `button[aria-label^="Select Seat"]:not([disabled])`, `aria-pressed`, `[data-testid^="ticketing-type-picker-add-"]`, `button:has-text("Continue to Checkout")` |
| Waits and counts | 60 s seat-map wait, 3000 ms hydration, 30 s `wait_for_url("**/session/*/ticketing/*")`, 15 × 200 ms checkout polling, 3 click retries, 8 iterations in `_clear_selection` |
| Fallback | after a deterministic failure, reload the page before vision; a failed reload returns the manual outcome |

6. **Vision contract.** Request: OpenAI-style chat with the canvas PNG as an `image_url` data URL and the legacy `_PROMPT` text. The model text is read from `choices[0].message.content` and parsed with the legacy regex for a JSON array of `[y, x]` points, 0–1000, origin top-left. The 20-point cap lives only in the prompt text, as in legacy. If a model returns another convention, `LLM_VISION_COORD_ORDER` and `LLM_VISION_COORD_SCALE` (and their `FALLBACK_` forms) convert to `[y, x]` 0–1000. Kept: 2 attempts with sleeps of 4 s and 2 s, the `Remove Seat` aria-label, `canvas[aria-label="Theatre seat map"]`. The 45 s `LLM_VISION_TIMEOUT_S` applies to each provider and is a change from legacy's hardcoded 60 s.
   - **Calibration:** run the 3 saved screenshots through the primary model; change the order and scale values only if the converted points of a response do not land on seat icons. Acceptance: for each screenshot, at least 7 of the first 10 points land on a seat icon when overlaid on the image (the user checks the overlay).
7. **Booking outcomes** for `book` jobs (`max_attempts = 1`):

| Outcome | Result | Message |
|---|---|---|
| held | `{seats, url}` | "Seats X + Y held! Pay here: <url>" |
| manual | `{manual, url}` | "Couldn't auto-pick seats; choose 2 yourself here: <url>" |
| failed | any exception | "Booking failed — try again" (error logged) |

Two intentional changes from legacy: any exception becomes the `failed` outcome (legacy let it escape the polling loop, and its `result is None` check never fires); the legacy "hang on" message is sent when the job is enqueued.

Tests: calling `book()` on a chain without `BOOK` raises `CapabilityNotSupported`; the handler rejects seats other than 2; each outcome sends its message; `test_seat_vision.py` covers the regex parse of a `[[y, x], …]` array, `xy` → `yx` and scale conversion, and 2 attempts then the fallback provider; `test_llm_client.py` covers routing by task, timeout, and fallback on error.

Done when:
- 5 of 5 live standard-screen holds succeed, each stopping at the payment page with no payment.
- 3 live IMAX attempts through RC vision are recorded in the README Results table. No pass mark: the result decides whether IMAX stays on the vision path or is listed as manual-link only (baseline: 0 of 3 in the original benchmark); a failed hold falls back to the manual link.
- `legacy/` is deleted after these checks pass.

### M5 — BookMyShow watch-only with insider detection

1. **Probe** (`scripts/explore_site.py`, at most 1 hour): which BMS pages and public JSON calls are reachable from the PC without login. Plain access means access without login, CAPTCHA or a challenge page; probe rate is at most 10 requests per minute for 10 minutes. Findings go in `docs/decisions/019-bms-probe.md`. If BMS blocks plain access, stop and report rather than add evasion.
2. **Re-plan checkpoint.** Add this milestone's file table (chain module files, stage-detection rules, fixture names) to `docs/plan.md`; the user approves it before coding.
3. **Build** `src/showwatch/chains/bms/` (WATCH | SHOWTIMES | SIGNALS for Bangalore) with fixtures from the probe, and `src/showwatch/worker/handlers/probe_signals.py`. Polling limits come from `chains/politeness.py` (M3).

Tests: the interval drops from base to fast when `stage` reaches `listed`; each stage alerts once per user; a 429 triggers back-off.

Done when:
- Stage alerts arrive in order, with no duplicates, for a real Bangalore opening (or a movie that moves from `listed` to `sessions` during the test window).
- No back-off is triggered during a 2-hour run at the 30-minute base interval plus one 10-minute run at the 1-minute fast interval.
- Fallback: if no real opening occurs within 7 days of coding, replay a recorded fixture sequence (listed → dates → venues → sessions pages) through the scheduler; alerts arrive in that order, once each.

### M6 — Plain-language requests and title matching

| Path | Action | Purpose |
|---|---|---|
| `migrations/versions/0002_parsed_requests.py` | Create | Table `parsed_requests` (Appendix A) |
| `src/showwatch/bot/handlers/freetext.py` | Create | Free-text messages that are not commands |
| `src/showwatch/llm/tasks/intent.py` | Create | Free text → `ParsedRequest` |
| `src/showwatch/llm/tasks/title_match.py`, `src/showwatch/domain/title_match.py` | Create / Modify | LLM matching across languages and dubbed or subtitled variants; the `difflib` path stays as the offline fallback |
| `tests/unit/test_intent.py`, `tests/unit/test_llm_title_match.py` | Create | Fake LLM client with recorded replies |

1. `ParsedRequest` (pydantic): `title: str`, `city: str | None`, `date_from: date | None`, `date_to: date | None`, `part_of_day: Literal["morning","afternoon","evening","night"] | None`, `format: str | None`, `seats: int = 2`, `confidence: float`. Part-of-day windows (local time): morning 06–12, afternoon 12–17, evening 17–21; "night" matches 21:00–23:59 on the chosen day and 00:00–02:00 of the next day.
2. If `confidence < 0.6`, or the LLM errors or times out, the bot replies "I didn't catch that" and shows the `/watch` flow. Otherwise it shows the parsed request with a confirm button, `intent:<request_id>`, backed by a `parsed_requests` row (1-hour expiry).
3. Confirming creates one `search_showtimes` job per day from `date_from` to `date_to` (at most 7); `part_of_day` and `format` filter the showtimes shown.

Done when:
- The parse test passes on 5 recorded phrases ("Dune IMAX Friday evening, 2 seats", two other English phrases, one Hindi, one Kannada).
- The live confirm-then-book step shows a booking offer for a Harkins movie that has showtimes that day.
- A Kannada title variant and a Hindi title variant each resolve to the right BMS movie in tests.

### M7 — District and PVR INOX

Same pattern as M5 per site: probe → decision file (`docs/decisions/020-district-probe.md`, `docs/decisions/021-pvrinox-probe.md`) → re-plan checkpoint → chain module with fixtures → live signal test.

Done when: same as M5, per site.

## Appendix A — Schema

Every table: `id bigint identity primary key`, `created_at timestamptz not null default now()`, `updated_at timestamptz not null default now()`. Foreign keys are `not null` unless stated. Text columns are `text`; timestamps are `timestamptz`.

| Table | Columns (beyond the common three) | Constraints |
|---|---|---|
| `users` | `telegram_user_id bigint`, `chat_id bigint null`, `display_name text null`, `status text default 'pending'`, `is_admin bool default false`, `pending_notified bool default false`, `current_city_id` FK `cities` null | unique `telegram_user_id`; `status` in `pending|approved|denied` |
| `cities` | `code`, `name`, `tz` | unique `code` |
| `chains` | `code`, `enabled bool default false`, `blocked_until null` | unique `code` |
| `theatres` | `chain_id`, `city_id`, `external_id`, `name` | unique `(chain_id, external_id)` |
| `movies` | `chain_id`, `city_id`, `external_id`, `title`, `normalized_title`, `url`, `status`, `first_seen_at default now()` | unique `(chain_id, city_id, external_id)`; `status` in `now_showing|coming_soon`; `normalized_title` = `casefold()`, punctuation removed, whitespace collapsed |
| `showtimes` | `movie_id`, `theatre_id`, `external_session_id`, `starts_at`, `format default 'Standard'`, `booking_url`, `first_seen_at default now()` | unique `(theatre_id, external_session_id)` |
| `watch_items` | `user_id`, `city_id`, `query`, `movie_id` null, `stage default 'waiting'`, `fast_until null`, `format_pref null` | unique `(user_id, city_id, query)`; `stage` in `waiting|listed|dates|venues|sessions|done` |
| `signals` | `watch_item_id`, `stage`, `evidence jsonb`, `seen_at default now()` | unique `(watch_item_id, stage)` |
| `alerts` | `user_id`, `dedupe_key`, `sent_at default now()` | unique `(user_id, dedupe_key)` |
| `jobs` | `kind`, `payload jsonb`, `status default 'queued'`, `run_after default now()`, `attempts int default 0`, `max_attempts int`, `chain_id null`, `city_id null`, `locked_by null`, `locked_at null`, `last_error null`, `result jsonb null` | index `(status, run_after)`; unique partial index `uq_jobs_running_limit` on `(kind, chain_id, coalesce(city_id, 0))` where `status = 'running'` and `kind` is `check_chain`, `book` or `probe_signals` (the database-level guard behind the concurrency limits; added during M1); `status` in `queued|running|done|failed` |
| `chain_snapshots` | `chain_id`, `city_id`, `taken_at default now()`, `movie_count int`, `status` | `status` in `good|suspect` |
| `parsed_requests` (M6, migration `0002`) | `user_id`, `payload jsonb`, `expires_at` | none |

Also in `0001`: `signals.stage` is limited to `listed|dates|venues|sessions`; foreign keys delete as follows: `watch_items.user_id`, `alerts.user_id` and `signals.watch_item_id` cascade, `showtimes.movie_id` cascades, and `watch_items.movie_id` is set to null when its movie is deleted. Tests in `tests/integration/test_constraints.py` check these and every CHECK list.

Seed rows in `0001`: cities `phx` (`America/Phoenix`) and `blr` (`Asia/Kolkata`); chains `harkins`, `bms`, `district`, `pvrinox` (only `harkins` enabled); theatre `harkins` / `phx` / external_id `16` / "Arizona Mills".

## Appendix B — Job catalogue

| Kind | Payload | Handler | Max attempts | Retry back-off | Concurrency limit |
|---|---|---|---|---|---|
| `check_chain` | `{chain, city}` | `worker/handlers/check_chain.py` | 3 | 60 s × 2^(attempts − 1) | one per chain + city |
| `search_showtimes` | `{user_id, movie_id, day}` | `worker/handlers/search_showtimes.py` | 2 | 30 s × 2^(attempts − 1) | none |
| `book` | `{user_id, showtime_id, seats}` | `worker/handlers/book.py` | 1 | none (a locked-profile `requeue` does not count as an attempt) | one per chain |
| `probe_signals` | `{watch_item_id}` | `worker/handlers/probe_signals.py` | 3 | 60 s × 2^(attempts − 1) | one per chain |

- `enqueue()` fills `jobs.chain_id`, `jobs.city_id` (where the payload names them) and `max_attempts` from this table; `attempts` counts failed runs.
- Skip predicate in `claim()`: skip a queued job when another `running` job has the same `kind`, the same `chain_id` and, for `check_chain` only, the same `city_id`. If two workers race past that check, the unique index `uq_jobs_running_limit` (Appendix A) rejects the second one and `claim()` moves on to the next job.
- `sweep()` counts a swept job as a failed attempt and re-queues it after the same back-off as `fail()` (so a crash loop does not retry at once); a `running` job with no lock time is swept too. `claim()` treats only the `uq_jobs_running_limit` violation as a lost race; any other database error propagates.
- `complete`, `fail` and `requeue` take the worker id and raise `JobNotOwnedError` unless the job is `running` under that worker, so a worker that was swept and replaced cannot overwrite the new run. A handler that gets this error stops without touching the job.
- `enqueue(session, kind, payload, *, chain_id=None, city_id=None, run_after=None)` takes the chain and city ids as arguments (the caller knows them); `check_chain` needs both, `book` and `probe_signals` need a chain and no city, `search_showtimes` needs neither.
- `locked_by` is `<container hostname>:<pid>`; the lock timeout is `JOB_LOCK_TIMEOUT_S` (900 s). `jobs.result` stores the `book` outcome (`held`, `manual` or `failed` plus fields); other kinds leave it null.

## Execution method

- The code for each milestone is written in the main session, from that milestone's file table, one milestone at a time.
- `/rc-helpers` (read-only use): `review` on each milestone's diff with glm-5-3 and deepseek-v4-1-flash in the background, and `digest` on saved HTML fixtures. Only public-site HTML and project code are sent, never `.env` files. Helper findings are leads: verify the top 3 against the code before acting, and trust a finding more when 2 models agree.
- `/fanout`: only for M5 and M7 site research (what District and PVR INOX expose publicly) while the BMS probe runs. The probes themselves run from the user's PC.
- A fresh-context QA agent reads each milestone's diff before the user's check.
- No parallel code-writing sub-agents unless the user asks for them.

## Verification (applies throughout)

- Each milestone: its "Done when" checks, run live where stated; results go in the milestone's PR description.
- Live smoke scripts (`scripts/smoke_book.py`) are run manually, never in CI, to avoid hitting live sites on every push.
- A separate review pass in a fresh session (the EGM review or a fresh subagent) reads each milestone's diff before the user's check.

## Risks

| Risk | Mitigation |
|---|---|
| BookMyShow blocks the home IP | Request budget, jitter and back-off (`chains/politeness.py`); probe first; stop rather than evade |
| Harkins needs a login inside the container | Checked in M4 step 1; noVNC login profile (C8) |
| RC gateway unreachable off-campus or rate-limited | Per-task fallback to Gemini; RC reachability checked in M4 step 1 |
| Harkins or BMS markup changes | Fixture tests; each break becomes a saved fixture and a failing test before the fix |
| Scope growth toward phase C before parity | `api/` not created until after M7 unless the user reprioritises |
