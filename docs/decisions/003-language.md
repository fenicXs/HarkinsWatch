# 003 — Language

Date: 2026-11-16 · Status: accepted · Source: planning session, full plan in [../plan.md](../plan.md)

**Decision:** Python 3.12 for the bot, worker and API. A future web frontend is a separate TypeScript app that talks to a FastAPI backend.

**Why:** The Harkins seat logic already works in Python, and Playwright is well supported there.

**Rejected:** A full TypeScript rewrite: throws away working code for no gain.
