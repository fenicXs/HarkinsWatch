# Goldfish Annotated Review Report

Status: resolved (auto accept applied; 42 fixes accepted, 0 contested, 0 escalated, 2 declined, 0 unaddressed). One placement change: the "new bot answers `/start` while legacy runs" check from Issue 13 sits in the M2 done-when, because the M0 bot has no handlers yet.

**Target:** `docs/plan.md` (lines 134-229, "Detailed Implementation Plan" to end of file)

**Source report:** `swirling-kindling-whale-agent-aed275d627f1c6535.md` (Stage 9, round 1). Finding IDs below match it, and the full discrepancy text is there.

**Intended location after plan mode:** `HarkinsWatch/docs/review/goldfish_review_report.md`. Plan mode only allows writes under the plans folder, so this report sits beside the source report for now.

## Tally

| Category | Count |
| --- | --- |
| PROPOSE FIX | 42 |
| DECLINE (nits) | 2 |
| CONTEST | 0 |
| ESCALATE | 0 |

I wrote the plan under review, so I tested each finding against the code and the plan text before categorising. None of them rest on a misreading. The 7 Blockers, 22 Majors and 13 of the 15 Nits are all valid.

## Choices the fixes make for you (overrule any of these)

| # | Choice made in the proposed edits | Finding |
| --- | --- | --- |
| C1 | Seat count is fixed at 2 for M4; the bot rejects other counts. An N-seat picker is future work. | 1-4 |
| C2 | The new stack uses its own Telegram bot token (you create a second bot in BotFather), so legacy and new can poll side by side. | 1-10 |
| C3 | `testcontainers` is dropped. Tests use `TEST_DATABASE_URL`: the CI service container in CI, and the Compose Postgres on port 5433 locally. | 1-8 |
| C4 | "Healthy" means Postgres healthy plus `bot` and `worker` running and logged "ready". No extra healthcheck code. | 1-9 |
| C5 | The IMAX step records results only. No pass mark. A failed IMAX hold keeps the manual-link fallback. | 1-14 |
| C6 | A scrape is "suspect" when it returns fewer than 50% of the last good snapshot's movie count. | 1-12 |
| C7 | M5's "normal rates" test window is 2 hours at the 30-minute base interval, plus one fast-poll window of 10 minutes at 1 minute. | style-1-2 |

## Prioritized Findings & AI Suggestions

### [Issue 1] [`implementation-readiness`] - Severity: Blocker (implementation-readiness-1-1)
*   **Affected Scope:** Lines 161-171 (depends on 86-98) (M1)
*   **Discrepancy Description:** Schema has only key columns; no types, constraints, status values, seeds, or a place for the user's current city.
*   **Rationale:** An implementer would have to design the schema.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. The table at lines 86-98 lists columns only.
*   ***Proposed Edit:***
    *   Location: M1, after "Files:" list (add new "Appendix A: Schema" section at the end of the plan)
    *   Change: Add DDL-level spec. Every table gets `id bigint identity primary key`, `created_at timestamptz not null default now()`, `updated_at timestamptz not null default now()`. Foreign keys are `not null` unless stated. Unique constraints: `users(telegram_user_id)`, `chains(code)`, `cities(code)`, `theatres(chain_id, external_id)`, `movies(chain_id, external_id)`, `showtimes(theatre_id, external_session_id)`, `alerts(user_id, dedupe_key)`, `signals(watch_item_id, stage)`. Index: `jobs(status, run_after)`. Allowed values (Postgres `CHECK`): `users.status` in `pending|approved|denied`; `jobs.status` in `queued|running|done|failed`; `movies.status` in `now_showing|coming_soon`; `watch_items.stage` in `waiting|listed|dates|venues|sessions|done`; `showtimes.format` is free text, defaulting to `Standard`. Add `users.current_city_id` (FK to `cities`, nullable until `/city` is run). Seed rows in the first migration: cities `phx` (`America/Phoenix`) and `blr` (`Asia/Kolkata`); chains `harkins`, `bms`, `district`, `pvrinox` (only `harkins` enabled); theatre `harkins` / `phx` / external_id `16` / name "Arizona Mills".

