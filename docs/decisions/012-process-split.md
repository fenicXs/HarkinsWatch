# 012 — Process split

Date: 2026-11-16 · Status: accepted · Source: planning session, full plan in [../plan.md](../plan.md)

**Decision:** Two containers from one codebase: bot (Telegram only) and worker (scheduler plus job runner). They communicate through a jobs table in Postgres using SELECT ... FOR UPDATE SKIP LOCKED. No Redis. Telegram long polling. An api container joins in phase C.

**Why:** A booking takes 12-144 s and must not block chat; a browser crash must not take the bot down.

**Rejected:** One async process: Playwright would share the bot's process. Redis plus a task queue: more services than this scale needs.
