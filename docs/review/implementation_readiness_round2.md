# Goldfish Review Report (EGM Stage 9, Implementation Readiness, Round 2)

**Target:** `docs/plan.md` (lines 146-381: "## Detailed Implementation Plan" through "## Risks")

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `egm-implementation-readiness` (id prefix `ir`) | completed |
| `egm-codebase-details` (id prefix `cd`) | completed |
| `egm-style` (id prefix `style`) | completed |

## Severity totals

| Severity | Count |
| --- | --- |
| Blocker | 2 |
| Major | 24 |
| Nit | 21 |

Notes:
- Findings are deduplicated. "Also flagged by" lists the other reviewers that raised the same point.
- Codebase-details found no false statements about legacy code. It confirmed `bot.py:135`, the `requests` imports, `FORMAT_JS`, the `sc-d9066650-9` class, and the vision constants.
- The implementation-readiness reviewer did not read legacy `telegram.py`, `README.md` or the CONTEXT files. Codebase-details and style did confirm `telegram.py` imports `requests` (line 162 is accurate).

## Prioritized Findings (Blockers -> Major -> Nits)

### Blockers

#### ir-2-1 Per-chain concurrency rule cannot be enforced with the defined payloads - Severity: Blocker
* **Reviewer Lens:** `egm-implementation-readiness` (also flagged by `egm-codebase-details`)
* **Affected Scope:** lines 211, 214, 358-363 (and the `jobs` table at plan line 109)
* **Discrepancy Description:** Line 363 says `claim()` skips a limited job when another `running` job has the same chain "in its payload". The `book` payload (`{user_id, showtime_id, seats}`) and the `probe_signals` payload (`{watch_item_id}`) carry no chain. `check_chain` is "one per chain + city" but the rule compares chain only. The M1 test at line 214 cannot be written. Also undefined: `jobs.max_attempts` (column or code constant), whether job results are persisted (line 312 shows a "Result"), and what `n` means in "60 s x 2^n".
* **Rationale & Recommendation:** Add a `chain` (and `city`) to payloads or a `jobs.chain_id` column. State the exact skip predicate. Decide `max_attempts` storage, result persistence, and the back-off exponent base.

#### ir-2-2 Container login path for Harkins cannot work as written - Severity: Blocker
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 198, 290
* **Discrepancy Description:** Legacy uses a persistent headed profile and `CLAUDE.md` mentions a login profile, so pre-check (a) probably comes out "login needed". The plan's answer is `scripts/harkins_login.py`, "a headed login inside the worker container". The worker runs under `xvfb-run`, so the user has no visible display. No VNC/noVNC, credentials flow, or profile transfer is specified. Line 290 itself says a Windows profile cannot be reused on Linux.
* **Rationale & Recommendation:** Specify one mechanism: a VNC/noVNC port in compose, a credential-driven scripted login, or another route. Name the profile path and how the login result reaches `/profiles/harkins`.

### Majors

#### ir-2-3 Data model cannot produce what the flows need (snapshots, "new release", audience) - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 278, 280, Appendix A (347-352); plan lines 105, 110
* **Discrepancy Description:** `chain_snapshots` is "marked suspect" and "last good" is used (line 278), but no suspect/good column exists and Appendix A does not define the table. A zero-movie first run is undefined. Whether a suspect scrape still upserts `movies` is undefined (that would recreate the baseline-overwrite bug). "New release" is undefined against a cumulative `movies` table with `coming_soon` rows: does `coming_soon` to `now_showing` count, or a first insert straight to `now_showing`, or a reappearing title? The audience of `new_release:` alerts (all approved users or only matching city) is not stated. `movies` has no city link.
* **Rationale & Recommendation:** Add the snapshot flag and table definition. Define "new release" in terms of status transitions. State that suspect scrapes do not write `movies`. State the alert audience and how city scoping works.

#### ir-2-4 Appendix A lacks column-level detail - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 347-352, 239
* **Discrepancy Description:** No types, nullability or defaults for most columns (`jobs.run_after`, `attempts`, `locked_*`, `payload`, `evidence`). `normalized_title` normalization is undefined. No uniqueness on `watch_items` (legacy refuses duplicates, `add_watch.py` 78-80). The admin upsert (line 239) gives no `chat_id` source or nullability.
* **Rationale & Recommendation:** Give a column table per entity, the normalization function, a `watch_items` unique key, and the `chat_id` rule.