### [Issue 2] [`implementation-readiness`] - Severity: Blocker (implementation-readiness-1-2)
*   **Affected Scope:** Lines 163, 180, 189 (depends on 56)
*   **Discrepancy Description:** Job kinds, payloads, retry limits, lock timeout, and the one-booking-per-chain rule are undefined.
*   **Rationale:** Bot and worker could not agree on the job contract.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid; line 56 states the rule but no mechanism.
*   ***Proposed Edit:***
    *   Location: M1 (add "Job catalogue" table; reference it from M3, M4, M5)
    *   Change: Add a table. Columns: kind, payload JSON, handler file, max attempts, retry back-off, concurrency. Rows: `check_chain` (`{chain, city}`, `worker/handlers/check_chain.py`, 3 attempts, 60 s × 2^n, one per chain+city); `search_showtimes` (`{user_id, movie_id, day}`, `worker/handlers/search_showtimes.py`, 2 attempts, 30 s); `book` (`{user_id, showtime_id, seats}`, `worker/handlers/book.py`, **1 attempt, no retry**, one per chain); `probe_signals` (`{watch_item_id}`, `worker/handlers/probe_signals.py`, 3 attempts, 60 s × 2^n, one per chain). Queue settings: lock timeout 15 minutes (`JOB_LOCK_TIMEOUT_S`), `locked_by` = `<container hostname>:<pid>`. "One per chain" is enforced inside `claim()`: it skips a job whose `kind` is limited to one and for which another `running` job has the same chain in its payload.

### [Issue 3] [`implementation-readiness`, `codebase-details`] - Severity: Blocker (implementation-readiness-1-3)
*   **Affected Scope:** Lines 170-172, 189 (M2, M4)
*   **Discrepancy Description:** No milestone builds the search flow (date, format, showtime buttons) that booking depends on.
*   **Rationale:** M4 assumes buttons that were never built.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. Legacy does this in `offer_dates`, `handle_date`, `handle_format`, `_send_time_buttons`.
*   ***Proposed Edit:***
    *   Location: M3 (add steps after the scheduler step)
    *   Change: Add: (a) `bot/handlers/search.py`: a `/watch` match or a "Book seats" alert button sends 7 date buttons, callback data `date:<movie_id>:<YYYY-MM-DD>`; (b) that callback enqueues a `search_showtimes` job and replies "Looking up showtimes…"; (c) `worker/handlers/search_showtimes.py` calls `chain.list_showtimes`, stores rows in `showtimes`, then sends either format buttons (`fmt:<movie_id>:<day>:<format>`, only when more than one format exists) or time buttons (`book:<showtime_id>`) through `notify.py`; (d) the watched-movie-now-showing alert from M3 carries a "Book seats" button with data `pick:<movie_id>`. M4 then only adds the `book:` handler.

### [Issue 4] [`implementation-readiness`, `codebase-details`, `style`] - Severity: Blocker (implementation-readiness-1-4)
*   **Affected Scope:** Lines 186-187, 189 (M4); also line 99 (`watch_items.seats`)
*   **Discrepancy Description:** "Unchanged in behaviour" conflicts with a variable seat count; the legacy picker handles pairs only, and `book:` buttons have no watch item.
*   **Rationale:** Any count other than 2 means redesigning the picker.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. `_region_pairs` builds adjacent pairs, `_select_deterministic` returns two IDs, `_finalize` uses `NUM_SEATS = 2`.
*   ***Proposed Edit:***
    *   Location: M4 steps 1-2 and the `watch_items` row in the data model (line 99)
    *   Change: Fix the seat count at 2 (choice C1). Step 2 reads: "Seat count is the constant `SEATS = 2` in `chains/harkins/booker.py`; the `book` job payload carries `seats` and the handler rejects any value other than 2." Remove `seats` from `watch_items` (the data-model row becomes "… `format_pref`"). An N-seat picker is added to Risks as future work.

### [Issue 5] [`implementation-readiness`, `codebase-details`] - Severity: Blocker (implementation-readiness-1-5)
*   **Affected Scope:** Lines 145-212 (whole section)
*   **Discrepancy Description:** File list incomplete, not repo-relative, no Create/Modify/Delete labels.
*   **Rationale:** The standard requires exact paths per phase.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. `core/db.py`, `domain/models.py`, `worker/runner.py`, `bot/keyboards.py` and `scripts/smoke_book.py` appear in the layout or in a step but are created by no milestone.
*   ***Proposed Edit:***
    *   Location: every milestone (M0-M7)
    *   Change: Give each milestone a "Files" table with columns Path | Action (Create/Modify/Delete) | Purpose, using full repo-relative paths (`src/showwatch/db/queue.py`, `tests/integration/test_queue.py`, `tests/fixtures/harkins/now_showing.html`, `migrations/versions/0001_initial.py`). Assign the missing files: M0 creates `src/showwatch/core/db.py` and `src/showwatch/domain/models.py` (skeleton with the `Chain` Protocol, `Capability`, `CapabilityNotSupported`); M2 creates `src/showwatch/bot/keyboards.py`; M3 creates `src/showwatch/worker/runner.py`; M4 creates `scripts/smoke_book.py`. Replace every glob (`db/repositories/`, "Handlers", `handlers/*.py`) with file names.

