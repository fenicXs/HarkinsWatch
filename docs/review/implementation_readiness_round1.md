# Goldfish Review Report: Implementation Readiness (Stage 9), Round 1

**Target:** `docs/plan.md` (lines 134-229, "## Detailed Implementation Plan" to end of file)

**Intended location:** `docs/review/implementation_readiness_report.md`. It was not written there because plan mode was active during this run, so the harness allowed only this file to be written. Copy it to the intended path when you are out of plan mode.

**Verdict:** Not ready to implement. 7 Blockers, 22 Majors, 15 Nits.

**Severity rule:** when reviewers gave the same finding different severities, this report uses the highest one.

## Reviewer Status

| Reviewer | Status | Raw counts (before deduplication) |
| --- | --- | --- |
| `implementation-readiness` | completed | 5 Blockers, 16 Majors, 4 Nits |
| `codebase-details` | completed | 2 Blockers, 10 Majors, 1 Nit |
| `style` | completed | 0 Blockers, 11 Majors, 17 Nits |

## Confirmed accurate (codebase-details)

- Every file in the M0 `git mv` list exists.
- Every ported symbol exists: `scrape_movies`, `get_showtimes`, `FORMAT_JS`, `_split_regions`, `_region_pairs`, `candidate_pairs`, `book_session`, `_select_deterministic`, `_finalize`, `_select_seat`, `_clear_selection`, `pick_seats_by_vision`, `find_matches`.
- `paths.py` anchors to its own folder (`paths.py:4`).
- The checkout is on `restructure/icm-layout` at `083cf1c`, as the plan expects.

## Prioritized Findings (Blockers -> Major -> Nits)

### 🔴 Blockers

#### implementation-readiness-1-1: Database schema not specified - Severity: Blocker
* **Reviewer Lens:** `implementation-readiness`
* **Affected Scope:** Lines 161, 164, 171 (depends on 86-98)
* **Discrepancy Description**: M1 builds "the tables above", but lines 86-98 give only key columns. Missing items:
  * primary and foreign keys, column types, nullability, defaults, `created_at`/`updated_at`;
  * unique constraints such as `movies(chain_id, external_id)`, `showtimes(theatre_id, external_session_id)`, `theatres(chain_id, external_id)`;
  * an index on `jobs(status, run_after)`;
  * allowed values for `jobs.status`, `jobs.kind`, `movies.status`, `watch_items.stage`, `showtimes.format`;
  * the exact seed rows, including the Arizona Mills `theatres` row (external_id 16) that line 178 assumes;
  * somewhere to store a user's current city for `/city` (line 171).
* **Rationale & Recommendation**: The implementer would have to design the schema. Add a DDL-level spec, including the theatres seed and a `users.current_city_id` column (or say where `/city` stores its value).

#### implementation-readiness-1-2: Job kinds, payloads and queue parameters undefined - Severity: Blocker
* **Reviewer Lens:** `implementation-readiness`
* **Affected Scope:** Lines 163, 180, 189 (depends on 56)
* **Discrepancy Description**: The plan never lists every `jobs.kind` value or its payload JSON. Also missing:
  * maximum attempts and the back-off formula;
  * the sweep's lock-timeout value and the `locked_by` format;
  * how the "1 booking per chain" rule (line 56) is enforced (a claim filter or an advisory lock).
* **Rationale & Recommendation**: The bot and worker would not agree on the job contract. Add a job catalogue (kind, payload schema, handler file, retry policy, concurrency rule) and the numeric queue settings.

#### implementation-readiness-1-3: No milestone builds the showtime search flow - Severity: Blocker
* **Reviewer Lens:** `implementation-readiness`, `codebase-details`
* **Affected Scope:** Lines 170-172, 189
* **Discrepancy Description**: Legacy `bot.py` books through four steps: `offer_dates` (7 days, `bot.py:50-65`), `handle_date` (groups by format, `bot.py:77-94`), `handle_format`, then `_send_time_buttons`. M2 has only `/start`, `/watch`, `/list`, `/unwatch`, `/city` and admin callbacks. Line 189 assumes "bot showtime buttons" already exist. No milestone creates:
  * the search-showtimes job or its worker handler;
  * the `date:`/`fmt:` callback handlers or their data formats (only `book:<showtime_id>` is defined);
  * the "Book seats" button legacy attaches to watchlist alerts.