#### ir-2-5 Domain types and the Chain Protocol are left to the implementer - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 168, 276 (plan lines 85-87)
* **Discrepancy Description:** `MovieListing`, `ShowtimeListing`, `Signal`, `WatchItem`, `BookingResult` and similar have no fields, and dataclass-versus-pydantic is not stated. `list_showtimes(movie, day)` has no city parameter although line 276 says it "uses the user's city".
* **Rationale & Recommendation:** Specify the fields and signatures, including how the city or theatre reaches `list_showtimes`.

#### ir-2-6 Migrations are not wired into any container - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 199, 209
* **Discrepancy Description:** Nothing says who runs `alembic upgrade head` (entrypoint, one-shot compose service, or manual). M2's bot cannot start without the schema. `alembic.ini` and `script.py.mako` are not in the M1 file table.
* **Rationale & Recommendation:** Pick the mechanism and list the files.

#### ir-2-7 Compose and DSN details are unspecified - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 186, 199
* **Discrepancy Description:** `DATABASE_URL` must be `postgres:5432` in Compose and `localhost:5433` on the host, but both read one `.env` and the override mechanism is not stated. The driver prefix (`postgresql+asyncpg://`), `POSTGRES_*` variables, and healthcheck command are absent. Which settings are required for `bot` and `worker` to log `ready` with a sparse `.env` is not stated. Postgres 18 images expect the data volume at `/var/lib/postgresql`, and the mount path is not given.
* **Rationale & Recommendation:** Add a compose `environment:` override for the DSN host, the driver prefix, the Postgres variables, the healthcheck, the volume path, and required-versus-optional settings.

#### ir-2-8 Legacy `.env` handoff is missing - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness` (also flagged by `egm-codebase-details`)
* **Affected Scope:** line 181
* **Discrepancy Description:** The hand-copy list covers `seen_movies.json` and `browser_profile/` but not the ignored `.env`. Legacy `load_dotenv()` searches upward, so without `legacy/.env` it silently falls back to the repo-root `.env`. That file will hold the new bot's token (line 188, C2), so legacy would poll with the wrong bot.
* **Rationale & Recommendation:** State the copy step (key names only) and that the root `.env` holds only the new keys.

#### ir-2-9 Package, test and tooling files are missing from the file boundary - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 159-177, 301-304, 319
* **Discrepancy Description:** Missing: `__init__.py` files for subpackages, `tests/conftest.py`, `.dockerignore`, `alembic.ini`, ruff/mypy/pytest-asyncio settings, Dockerfile steps (uv sync form, entrypoints, user), exact `docs/decisions/001...018` filenames. The M4 table omits `README.md`, which line 319 edits.
* **Rationale & Recommendation:** Add the missing paths and config values so no file creation is an implementer decision.

#### ir-2-10 Dependencies and CI browser install are left open - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 142-144 (stack), 198, 200, 266
* **Discrepancy Description:** Versions are "pinned in M0 after checking the latest", which is an implementer decision. No OpenAI-compatible client library (`openai` or `httpx`) or Pillow is listed. The Playwright version must match the worker image tag. CI has no step to install Chromium, yet the `FORMAT_JS` fixture test needs a browser, and how it runs against saved HTML (for example `page.set_content`) is not stated.
* **Rationale & Recommendation:** List the dependencies and the pinning rule, add the CI browser install, and state how the fixture test loads HTML.

#### ir-2-11 Coverage gate cannot pass as configured - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 200, 228
* **Discrepancy Description:** `--cov-fail-under=85` applies from M0 over `domain/`, `db/`, `seatmap.py`, `llm/`, `scheduler.py`, `bot/handlers/`. No tests are listed for `bot/handlers/search.py`, `llm/client.py`, or unit coverage of repositories. The include/omit mechanism is not given. Handler tests use "a fake bot" but handlers call repositories (the only SQL code), so database use in those tests is unstated.
* **Rationale & Recommendation:** Phase the gate or list the tests, specify the include/omit config, and state whether handler tests use fakes or Postgres.

