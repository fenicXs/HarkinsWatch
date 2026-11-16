# 013 — Repository layout

Date: 2026-11-16 · Status: accepted · Source: planning session, full plan in [../plan.md](../plan.md)

**Decision:** src/showwatch/ (core, domain, db, chains, llm, bot, worker), migrations/, tests/, scripts/, docker/, docs/, legacy/. No empty folders.

**Why:** Matches the plan's architecture; saved pages and screenshots become test fixtures.

**Rejected:** Keeping the flat layout.