* **Rationale & Recommendation**: M4 cannot be built on M2 as written. Add these steps, file paths, callback formats and messages to M3 or M4.

#### implementation-readiness-1-4: "Unchanged in behaviour" conflicts with a variable seat count - Severity: Blocker
* **Reviewer Lens:** `implementation-readiness`, `codebase-details`, `style`
* **Affected Scope:** Lines 186-187, 189
* **Discrepancy Description**: The legacy picker only handles 2 seats, but line 187 takes the seat count from the watch item:
  * `_region_pairs` builds adjacent pairs only (`booker.py:37-41`);
  * `_select_deterministic` returns two IDs (`booker.py:90`);
  * `_finalize` uses `NUM_SEATS = 2` (`booker.py:7, 58`).
  
  Separately, a `book:<showtime_id>` button from a search or a plain-language request has no watch item to read a seat count from.
* **Rationale & Recommendation**: Any count other than 2 means redesigning the picker. Either fix the count at 2 in M4 and reject other values in the bot, or specify an N-seat run algorithm with tests for N = 1, 2, 4. Define where the seat count comes from at each entry point.

#### implementation-readiness-1-5: File list is incomplete, not repo-relative, and has no actions - Severity: Blocker
* **Reviewer Lens:** `implementation-readiness`, `codebase-details`
* **Affected Scope:** Lines 145-212 (whole section)
* **Discrepancy Description**:
  * Files in the layout that no milestone creates: `core/db.py`, `domain/models.py` (the Protocol types and `CapabilityNotSupported`), `worker/runner.py` (which M3 needs), `bot/keyboards.py`, `scripts/smoke_book.py` (which line 218 needs).
  * M1-M7 paths omit `src/showwatch/`.
  * Several items are globs instead of file names: `db/repositories/`, "Handlers", `chains/bms/`, `handlers/*.py`, and every test file.
  * No file is marked Create, Modify or Delete.
* **Rationale & Recommendation**: The standard requires exact paths for each phase. List every repo-relative path with its action for each milestone, covering source, migrations, tests and fixtures.

#### codebase-details-1-1: The `--help` smoke check starts the live bot - Severity: Blocker
* **Reviewer Lens:** `codebase-details`, `implementation-readiness`, `style`
* **Affected Scope:** Line 157
* **Discrepancy Description**: `bot.py` does not parse arguments, and lines 201-202 call `main()` directly. `main()` then:
  * runs `run_watch_check()`, which scrapes harkins.com and rewrites `data/coming_soon.json` and `data/seen_movies.json` (`bot.py:146-147`, `tracker.py:17-18, 41`);
  * polls Telegram forever.
  
  Even a plain import runs `load_dotenv()` and builds the API URL with the token.
* **Rationale & Recommendation**: In CI the check would either hang or hit live sites, which breaks line 218. Name an exact command that runs nothing, such as `python -m py_compile legacy/*.py`, and say which environment runs it.

#### codebase-details-1-2: The "existing synthetic-seat check" does not exist - Severity: Blocker
* **Reviewer Lens:** `codebase-details`, `implementation-readiness`, `style`
* **Affected Scope:** Line 186
* **Discrepancy Description**: The repo has no "synthetic" match and no `tests/` folder. The `__main__` block in `booker.py` (lines 165-167) books a live session. `tools/test_setup.py` only checks that Playwright loads a page.
* **Rationale & Recommendation**: There is nothing to convert. Write the test cases into the plan:
  * `_split_regions` with fewer than 3 rows returns one region (`booker.py:13-14`);
  * a gap above 1.6 x the median splits back then front (`booker.py:18-21`);
  * `_region_pairs` keeps consecutive same-row seats, nearest the centre first (`booker.py:36-43`);
  * `candidate_pairs` puts the back region first;
  * gaps in seat numbers.

### 🟡 Major