#### ir-2-12 Title-match port contradicts legacy and has no signature - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness` (also flagged by `egm-codebase-details`)
* **Affected Scope:** lines 227, 248
* **Discrepancy Description:** Legacy `find_matches` (`add_watch.py` 67-74) runs `difflib` only when there are zero substring hits, so the lists are never mixed. The plan says "substring first, then difflib" and tests "substring hits before fuzzy hits", which a faithful port cannot satisfy. Inputs (which `movies` rows, chain, city, statuses) and the return type are not defined.
* **Rationale & Recommendation:** Choose "fuzzy only if no substring match" and fix the test, or declare the combined list a behaviour change. Add the signature.

#### ir-2-13 M3 parity test is underspecified and would clobber real legacy state - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness` (also flagged by `egm-codebase-details`)
* **Affected Scope:** lines 263, 286
* **Discrepancy Description:** `find_new_movies` always calls `save_seen` (`tracker.py:41`) and the legacy functions read and write `legacy/data/*.json`, so the script would overwrite the owner's baseline. Both functions take lists of `{'title','url'}` dicts, and `scrape_movies` needs a live URL, so the fixture HTML cannot be fed in as described. The parity script is not in the M3 file table. New-side seeding (users, movies, snapshot, watch item) and alert capture (a fake `notify`) are not described. How "saved live pages" are captured is unstated, and the port must separate parsing from navigation (`scraper.py` 8-29).
* **Rationale & Recommendation:** Point `paths` at a temp dir or back up `legacy/data`, name the script and its path, describe seeding and capture, and require a parse-only function.

#### ir-2-14 Scheduler and sweep behaviour are underspecified - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 211, 259, 191 (plan lines 94, 106)
* **Discrepancy Description:** Not stated: which (chain, city) pairs are scheduled, how duplicate queued jobs are avoided, how "next due" survives restarts, how jitter is applied (into `run_after`?), who calls `sweep()` and how often, and what a swept `book` job (max 1 attempt) does. `watch_items.stage` transitions and when `fast_until` is set are undefined, as is when `movie_id` is matched for an unmatched watch.
* **Rationale & Recommendation:** Add a scheduler spec and a stage-transition table.

#### ir-2-15 Access-control behaviour has gaps - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 222, 232-239
* **Discrepancy Description:** "One reply on first `/start`" for `pending` needs persisted state, and no column exists. The response to an unknown user's non-`/start` message is not stated. Approve/deny callback data formats and the `app.py` status filter are not given.
* **Rationale & Recommendation:** Add the state column, the callback formats, and the filter rule.

#### ir-2-16 Telegram 64-byte `callback_data` limit at risk - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** line 282 (plan line 112)
* **Discrepancy Description:** `fmt:<movie_id>:<day>:<format>` embeds a free-text format name. A long name exceeds 64 bytes and Telegram rejects the whole keyboard. The plan's own rule is record IDs only.
* **Rationale & Recommendation:** Use an ID or index for formats and add a length test.

#### ir-2-17 `book` retry semantics are contradictory - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 291, 309, 360
* **Discrepancy Description:** Line 291 re-queues a job 60 seconds later on a locked profile, with no detection mechanism. Line 309 and Appendix B set `max_attempts = 1` with no retry.
* **Rationale & Recommendation:** Say a lock re-queue does not count as an attempt (and how it is detected), or say what happens when the profile is locked.

#### ir-2-18 Vision calibration configuration and tests are unspecified - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness` (also flagged by `egm-style`)
* **Affected Scope:** lines 195, 308, 317
* **Discrepancy Description:** The per-model `coord_order`/`scale` entry is not in the settings table and has no shape. Line 317 lists no `seat_vision` cases though `test_seat_vision.py` is a deliverable (needs regex parse, coordinate conversion, 2 attempts, fallback order), and there is no `test_llm_client.py`. Timeouts exist only for vision. Line 116 says "ordered fallback list" but settings has one `LLM_FALLBACK_*` triple. Line 308 says the timeout is "per provider" while line 195 gives one `TIMEOUT_S` default of 45. The 3-screenshot calibration has no procedure or acceptance criterion. The conditional "if a model returns pixel or [x, y]..." names no trigger.
* **Rationale & Recommendation:** Add the settings shape, the missing tests, the calibration acceptance rule, and reconcile the fallback and timeout wording.

