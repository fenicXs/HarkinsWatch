# 007 — Restructure commit as the base

Date: 2026-11-16 · Status: accepted · Source: planning session, full plan in [../plan.md](../plan.md)

**Decision:** Commit 083cf1c (data/, tools/, docs/, paths.py, agent docs) is the base for the revamp; M0 moves it into legacy/.

**Why:** It was verified (compile, imports from another folder, seat-pair logic, live scrape) except bot.py end to end.

**Rejected:** Dropping it: it makes the old bot easier to run and port from.
