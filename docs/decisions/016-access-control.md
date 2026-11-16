# 016 — Access control

Date: 2026-11-16 · Status: accepted · Source: planning session, full plan in [../plan.md](../plan.md)

**Decision:** The admin's Telegram user ID comes from settings. Anyone else who messages the bot gets an access-requested reply, and the admin approves or denies with buttons. Every update is checked by user ID. Button data carries record IDs, never list positions.

**Why:** Fixes the no-sender-check and stale-button bugs at the source and is the model phase C extends.

**Rejected:** Invite codes (possible later) and a hard-coded ID list (needs a restart per change).