#### ir-2-19 Polite-polling machinery has no home before M5 - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 194, 325 (plan line 52)
* **Discrepancy Description:** The request budget and 403/429 back-off are hard constraints for all chains but are built only in M5 step 3 for BMS. Harkins checks in M3 get none. The shared component's file and where back-off state lives (memory or DB) are not named.
* **Rationale & Recommendation:** Name the shared module and place it in M3, or state Harkins is exempt.

#### ir-2-20 M6 depends on storage and files that do not exist - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 333-337, 359
* **Discrepancy Description:** `intent:<request_id>` and "the stored request" need a table or column, and Appendix A has none (no `0002` migration listed). `ParsedRequest` carries a date range, part of day and format, but the `search_showtimes` payload is `{user_id, movie_id, day}`, so the mapping and any filter step are unstated. The free-text handler file is not listed. "Night 21-02" crosses midnight with no day-boundary rule.
* **Rationale & Recommendation:** Add the table, migration, payload mapping, handler file and midnight rule.

#### ir-2-21 M5-M7 cannot be executed from this plan - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 323-325, 329, 341
* **Discrepancy Description:** M5-M7 file tables, stage-detection rules and fixture names are deferred to a re-plan checkpoint. The deferral is documented, but the standard asks for an exact file list per phase. M5's done-when needs a real Bangalore opening, which cannot be scheduled. Style also notes no fallback is given if none occurs in the window.
* **Rationale & Recommendation:** Either accept the checkpoint as an explicit exception in the plan header, or add provisional file tables. Add a fallback done-when for M5 (for example a recorded-fixture replay).

#### cd-2-1 "Behaviour unchanged" for the booker port omits hardcoded constants and branches - Severity: Major
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** lines 295-296
* **Discrepancy Description:** The following live in `booker.py` and are not in the plan. Only the first 8 candidate pairs are tried (`candidate_pairs(seats)[:8]`). Two failed seat clicks abort the deterministic path. The seat id regex is `([A-Z]+)(\d+)`. DOM selectors are `button[aria-label^="Select Seat"]:not([disabled])`, `aria-pressed`, `ticketing-type-picker-add-*` and `Continue to Checkout`. Waits and counts: 60 s seat-map wait, 3000 ms hydration, 30 s `wait_for_url("**/session/*/ticketing/*")`, 15 x 200 ms checkout polling, 3 click retries, 8 iterations in `_clear_selection`. After a deterministic failure the page reloads before vision, and a failed reload returns the manual-link outcome.
* **Rationale & Recommendation:** Add these constants, or cite `legacy/booker.py` line ranges as the authoritative spec. (The seat-map and vision constants the plan does name were confirmed accurate.)

#### cd-2-2 Legacy watch and booking flow is changed without being stated - Severity: Major
* **Reviewer Lens:** `egm-codebase-details` (also flagged by `egm-implementation-readiness`)
* **Affected Scope:** lines 243, 280, 282
* **Discrepancy Description:** Legacy (`bot.py:176-190`) offers date buttons right after adding a coming-soon title to the watchlist, and for an already-playing title it offers dates without adding it. Line 243 says `/watch` only saves a row, and line 282 offers `pick:` only for titles already playing. Whether a coming-soon `/watch` also offers dates is undecided. Legacy builds the 7 dates from the PC's `date.today()` (`bot.py:60`), while the plan uses per-city time zones, so which "today" applies is unstated.
* **Rationale & Recommendation:** State the intended behaviour and the timezone source for "today".

#### style-2-1 Unverifiable process claims in Step 0 - Severity: Major
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 155, 286
* **Discrepancy Description:** "Applies 42 of its 44 fixes (the 2 declined are wording nits)" cannot be checked because the review reports are copied in later. "I edit `docs/plan.md` to resolve any later findings" is process narration already covered by the approval gate.
* **Rationale & Recommendation:** Cite the report path or drop the counts. Drop the narration.

#### style-2-2 First-person voice in a shared plan - Severity: Major
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 148, 154, 155
* **Discrepancy Description:** "I run", "I open a PR", "I edit" put the author's voice into a plan other readers execute. Line 154's "stays where it is" depends on a layout the reader cannot see.
* **Rationale & Recommendation:** Use imperative or passive voice or name the actor.

