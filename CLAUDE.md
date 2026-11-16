# ShowWatch (repo: HarkinsWatch) — agent entry

Watches movie theatre chains (Harkins in Phoenix; BookMyShow, District, PVR INOX in Bangalore), alerts approved Telegram users when tickets open, and books seats where a chain allows it. Python 3.12, Postgres, Playwright, Docker. Rebuild in progress: see `docs/plan.md`.

This file only routes. Content lives in the files it points to.

## Where things live

| Path | What it is |
|---|---|
| `src/showwatch/` | The new app: `core`, `domain`, `db`, `chains`, `llm`, `bot`, `worker` |
| `migrations/`, `alembic.ini` | Alembic migrations |
| `tests/` | `unit/`, `integration/` (needs `TEST_DATABASE_URL`), `fixtures/<chain>/` |
| `scripts/` | Manual tools (site probes, smoke runs); never run in CI |
| `docker/Dockerfile`, `compose.yaml` | Images (`bot`, `worker`) and the stack (`postgres`, `migrate`, `bot`, `worker`) |
| `legacy/` | The old flat bot, runnable in its own venv until M4 deletes it. See `legacy/CONTEXT.md` |
| `docs/plan.md` | The approved plan: milestones M0–M7, schema (Appendix A), job catalogue (Appendix B) |
| `docs/decisions/` | One short file per decision |
| `docs/review/` | The plan's review history |

## Where to go for task X

| Task | Read first |
|---|---|
| Build or check a milestone | `docs/plan.md` → that milestone's file table and "Done when" |
| Change the schema | `docs/plan.md` → Appendix A, then add a new migration |
| Add or change a job kind | `docs/plan.md` → Appendix B |
| Understand why something was chosen | `docs/decisions/` |
| Port something from the old bot | `legacy/CONTEXT.md` |
| Run or check the stack | `docs/architecture.md` → "Running it" |

## Conventions

- Work happens on branch `revamp`. Never push, and never commit before the milestone's checks pass, without the user's confirmation.
- Only public data from chain sites: no login-only endpoints, no CAPTCHA solving, no fingerprint stealth, no proxy rotation. Back off when a site pushes back.
- Never print or commit secrets. `.env` is gitignored; `.env.example` lists key names only. Compare key names, never values.
- Only code in `src/showwatch/db/` writes SQL. Telegram button data carries record IDs and is at most 64 bytes.
- Benchmark and status numbers live only in `README.md` → Results.
- Before each commit run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`.