### [Issue 6] [`codebase-details`, `implementation-readiness`, `style`] - Severity: Blocker (codebase-details-1-1)
*   **Affected Scope:** Line 157 (M0 done-when)
*   **Discrepancy Description:** `python legacy/bot.py --help` would start the live bot (no argument parsing; `main()` scrapes and polls forever).
*   **Rationale:** The check would hang or hit live sites.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. `bot.py` ends with `main()` under `if __name__ == "__main__"` and `main()` calls `run_watch_check()` first.
*   ***Proposed Edit:***
    *   Location: M0 done-when
    *   Change: Replace the `--help` check with `python -m py_compile legacy/*.py legacy/tools/*.py`, run in the legacy venv. It runs no code.

### [Issue 7] [`codebase-details`, `implementation-readiness`, `style`] - Severity: Blocker (codebase-details-1-2)
*   **Affected Scope:** Line 186 (M4 step 1)
*   **Discrepancy Description:** The "existing synthetic-seat check" does not exist in the repo.
*   **Rationale:** There is nothing to convert.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. The check I ran during the restructure was an ad-hoc command, never committed; `booker.py`'s `__main__` block books a live session.
*   ***Proposed Edit:***
    *   Location: M4 step 1
    *   Change: Replace with: "Write `tests/unit/test_seatmap.py` with these cases: `_split_regions` returns one region when fewer than 3 rows exist; a row gap above 1.6 × the median gap splits back region first, then front; `_region_pairs` keeps only consecutive same-row seats, nearest the centre first; `candidate_pairs` lists back-region pairs before front-region pairs; a gap in seat numbers (A3, A5) produces no pair. Use made-up seat lists."