#### style-2-3 Done-when checks are vague or mixed - Severity: Major
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 286, 319, 329, 337
* **Discrepancy Description:** Line 286 joins two criteria in one sentence, and the zero-movie case needs a stated precondition (no prior snapshot falls under the first-run rule instead). Line 319 sets "5 of 5" for standard screens but "no pass mark" for IMAX, with no stated decision it informs, and it contrasts with the 0/3 baseline cited at plan line 13. Line 337 has no count or threshold ("passes on fixtures", "ends in a booking offer") and names no specific title variants.
* **Rationale & Recommendation:** Give each criterion its own measurable bullet. State what the IMAX result gates. Name the variants and thresholds.

### Nits

#### ir-2-22 `py_compile` command relies on shell globs - Severity: Nit
* **Reviewer Lens:** `egm-implementation-readiness` (also flagged by `egm-codebase-details`)
* **Affected Scope:** line 202
* **Discrepancy Description:** PowerShell does not expand `legacy/*.py`. Python receives the literal pattern.
* **Rationale & Recommendation:** Use `python -m compileall legacy` or name the shell.

#### ir-2-23 Milestone PR flow and Step 0 are ambiguous - Severity: Nit
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 148, 154-155
* **Discrepancy Description:** With one long-lived `revamp` branch and a PR to `main` per milestone, it is unclear whether `revamp` is rebased or merged after each. The source path of the two review reports is not given. Step 0 files have no Path | Action table.
* **Rationale & Recommendation:** State the branch handling, the source paths and a file table.

#### ir-2-24 Parse selector may catch `/movies/coming-soon`; duplicate titles across pages - Severity: Nit
* **Reviewer Lens:** `egm-implementation-readiness` (also flagged by `egm-codebase-details`)
* **Affected Scope:** lines 266, 273, 348
* **Discrepancy Description:** `a[href^='/movies/']` could match the coming-soon link and yield `external_id = coming-soon`. A title on both pages conflicts with one `movies.status` and the unique `(chain_id, external_id)`. Legacy does no de-duplication and has a `FORMAT_JS` fallback chain (`img[alt]`, then `h3/h4`, then `data-testid`) and a fixed 5000 ms wait that the plan omits. The `{movie_url}/{date}` URL is built in `bot.py:83`, not the scraper.
* **Rationale & Recommendation:** Add an exclusion rule and a precedence rule for titles on both pages, and carry over or drop the fallback chain explicitly.

#### ir-2-25 Dropped user-facing details in the search flow - Severity: Nit
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** line 282
* **Discrepancy Description:** Legacy's "No Arizona Mills showtimes that day." reply (`bot.py:85`) is not carried over, and the time button label format is unspecified.
* **Rationale & Recommendation:** State the empty-result reply and label format.

#### ir-2-26 `book:` buttons appear in M3 but the handler arrives in M4 - Severity: Nit
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** lines 262, 282, 300
* **Discrepancy Description:** Taps on `book:<showtime_id>` are silently ignored in M3.
* **Rationale & Recommendation:** Say so, or add a stub handler in M3.

#### ir-2-27 Legacy `telegram.py` shadows the `telegram` package - Severity: Nit
* **Reviewer Lens:** `egm-implementation-readiness` (also flagged by `egm-codebase-details`)
* **Affected Scope:** lines 181, 295-296
* **Discrepancy Description:** `booker.py:4` has an unused `from telegram import send_telegram`. The legacy venv must not install `python-telegram-bot`, and the port must drop that import.
* **Rationale & Recommendation:** Add one sentence to each place.

#### ir-2-28 `smoke_book.py` has no arguments or output defined - Severity: Nit
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** line 301
* **Discrepancy Description:** Inputs and expected output for the manual script are not given.
* **Rationale & Recommendation:** State arguments and what it prints.

#### ir-2-29 Redaction key matching case-sensitivity is unstated - Severity: Nit
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** line 197
* **Discrepancy Description:** Env names are upper case while structlog keys are usually lower case.
* **Rationale & Recommendation:** State case-insensitive matching and add a test case.

#### cd-2-3 `SEATS` differs from legacy `NUM_SEATS` - Severity: Nit
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** lines 296, 306, 316
* **Discrepancy Description:** Legacy is `NUM_SEATS = 2` (`booker.py:7`), and `seat_ai.pick_seats_by_vision` takes `num_seats`.
* **Rationale & Recommendation:** Keep the name or say the rename is intentional.