#### implementation-readiness-1-6: Settings keys never listed - Severity: Major
* **Reviewer Lens:** `implementation-readiness`, `codebase-details`
* **Affected Scope:** Lines 149, 153
* **Discrepancy Description**: "Every key listed in `.env.example`" is circular, because the plan never lists the keys. Unnamed keys include:
  * the database URL (`postgres:5432` inside Compose versus `localhost:5433` from the host);
  * the admin Telegram ID;
  * the poll intervals and timeout;
  * the per-task LLM keys and timeouts;
  * the per-chain budgets;
  * whether the legacy names `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `GEMINI_API_KEY` are kept.
* **Rationale & Recommendation**: Add a table with each key's name, type, default and the module that reads it.

#### implementation-readiness-1-7: Key-name redaction misses the bot token in URLs - Severity: Major
* **Reviewer Lens:** `implementation-readiness`, `style`
* **Affected Scope:** Line 150 (constraint at line 40)
* **Discrepancy Description**: python-telegram-bot logs requests through httpx at INFO, and those request URLs contain `/bot<TOKEN>/`. A filter on key names never sees them. The bare substring `key` also matches harmless fields such as `dedupe_key`. The plan also doesn't say whether matching is by whole name or by substring.
* **Rationale & Recommendation**: The token would reach the logs, which breaks the secrets constraint. Set the `httpx`/`httpcore` loggers to WARNING, add a value-pattern redactor (for example `bot\d+:[A-Za-z0-9_-]+`), and define the match rule.

#### codebase-details-1-3: Legacy runtime environment not defined - Severity: Major
* **Reviewer Lens:** `codebase-details`, `implementation-readiness`, `style`
* **Affected Scope:** Lines 147, 155, 157
* **Discrepancy Description**:
  * `requirements.txt` is not in the move list, and it lacks `requests`, which `bot.py:4`, `seat_ai.py:12` and `telegram.py:3` import.
  * The new `pyproject.toml` will not contain sync Playwright, python-dotenv or `requests`, and the plan names no legacy interpreter or venv.
  * `git mv` skips ignored runtime files (`data/seen_movies.json`, `data/browser_profile/`), so they would not follow `legacy/paths.py`. Neither exists in the working tree today.
* **Rationale & Recommendation**: The claim that "the old bot still runs" (line 147) is unverified. Move `requirements.txt` to `legacy/` and add `requests`, name the legacy venv, and keep the legacy key names.

#### implementation-readiness-1-8: CI scope will fail on legacy code - Severity: Major
* **Reviewer Lens:** `implementation-readiness`, `codebase-details`
* **Affected Scope:** Lines 147, 148, 154, 157
* **Discrepancy Description**:
  * pytest would collect `legacy/tools/test_setup.py`, which launches Chromium on import.
  * With no tests in M0, pytest exits with code 5 and CI fails.
  * ruff and mypy would scan the untyped `legacy/` folder.
  * `legacy/telegram.py` has the same name as python-telegram-bot's `telegram` package, so `import legacy.bot` fails with an ImportError on `send_telegram`.
  * Line 154 uses a CI Postgres service container, but line 132 chose `testcontainers`.
  * The 85% coverage gate is not in CI, and "logic modules" is not defined.
* **Rationale & Recommendation**: The CI for M0 would not pass. Set `testpaths = ["tests"]`, exclude `legacy/` from ruff, mypy and coverage, add one smoke test, choose one Postgres approach, and add `--cov-fail-under=85` with a named module list.

#### implementation-readiness-1-9: "All three services healthy" cannot be met - Severity: Major
* **Reviewer Lens:** `implementation-readiness`, `codebase-details`, `style`
* **Affected Scope:** Lines 153, 157
* **Discrepancy Description**: Only `postgres` has a healthcheck, so Compose can never report `bot` or `worker` as healthy.
* **Rationale & Recommendation**: Add healthchecks to both, or change the check to "postgres healthy; bot and worker running and logged `ready`".

#### implementation-readiness-1-10: Legacy and new bots conflict on one Telegram token - Severity: Major
* **Reviewer Lens:** `implementation-readiness`, `codebase-details`
* **Affected Scope:** Lines 157, 182
* **Discrepancy Description**: Both bots use long polling. Two pollers on one token get 409 Conflict, yet M0-M4 assume both run side by side.
* **Rationale & Recommendation**: Require a separate bot token for the new stack and name its key, or state that legacy is stopped while the new stack runs.

#### implementation-readiness-1-11: M2 handler behaviour unspecified and contradictory - Severity: Major
* **Reviewer Lens:** `implementation-readiness`, `style`
* **Affected Scope:** Lines 170-171, 174
* **Discrepancy Description**:
  * Line 170 gives pending users one "access requested" reply, but line 174 says unapproved updates "are ignored". Denied users are covered by neither line.
  * The plan doesn't say how `/watch <title>` matches a movie before M6, or how it fills `movie_id`.
  * `/unwatch` and `/city` have no argument shapes or reply text.
  * It doesn't say how the admin row is created or how `is_admin` relates to the settings ID.
  * A repeated `/start` is not handled.
* **Rationale & Recommendation**: Write one rule per user status, plus a spec for each handler (inputs, database effects, replies). Use the same words in the done-when check.

#### implementation-readiness-1-12: Harkins data mapping to the schema undefined - Severity: Major
* **Reviewer Lens:** `implementation-readiness`
* **Affected Scope:** Lines 178, 180 (depends on 98)
* **Discrepancy Description**: The legacy scraper returns `{title, url}` and `{time, url, format}`. The plan doesn't say how to derive:
  * `movies.external_id`, `external_session_id` and `starts_at` (UTC, from local time and the date in `America/Phoenix`). The URL `/ticketing/theatre/16/movie/HO.../session/568953/` (`booker.py:166`) shows where the IDs could come from.
  * `movies.status` (now playing versus coming soon);
  * which theatre `list_showtimes(movie, day)` scrapes;
  * the `dedupe_key` format;
  * how a watch query is matched to a movie;
  * the number behind "far fewer movies" in the snapshot check.
* **Rationale & Recommendation**: Specify each derivation and give the snapshot threshold.

#### implementation-readiness-1-13: The M3 parity test compares two empty results - Severity: Major
* **Reviewer Lens:** `implementation-readiness`, `style`, `codebase-details`
* **Affected Scope:** Line 182
* **Discrepancy Description**:
  * `tracker.find_new_movies` only saves a baseline on its first run (`tracker.py:39-44`), and the new database starts empty, so one live run compares two empty sets.
  * Running legacy rewrites its baseline and can remove watchlist entries.
  * Legacy has no one-shot mode; its loop long-polls Telegram.
* **Rationale & Recommendation**: Define a reproducible comparison. For example, use the same saved fixture pages and a pre-seeded seen-title set on both sides, call the legacy `scraper`/`tracker` functions from a script, and list the expected alerts (type plus title).

#### codebase-details-1-4: Vision port has no request or response contract - Severity: Major
* **Reviewer Lens:** `codebase-details`, `implementation-readiness`
* **Affected Scope:** Line 188
* **Discrepancy Description**: `seat_ai` calls Gemini's native `generateContent` API (`seat_ai.py:18, 33-39`). It asks for `[y, x]` points on a 0-1000 scale (`seat_ai.py:25`) and parses them with a regex that expects that shape (`seat_ai.py:48, 83-87`). Its fixed values are:
  * 20 points, a 60 s timeout, and 2 attempts;
  * the `Remove Seat` aria-label and the canvas selector.
  
  The plan moves to OpenAI-compatible chat with Qwen as the first choice, but defines no prompt, coordinate convention, parsing step or per-provider timeout.
* **Rationale & Recommendation**: A timeout is what caused the IMAX 0/3, so this cannot be left open. Give the prompt, the expected shape for each model, how each is normalised to `[y, x]` 0-1000, and which constants are kept.

#### codebase-details-1-5: M6 uses `legacy/` after M4 deletes it; `/watch` has no matcher - Severity: Major
* **Reviewer Lens:** `codebase-details`, `implementation-readiness`, `style`
* **Affected Scope:** Lines 171, 191, 193, 206
* **Discrepancy Description**: M4 deletes `legacy/`, but M6 ports `legacy/add_watch.find_matches`. The `/watch` command in M2 needs a matcher years of milestones earlier. Line 206 also describes the matcher imprecisely: it does a lowercase substring match first, then `get_close_matches(n=5, cutoff=0.3)` (`add_watch.py:67-74`).
* **Rationale & Recommendation**: Port `find_matches` in M2 or M3 (for example to `src/showwatch/domain/title_match.py`) with unit tests, and point M6 at the ported module.

#### implementation-readiness-1-14: The IMAX criterion has no pass mark - Severity: Major
* **Reviewer Lens:** `implementation-readiness`, `style`
* **Affected Scope:** Line 193
* **Discrepancy Description**: "At least 3 attempts ... results recorded" passes even at 0/3. The target file is not named, and the repo convention keeps benchmark numbers in README Results, not in `docs/decisions/`.
* **Rationale & Recommendation**: Set a threshold or state that the step only records results, and name one destination file.

#### implementation-readiness-1-15: M5 and M7 code tasks have no spec or done-when - Severity: Major
* **Reviewer Lens:** `implementation-readiness`, `style`
* **Affected Scope:** Lines 197-199, 212
* **Discrepancy Description**:
  * `chains/bms/` lists no files, stage-detection rules or fixture names.
  * The fast-poll scheduler design is open: separate `probe_signals` jobs, or part of the chain check.
  * The rate budget and back-off have no numbers.
  * M7 has no done-when, and its decision files are not named.
* **Rationale & Recommendation**: After each probe, add a re-plan step that writes that chain's file and step list, with a review checkpoint before coding. Give M7 a done-when and name `020-district-probe.md` and `021-pvrinox-probe.md`.

#### implementation-readiness-1-16: Intent parsing has no schema or test strategy - Severity: Major
* **Reviewer Lens:** `implementation-readiness`, `style`
* **Affected Scope:** Lines 205, 208
* **Discrepancy Description**:
  * There is no typed output schema.
  * Words like "evening" have no defined meaning.
  * Low-confidence parses and LLM failures have no defined behaviour.
  * The plan doesn't say how the confirm button maps to a job, or how tests avoid live LLM calls.
  * The "Dune IMAX Friday" check depends on what Harkins is showing that day.
* **Rationale & Recommendation**: Define the schema and the failure paths. Use a fake client or recorded responses for tests. Make the parse a fixture test, separate from the live confirm-then-book step.

#### implementation-readiness-1-17: Test plans are thin outside M1 - Severity: Major
* **Reviewer Lens:** `implementation-readiness`
* **Affected Scope:** Lines 166, 174, 179, 201, 208, 216
* **Discrepancy Description**: Only M1 names concrete test cases.
* **Rationale & Recommendation**: List each milestone's test cases and file paths. Examples:
  * an unapproved callback is dropped;
  * a suspect snapshot sends no alert;
  * a duplicate alert is suppressed;
  * jitter stays within ±20%;
  * calling a missing capability raises `CapabilityNotSupported`.

#### implementation-readiness-1-18: Browser and profile handling unspecified - Severity: Major
* **Reviewer Lens:** `implementation-readiness`
* **Affected Scope:** Lines 152, 190
* **Discrepancy Description**: The plan doesn't say:
  * whether async Playwright uses one browser per job or a shared one per worker;
  * the profile path for each chain inside `showwatch_profiles`;
  * what happens when a profile is locked.
* **Rationale & Recommendation**: Specify all three.

#### codebase-details-1-6: Booking outcomes and errors not mapped to job results - Severity: Major
* **Reviewer Lens:** `codebase-details`
* **Affected Scope:** Lines 163, 187, 189
* **Discrepancy Description**:
  * `book_session` returns either `{"seats", "url"}` or `{"manual": True, "url"}` (`booker.py:104-129`).
  * It raises on a `wait_for_url` or `goto` timeout, and has no `finally` to close the browser.
  * `bot.handle_book` checks for `None`, which `book_session` never returns (`bot.py:112`).
  * The general "retry and back-off" rule (line 163) could hold seats twice.
* **Rationale & Recommendation**: List the `book` job outcomes (held, manual, failed) and the message each sends. Set `max_attempts=1` for `book` jobs, and close the browser in a `finally` block.

#### codebase-details-1-7: Gaps in the M3 watching port - Severity: Major
* **Reviewer Lens:** `codebase-details`, `style`
* **Affected Scope:** Lines 178-180
* **Discrepancy Description**:
  * `get_now_playing`, `get_coming_soon` and their URLs are not ported (`scraper.py:4-5, 32-39`), though legacy uses Coming Soon for title matching.
  * The first-run baseline rule is missing, so an empty database would alert all ~65 movies on the first check.
  * Parsing details go unstated:
    * links without an aria-label are skipped;
    * disabled showtimes are skipped;
    * the format defaults to "Standard";
    * `FORMAT_JS` relies on the generated class `sc-d9066650-9`;
    * the URL is built as `{movie_url}/{date}`;
    * results are filtered by theatre ID.
  * The watch-item lifecycle after an alert is not stated; legacy removes the title (`bot.py:135`).
* **Rationale & Recommendation**: Add the Coming Soon functions, the first-run baseline rule, the parsing rules and the watch lifecycle.

#### style-1-1: CI and PR checks depend on pushes the plan never schedules - Severity: Major
* **Reviewer Lens:** `style`, `implementation-readiness`
* **Affected Scope:** Lines 136, 157, 216, 217
* **Discrepancy Description**: GitHub Actions runs only after a push, but line 136 never says when pushes happen. "CI green" (line 157) and "every commit" (line 216) therefore cannot be checked. The "milestone PR description" (line 217) refers to PRs the plan never introduces.
* **Rationale & Recommendation**: Say what runs locally before each commit. Say CI runs on each confirmed push, and say who opens PRs and when.

#### style-1-2: "Normal rates" and "test window" are undefined - Severity: Major
* **Reviewer Lens:** `style`
* **Affected Scope:** Line 201
* **Discrepancy Description**: "No back-off at normal rates" cannot be checked as written.
* **Rationale & Recommendation**: Give the intervals used (base 30 min, fast 1-2 min, per line 82) and the length of the test window.

#### style-1-3: The "caching" mitigation is never built - Severity: Major
* **Reviewer Lens:** `style`
* **Affected Scope:** Line 225
* **Discrepancy Description**: No milestone builds caching. "Or account" does not fit either, because M5 runs without a login.
* **Rationale & Recommendation**: Drop both phrases, or add a caching item to M5.

#### style-1-4: Step numbering contradicts the stated order - Severity: Major
* **Reviewer Lens:** `style`, `implementation-readiness`
* **Affected Scope:** Line 190
* **Discrepancy Description**: Step 5 says "before the porting work", yet it comes after porting steps 1-4.
* **Rationale & Recommendation**: Move it to step 1 and renumber the rest.

### 🟢 Nits

| ID | Lines | Discrepancy and fix |
| --- | --- | --- |
| implementation-readiness-1-19 | 155 | `001…018`: the decision table merges some rows (4/5/7/8, 10-11, 14-15). Say whether there is one file per row or one per number. |
| implementation-readiness-1-20 | 140, 147 | Step 0 doesn't say whether the plan copy is committed. Say what happens to `docs/procedure.md` and the `tools/CONTEXT.md`/`data/CONTEXT.md` routing files. |
| codebase-details-1-8 | 155 | `.env` is already in `.gitignore:4`. Say "keep existing entries; add `.venv/` and `*.egg-info`". |
| style-1-5 | 141 | "Findings are resolved" names no person or record. Say who edits `docs/plan.md` and who approves. |
| style-1-6 | 152 | "The Playwright Python image" is not named. Give the reference, for example `mcr.microsoft.com/playwright/python:v<pinned>-noble`. |
| style-1-7 | 163 | "A sweep that returns jobs" reads like a return value. Use "re-queues", and name the lock-timeout setting. |
| style-1-8 | 166 | "Never getting the same job" cannot be proven by a test. Say "no duplicate claims across N concurrent claim attempts". |
| style-1-9 | 174 | "Handler unit tests use a fake bot" describes a method, not a pass condition. Say "pass using a fake bot". |
| style-1-10 | 178 | Give the Arizona Mills theatre ID value (16). |
| style-1-11 | 180 | "Alert new releases and watched movies" is imprecise. Say "new Now Showing titles, and watched titles that become Now Showing". |
| style-1-12 | 191 | "Once standard-screen booking matches it" repeats line 193 less precisely. Point to the M4 done-when check instead. |
| style-1-13 | 193 | "At least 5 of 5" is redundant. Use "5 of 5". |
| style-1-14 | 197 | "Repeated requests" needs a rate and a duration. Define "plain access" as access without login, CAPTCHA or a challenge page. |
| style-1-15 | 199 | "Rate budget" drifts from "request budget" (line 39). Use one term. |
| style-1-16 | 219 | "Fresh-context review agent" is jargon. Name the tool or command. |
