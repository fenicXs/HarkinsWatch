# 005 — Rebuild approach

Date: 2026-11-16 · Status: accepted · Source: planning session, full plan in [../plan.md](../plan.md)

**Decision:** Clean rebuild: scaffold the new src/ package, then port the proven parts (seat-pair picker, Harkins selectors, vision fallback). The old bot lives in legacy/ and stays runnable until the new bot matches it.

**Why:** The core design changes (many users, many chains, async Telegram, a database), so refactoring in place would rewrite nearly every line anyway.

**Rejected:** Gradual in-place refactor: the single-user shared-state design fights every step.