### [Issue 8] [`implementation-readiness`, `codebase-details`] - Severity: Major (implementation-readiness-1-6)
*   **Affected Scope:** Lines 149, 153 (M0)
*   **Discrepancy Description:** Settings keys are never listed ("every key in `.env.example`" is circular).
*   **Rationale:** Cannot be implemented without guessing names.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M0 step 3 (add a "Settings keys" table)
    *   Change: Table with name | type | default | read by. Keys: `DATABASE_URL` (postgres DSN; `postgres:5432` inside Compose, `localhost:5433` from the host), `TEST_DATABASE_URL`, `TELEGRAM_BOT_TOKEN` (new bot, see Issue 14), `ADMIN_TELEGRAM_USER_ID` (int), `LOG_LEVEL` (`INFO`), `CHECK_INTERVAL_S` (1800), `FAST_INTERVAL_S` (60), `FAST_WINDOW_H` (72), `JOB_LOCK_TIMEOUT_S` (900), `POLL_JITTER_PCT` (20), `CHAIN_REQUEST_BUDGET_PER_MIN` (per chain, default 10), `LLM_VISION_BASE_URL`, `LLM_VISION_MODEL`, `LLM_VISION_API_KEY`, `LLM_VISION_TIMEOUT_S` (45), `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_MODEL`, `LLM_FALLBACK_API_KEY`, and the same trio for `LLM_TEXT_*` (intent and title matching). Legacy keeps its own `.env` names (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`) in `legacy/.env`.

### [Issue 9] [`implementation-readiness`, `style`] - Severity: Major (implementation-readiness-1-7)
*   **Affected Scope:** Line 150 (M0 step 4), constraint at line 40
*   **Discrepancy Description:** Key-name redaction misses the bot token that appears in request URLs.
*   **Rationale:** The token would reach logs, breaking the secrets constraint.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. python-telegram-bot requests go through httpx, whose INFO logs include `/bot<TOKEN>/`.
*   ***Proposed Edit:***
    *   Location: M0 step 4
    *   Change: "structlog JSON logs; set the `httpx` and `httpcore` loggers to WARNING; a redaction processor that (a) masks values of keys that equal or end with `token`, `secret`, `password` or `api_key` (whole-name match, so `dedupe_key` is untouched) and (b) masks any string matching `bot\d+:[A-Za-z0-9_-]+`. Test: `tests/unit/test_logging.py`."

### [Issue 10] [`codebase-details`, `implementation-readiness`, `style`] - Severity: Major (codebase-details-1-3)
*   **Affected Scope:** Lines 147, 155, 157 (M0)
*   **Discrepancy Description:** The legacy runtime environment is undefined (`requirements.txt` not moved, `requests` missing, no venv, ignored runtime files not moved).
*   **Rationale:** "The old bot still runs" is unverified.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. `requests` is imported by `bot.py`, `seat_ai.py` and `telegram.py` but absent from `requirements.txt`.
*   ***Proposed Edit:***
    *   Location: M0 step 1
    *   Change: Add `requirements.txt` to the `git mv` list and add `requests` to it. Add: "Create a separate venv for legacy (`legacy/.venv`, ignored); copy any existing `seen_movies.json` and `browser_profile/` from the repo root or `data/` into `legacy/data/` by hand (`git mv` skips ignored files)."

### [Issue 11] [`implementation-readiness`, `codebase-details`] - Severity: Major (implementation-readiness-1-8)
*   **Affected Scope:** Lines 147, 148, 154, 157 (M0)
*   **Discrepancy Description:** CI would fail on legacy code and with no tests; Postgres approach inconsistent; coverage gate not in CI.
*   **Rationale:** M0 would not reach "CI green".
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid, including the `legacy/telegram.py` name clash with the python-telegram-bot `telegram` package.
*   ***Proposed Edit:***
    *   Location: M0 steps 2 and 8, and the stack paragraph (line 132)
    *   Change: In `pyproject.toml`: `testpaths = ["tests"]`; exclude `legacy/` from ruff, mypy and coverage. Add `tests/unit/test_smoke.py` (imports `showwatch` and checks the version) so pytest has at least one test. Choose one Postgres approach (choice C3): drop `testcontainers[postgres]` from the stack; integration tests read `TEST_DATABASE_URL`. CI step: `pytest --cov=src/showwatch --cov-fail-under=85` over the logic modules `domain/`, `db/`, `chains/harkins/seatmap.py`, `llm/`, `worker/scheduler.py`, `bot/handlers/`. Add: "Legacy runs only as scripts inside its own venv; nothing imports `legacy.*`."

### [Issue 12] [`implementation-readiness`, `codebase-details`, `style`] - Severity: Major (implementation-readiness-1-9)
*   **Affected Scope:** Lines 153, 157 (M0)
*   **Discrepancy Description:** "All three services healthy" cannot be met; only Postgres has a healthcheck.
*   **Rationale:** The done-when check is unreachable as written.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M0 done-when
    *   Change: "`docker compose up` shows `postgres` healthy, and `bot` and `worker` running and each having logged `ready`" (choice C4).

### [Issue 13] [`implementation-readiness`, `codebase-details`] - Severity: Major (implementation-readiness-1-10)
*   **Affected Scope:** Lines 157, 182 (M0-M4)
*   **Discrepancy Description:** Legacy and new bots both long-poll one Telegram token, which returns 409 Conflict.
*   **Rationale:** Running both side by side, as M0-M4 assume, fails.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid; Telegram allows one `getUpdates` poller per token.
*   ***Proposed Edit:***
    *   Location: M0 step 9 and the settings table
    *   Change: Add: "The new stack uses its own bot. Create a second bot in BotFather and put its token in `.env` as `TELEGRAM_BOT_TOKEN`; legacy keeps the first bot's token in `legacy/.env`" (choice C2). Add to the M0 done-when: "the new bot answers `/start` while the legacy bot runs."

### [Issue 14] [`implementation-readiness`, `style`] - Severity: Major (implementation-readiness-1-11)
*   **Affected Scope:** Lines 170-171, 174 (M2)
*   **Discrepancy Description:** M2 handler behaviour is unspecified and contradictory (pending reply vs "ignored"; denied users; `/watch` matching; admin row; repeated `/start`).
*   **Rationale:** Two implementers would build different bots.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M2 (add a "Handler spec" table)
    *   Change: Add rules per user status: **unknown** user → creates a `pending` row and sends the admin an Approve/Deny message; **pending** → one reply "Access requested; waiting for approval" on first `/start`, silence after; **denied** → silence; **approved** → full access. Admin: on startup, upsert `users` with `telegram_user_id = ADMIN_TELEGRAM_USER_ID`, `status = approved`, `is_admin = true`. Repeated `/start` by an approved user replies "You're set up." Table of handlers: `/watch <title>` (matches with the ported `find_matches`; shows up to 5 numbered buttons `watchpick:<movie_id>`; saves a `watch_items` row), `/list` (the user's rows, one line each), `/unwatch` (buttons `unwatch:<watch_item_id>`), `/city` (buttons `city:<code>`; sets `users.current_city_id`). Done-when wording uses the same terms.

### [Issue 15] [`implementation-readiness`] - Severity: Major (implementation-readiness-1-12)
*   **Affected Scope:** Lines 178, 180 (depends on 98) (M3)
*   **Discrepancy Description:** The mapping from Harkins scrape results to schema columns is undefined.
*   **Rationale:** `external_id`, `starts_at`, status and the snapshot threshold cannot be derived by guessing.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M3 (add a "Mapping" table)
    *   Change: `movies.external_id` = the last path segment of the movie URL (`/movies/<slug>`); `movies.status` = `now_showing` when the title came from `/movies`, `coming_soon` when from `/movies/coming-soon`; `showtimes.external_session_id` = the number after `/session/` in the booking URL (example in legacy: `.../movie/HO00015450/session/568953/date/2026-07-23`); `showtimes.starts_at` = the page's date plus the time text, read in `America/Phoenix` and stored as UTC; `list_showtimes` reads the theatre rows for the user's city (Arizona Mills, external_id 16); `alerts.dedupe_key` = `new_release:<movie_id>` or `watch_open:<watch_item_id>:<stage>`; a scrape is **suspect** when it returns fewer than 50% of the last good `chain_snapshots.movie_count` (choice C6).

### [Issue 16] [`implementation-readiness`, `style`, `codebase-details`] - Severity: Major (implementation-readiness-1-13)
*   **Affected Scope:** Line 182 (M3 done-when)
*   **Discrepancy Description:** The parity test compares two empty results; legacy has no one-shot mode and rewrites its own state.
*   **Rationale:** The check can pass without proving anything.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. `find_new_movies` only saves a baseline on first run and returns `[]` then.
*   ***Proposed Edit:***
    *   Location: M3 done-when
    *   Change: "Parity test: save one Now Showing page as `tests/fixtures/harkins/now_showing.html`. Pre-seed a seen-titles set that omits 2 titles and a watchlist containing 1 of them. Run `legacy/tracker.find_new_movies` and `find_watchlisted_now_showing` on the fixture titles through a script, and run the new `check_chain` on the same fixture with the same seed. Both must produce the same alerts: 2 new-release alerts and 1 watch alert, matched by type plus title."

### [Issue 17] [`codebase-details`, `implementation-readiness`] - Severity: Major (codebase-details-1-4)
*   **Affected Scope:** Line 188 (M4 step 3)
*   **Discrepancy Description:** The vision port has no request or response contract.
*   **Rationale:** The IMAX 0/3 was a timeout; the port cannot leave this open.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. `seat_ai.py` calls Gemini's native `generateContent`, asks for `[y, x]` points on a 0-1000 scale, parses with a regex, uses 20 points, a 60 s timeout and 2 attempts.
*   ***Proposed Edit:***
    *   Location: M4 step 3
    *   Change: Add the contract. Request: OpenAI-style chat with the canvas PNG as an `image_url` data URL and the legacy `_PROMPT` text. Response: parsed with the legacy regex for a JSON array of `[y, x]` points, 0-1000, origin top-left, at most 20 points. If a model returns pixel or `[x, y]` coordinates, a per-model entry in `settings` (`coord_order`, `scale`) converts to `[y, x]` 0-1000; this is calibrated against 3 saved seat-map screenshots in `tests/fixtures/harkins/` before the live run. Kept constants: 20 points, 2 attempts, `Remove Seat` aria-label, `canvas[aria-label="Theatre seat map"]`; timeout comes from `LLM_VISION_TIMEOUT_S` per provider.

### [Issue 18] [`codebase-details`, `implementation-readiness`, `style`] - Severity: Major (codebase-details-1-5)
*   **Affected Scope:** Lines 171, 191, 193, 206 (M2, M4, M6)
*   **Discrepancy Description:** M6 ports from `legacy/` after M4 deletes it; `/watch` in M2 needs a matcher earlier.
*   **Rationale:** The dependency order is broken.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. (Note: the finding's phrase "years of milestones earlier" should read "several milestones earlier"; the finding itself stands.)
*   ***Proposed Edit:***
    *   Location: M2 (new step) and M6 step 2
    *   Change: M2 creates `src/showwatch/domain/title_match.py`, porting `find_matches` (lowercase substring match first, then `difflib.get_close_matches(n=5, cutoff=0.3)`), with `tests/unit/test_title_match.py`. M6 step 2 now extends `domain/title_match.py` with the LLM matcher and keeps the `difflib` path as the offline fallback.

### [Issue 19] [`implementation-readiness`, `style`] - Severity: Major (implementation-readiness-1-14)
*   **Affected Scope:** Line 193 (M4 done-when)
*   **Discrepancy Description:** The IMAX criterion has no pass mark and no named destination file.
*   **Rationale:** "At least 3 attempts, results recorded" passes at 0/3.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M4 done-when
    *   Change: "IMAX: make 3 live attempts through RC vision; record the results in the README 'Results' table (the repo's convention for benchmark numbers). No pass mark: a failed hold falls back to the manual seat-pick link" (choice C5).

### [Issue 20] [`implementation-readiness`, `style`] - Severity: Major (implementation-readiness-1-15)
*   **Affected Scope:** Lines 197-199, 212 (M5, M7)
*   **Discrepancy Description:** M5 and M7 code tasks have no spec or done-when; fast-poll design open; budget numbers missing.
*   **Rationale:** BMS cannot be specified before the probe, but the plan leaves the follow-up open.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. The right fix is a gate, not invented detail.
*   ***Proposed Edit:***
    *   Location: M5 steps 1-3 and M7
    *   Change: Insert after the probe: "Re-plan checkpoint: write `docs/decisions/019-bms-probe.md` and add this milestone's file table (chain module files, stage-detection rules, fixture names) to `docs/plan.md`; the user approves it before coding." Fix the design: `probe_signals` is its own job kind, enqueued by the scheduler only for watches with `stage` in `listed|dates|venues` (fast interval) and by `check_chain` for the rest. Numbers: request budget 10 per minute per chain (`CHAIN_REQUEST_BUDGET_PER_MIN`); back-off after a 403, 429 or challenge page: 5 minutes, doubling to a 60-minute cap, reset after one clean response. M7 gets the same re-plan checkpoint, a done-when ("same as M5, per site"), and decision files `020-district-probe.md` and `021-pvrinox-probe.md`.

### [Issue 21] [`implementation-readiness`, `style`] - Severity: Major (implementation-readiness-1-16)
*   **Affected Scope:** Lines 205, 208 (M6)
*   **Discrepancy Description:** Intent parsing has no schema, failure paths, or test strategy.
*   **Rationale:** The "Dune IMAX Friday" check depends on live site state.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M6 step 1 and done-when
    *   Change: Define a pydantic `ParsedRequest` (`title: str`, `city: str | None`, `date_from: date | None`, `date_to: date | None`, `part_of_day: Literal["morning","afternoon","evening","night"] | None`, `format: str | None`, `seats: int = 2`, `confidence: float`). Part-of-day windows (local time): morning 06-12, afternoon 12-17, evening 17-21, night 21-02. If `confidence < 0.6`, or the LLM errors or times out, the bot replies "I didn't catch that" and shows the `/watch` flow instead. The confirm button's callback data is `intent:<request_id>`; the stored request becomes a `search_showtimes` job. Tests use a fake LLM client with recorded replies (`tests/unit/test_intent.py`); the done-when splits in two: the parse check runs on fixtures, and the live confirm-then-book step runs on any Harkins movie that has showtimes on the test day.

### [Issue 22] [`implementation-readiness`] - Severity: Major (implementation-readiness-1-17)
*   **Affected Scope:** Lines 166, 174, 179, 201, 208, 216
*   **Discrepancy Description:** Test plans are thin outside M1.
*   **Rationale:** Done-when checks cannot be confirmed without concrete cases.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: each milestone ("Tests" line in every Files table)
    *   Change: Add named cases. M2: callback from an unapproved user is dropped; admin approval flips `pending` to `approved`; `/list` shows only the caller's rows. M3: a suspect snapshot sends no alert; a duplicate alert is suppressed by `dedupe_key`; fixture parse returns the expected titles and times. M4: calling `book()` on a chain without `BOOK` raises `CapabilityNotSupported`; the bot rejects seats other than 2. M5: poll jitter stays within ±20%; the interval drops from base to fast when `stage` reaches `listed`; a 429 triggers back-off. M6: `ParsedRequest` low confidence falls back to `/watch`.

### [Issue 23] [`implementation-readiness`] - Severity: Major (implementation-readiness-1-18)
*   **Affected Scope:** Lines 152, 190
*   **Discrepancy Description:** Browser and profile handling are unspecified.
*   **Rationale:** Concurrent jobs could corrupt a persistent profile.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M4 (add as step 1, with the Harkins login check)
    *   Change: "One browser context per job, closed in a `finally` block. Each chain's persistent profile lives at `/profiles/<chain code>` on the `showwatch_profiles` volume. If a profile is locked, the job is re-queued 60 seconds later (the one-booking-per-chain rule makes this rare)."

### [Issue 24] [`codebase-details`] - Severity: Major (codebase-details-1-6)
*   **Affected Scope:** Lines 163, 187, 189
*   **Discrepancy Description:** Booking outcomes and errors are not mapped to job results; `bot.handle_book` checks for `None`, which `book_session` never returns; retry could hold seats twice.
*   **Rationale:** A retry after a half-finished hold is a real double-booking risk.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid; `book_session` has no `finally`, and raises on `wait_for_url` or `goto` timeouts.
*   ***Proposed Edit:***
    *   Location: M4 step 4
    *   Change: Add an outcome table for `book` jobs: **held** (`{seats, url}`) → "Seats X + Y held! Pay here: <url>"; **manual** (`{manual, url}`) → "Couldn't auto-pick seats; choose 2 yourself here: <url>"; **failed** (any exception) → "Booking failed — try again", logged with the error. `book` jobs have `max_attempts = 1`. The browser is closed in a `finally` block.

### [Issue 25] [`codebase-details`, `style`] - Severity: Major (codebase-details-1-7)
*   **Affected Scope:** Lines 178-180 (M3)
*   **Discrepancy Description:** The M3 watching port omits Coming Soon, the first-run baseline rule, parsing details, and the watch lifecycle.
*   **Rationale:** An empty database would alert every one of the roughly 65 movies on the first check.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M3 steps 1 and 3
    *   Change: Port `get_now_playing` and `get_coming_soon` (URLs `https://www.harkins.com/movies` and `.../movies/coming-soon`). Rules to carry over: skip links with no `aria-label`; skip showtimes whose link class contains `disabled`; format defaults to `Standard`; the showtime URL is `{movie_url}/{YYYY-MM-DD}`; keep only links under `/ticketing/theatre/16/`; `FORMAT_JS` relies on the generated class `sc-d9066650-9`, so the fixture test must fail loudly if it stops matching. **First-run rule:** the first successful check for a chain and city stores movies without sending alerts. **Watch lifecycle:** after the "now showing" alert the watch item moves to `stage = done` (legacy removes the title, `bot.py:135`).

### [Issue 26] [`style`, `implementation-readiness`] - Severity: Major (style-1-1)
*   **Affected Scope:** Lines 136, 157, 216, 217
*   **Discrepancy Description:** CI and PR checks depend on pushes and PRs the plan never schedules.
*   **Rationale:** "CI green" and "every commit" cannot be checked.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid; plan rules forbid push without per-push confirmation.
*   ***Proposed Edit:***
    *   Location: Step 0 and the Verification section
    *   Change: "Before each commit I run `ruff`, `mypy` and `pytest` locally. CI runs after each push, which only happens when you confirm it. At the end of each milestone I open a PR to `main` on your confirmation; its description holds that milestone's results."

### [Issue 27] [`style`] - Severity: Major (style-1-2)
*   **Affected Scope:** Line 201
*   **Discrepancy Description:** "Normal rates" and "test window" are undefined.
*   **Rationale:** The check cannot be confirmed.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M5 done-when
    *   Change: "…and no back-off triggered during a 2-hour run at the 30-minute base interval plus one 10-minute run at the 1-minute fast interval" (choice C7).

### [Issue 28] [`style`] - Severity: Major (style-1-3)
*   **Affected Scope:** Line 225 (Risks table)
*   **Discrepancy Description:** "Caching" is never built, and "or account" does not fit M5 (no login).
*   **Rationale:** The mitigation names things the plan does not do.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: Risks table, first row
    *   Change: "BookMyShow blocks the home IP" with mitigation "Request budget, jitter and back-off (M5 step 3); probe first; stop rather than evade."

### [Issue 29] [`style`, `implementation-readiness`] - Severity: Major (style-1-4)
*   **Affected Scope:** Line 190 (M4 step 5)
*   **Discrepancy Description:** Step 5 says "before the porting work" but comes after steps 1-4.
*   **Rationale:** Contradicts the stated order.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M4
    *   Change: Move the login and RC-reachability checks to step 1 and renumber (the profile handling from Issue 23 joins this step).

### [Issue 30] [`implementation-readiness`] - Severity: Nit (implementation-readiness-1-19)
*   **Affected Scope:** Line 155
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Cheap and removes ambiguity.
*   ***Proposed Edit:***
    *   Location: M0 step 9
    *   Change: "one short file per grilling question, numbered `001`-`018`".

### [Issue 31] [`implementation-readiness`] - Severity: Nit (implementation-readiness-1-20)
*   **Affected Scope:** Lines 140, 147
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: Step 0 and M0 step 1
    *   Change: "Commit `docs/plan.md` on the `revamp` branch. `docs/procedure.md` stays. The `data/CONTEXT.md` and `tools/CONTEXT.md` files move into `legacy/` with their folders."

### [Issue 32] [`codebase-details`] - Severity: Nit (codebase-details-1-8)
*   **Affected Scope:** Line 155
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** `.env` is already in `.gitignore`.
*   ***Proposed Edit:***
    *   Location: M0 step 9
    *   Change: "`.gitignore`: keep the existing entries; add `.venv/`, `legacy/.venv/` and `*.egg-info`."

### [Issue 33] [`style`] - Severity: Nit (style-1-5)
*   **Affected Scope:** Line 141
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: Step 0
    *   Change: "I edit `docs/plan.md` to resolve findings; you approve before M0 starts."

### [Issue 34] [`style`] - Severity: Nit (style-1-6)
*   **Affected Scope:** Line 152
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M0 step 6
    *   Change: Name the base image: `mcr.microsoft.com/playwright/python:v<pinned Playwright version>-noble`.

### [Issue 35] [`style`] - Severity: Nit (style-1-7)
*   **Affected Scope:** Line 163
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M1 step 3
    *   Change: "a sweep that sets `running` jobs back to `queued` when `locked_at` is older than `JOB_LOCK_TIMEOUT_S`".

### [Issue 36] [`style`] - Severity: Nit (style-1-8)
*   **Affected Scope:** Line 166
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid; a test cannot prove "never".
*   ***Proposed Edit:***
    *   Location: M1 done-when
    *   Change: "no duplicate claims across 50 concurrent claim attempts by 2 workers".

### [Issue 37] [`style`] - Severity: Nit (style-1-9)
*   **Affected Scope:** Line 174
*   ***AI Suggestion:*** DECLINE
*   ***Justification:*** The wording is covered by Issue 22, which names the M2 test cases.

### [Issue 38] [`style`] - Severity: Nit (style-1-10)
*   **Affected Scope:** Line 178
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Covered by the theatre seed in Issue 1; a single edit is enough.
*   ***Proposed Edit:***
    *   Location: M3 step 1
    *   Change: "keep the Arizona Mills theatre (external_id 16) as a `theatres` row".

### [Issue 39] [`style`] - Severity: Nit (style-1-11)
*   **Affected Scope:** Line 180
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M3 step 3
    *   Change: "alert new Now Showing titles, and watched titles that become Now Showing".

### [Issue 40] [`style`] - Severity: Nit (style-1-12)
*   **Affected Scope:** Line 191
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M4 step 6
    *   Change: "Delete `legacy/` once the M4 done-when checks pass."

### [Issue 41] [`style`] - Severity: Nit (style-1-13)
*   **Affected Scope:** Line 193
*   ***AI Suggestion:*** DECLINE
*   ***Justification:*** Trivial wording ("at least 5 of 5"); no reader is misled.

### [Issue 42] [`style`] - Severity: Nit (style-1-14)
*   **Affected Scope:** Line 197
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Covered by the numbers in Issue 20; one edit defines "plain access".
*   ***Proposed Edit:***
    *   Location: M5 step 1
    *   Change: "Plain access means access without login, CAPTCHA or a challenge page. Probe rate: at most 10 requests per minute for 10 minutes."

### [Issue 43] [`style`] - Severity: Nit (style-1-15)
*   **Affected Scope:** Line 199
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: M5 step 3 and constraints (line 39)
    *   Change: Use one term, "request budget", in both places.

### [Issue 44] [`style`] - Severity: Nit (style-1-16)
*   **Affected Scope:** Line 219
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: Verification section
    *   Change: "A separate review pass in a fresh session (the EGM review or a fresh subagent) reads each milestone's diff before your check."

## Remaining after auto accept

*   **Applied by "auto accept":** the 42 PROPOSE FIX items, as one batch of edits to the plan file. Items 3, 5, 14, 15, 20 and 21 add whole sections or tables; the rest are one-line to paragraph-length edits.
*   **Not applied:** the 2 DECLINE items (Issues 37 and 41).
*   **Open decisions:** C1-C7 above stay as written unless you overrule them.
