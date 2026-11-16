# 011 — Postgres container

Date: 2026-11-16 · Status: accepted · Source: planning session, full plan in [../plan.md](../plan.md)

**Decision:** Own container, postgres:18-alpine, in Compose, with a named volume. Host port 5433 (bound to 127.0.0.1) because the native PostgreSQL 18 on this PC owns 5432.

**Why:** Keeps the app database separate from the user's other projects and reproducible on any machine.

**Rejected:** A new database on the native server: ties the app to this PC's setup.