#### cd-2-4 `requests` has no pin and the legacy Python version is unnamed - Severity: Nit
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** line 162
* **Discrepancy Description:** Every other line in `legacy/requirements.txt` is pinned (for example `playwright==1.61.0`). The existing `__pycache__` files are `cpython-314`, while the new stack is 3.12.
* **Rationale & Recommendation:** Give the pin and the legacy Python version.

#### cd-2-5 Legacy file lists are grouped, not exact - Severity: Nit
* **Reviewer Lens:** `egm-codebase-details` (also flagged by `egm-style` on the "Modify" wording)
* **Affected Scope:** lines 160-161, 304
* **Discrepancy Description:** `legacy/ (all current *.py ...)` and a directory-level `Delete` do not give exact paths (legacy `tools/` holds `explore.py`, `explore_booking.py`, `test_setup.py`). The Action "Modify (`git mv`)" understates a move.
* **Rationale & Recommendation:** List exact paths and use "Move" as the action.

#### cd-2-6 Vision port details differ from legacy - Severity: Nit
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** lines 195, 308
* **Discrepancy Description:** The 20-point cap lives only in `_PROMPT` and is not enforced in `seat_ai.py`. Legacy timeout is a hardcoded 60 s and retry sleeps are 4 s and 2 s, so the plan's 45 s default is a change. Legacy reads Gemini's `candidates[0].content.parts`, and the plan does not state where text is read in an OpenAI-style response.
* **Rationale & Recommendation:** Flag the timeout as a change and state the response field.

#### cd-2-7 Booking failure behaviour differs from legacy and is not called out - Severity: Nit
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** lines 309-316
* **Discrepancy Description:** `bot.handle_book` checks `result is None`, which `book_session` never returns (it returns a dict or raises, and the exception escapes the polling loop). The plan's "failed = any exception" is a behaviour change. Legacy's "Booking the best seats for you... hang on." message is not carried over.
* **Rationale & Recommendation:** Note both as intentional changes.

#### style-2-4 Redundancy and filler - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 148, 150, 160, 162, 179, 363, 367-370
* **Discrepancy Description:** The pre-commit checks and "each milestone ends with a check" are stated at 148 and again at 367-370. Line 150 is a statement about the document that the tables already show. Line 363 repeats the concurrency rule from plan line 68. "Old bot kept runnable" (162) and "Details:" (179) are fragments.
* **Rationale & Recommendation:** Keep one copy in Verification and drop the filler.

#### style-2-5 Unverifiable Dockerfile purpose clause - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** line 198
* **Discrepancy Description:** "so a headed browser works in the container" has no check.
* **Rationale & Recommendation:** Add a verification step or drop the clause.

#### style-2-6 Unverified "rare" frequency claim - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** line 291
* **Discrepancy Description:** "the one-booking-per-chain rule makes this rare" is not substantiated.
* **Rationale & Recommendation:** Say "expected only when a prior job crashed" or drop it.

#### style-2-7 Risks table wording duplicates plan steps and overstates verification - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 377-381
* **Discrepancy Description:** "Verified at the start of M4" and "checked in M4" describe planned steps, not facts. Lines 380-381 repeat scope text from plan line 140 and C1.
* **Rationale & Recommendation:** Reword to "Checked in M4 step 1" and trim the duplicates.

#### style-2-8 Soft time box on the probe - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** line 323
* **Discrepancy Description:** "about 1 hour" has no stop condition.
* **Rationale & Recommendation:** Say "at most 1 hour".

#### style-2-9 Inconsistent back-off notation - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 325, 358-361
* **Discrepancy Description:** Appendix B uses "60 s x 2^n" and "30 s". M5 uses "5 minutes, doubling to a 60-minute cap".
* **Rationale & Recommendation:** Use one notation, and state caps and the meaning of `n`.

#### style-2-10 Parity done-when is a run-on - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** line 286
* **Discrepancy Description:** Four semicolon-chained actions in one sentence.
* **Rationale & Recommendation:** Convert to a numbered list (also see style-2-3 and ir-2-13).

#### style-2-11 Unverified restated facts - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 295, 308
* **Discrepancy Description:** The style reviewer flagged "behaviour unchanged" and the vision constants as unverified. Codebase-details confirmed the named constants are accurate (`Remove Seat`, canvas selector, `[y, x]` 0-1000, 2 attempts). The remaining gap is the unnamed constants in cd-2-1.
* **Rationale & Recommendation:** No separate action beyond cd-2-1 and cd-2-6.
