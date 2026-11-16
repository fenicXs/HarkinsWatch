# 001 — Growth path

Date: 2026-11-16 · Status: accepted · Source: planning session, full plan in [../plan.md](../plan.md)

**Decision:** Build for a small multi-user bot (B) from day one, starting as a personal bot (A). A public product (C) comes later and reuses the same database and job queue.

**Why:** The current code assumes one user everywhere (shared state in bot.py, a single CHAT_ID). That is the root of the stale-button and no-sender-check bugs.

**Rejected:** Designing for a public product first: it is a different project and raises terms-of-service and anti-bot risk.
