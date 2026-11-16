# 010 — Database

Date: 2026-11-16 · Status: accepted · Source: planning session, full plan in [../plan.md](../plan.md)

**Decision:** PostgreSQL from day one. Start with an empty database.

**Why:** The bot, the worker and a future API write at the same time; Postgres avoids a later migration. The old watchlist was empty and the seen-movies list was never in the repository.

**Rejected:** SQLite first: cheap now, awkward once several processes write.
