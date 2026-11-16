# 002 — Bangalore scope

Date: 2026-11-16 · Status: accepted · Source: planning session, full plan in [../plan.md](../plan.md)

**Decision:** Bangalore chains start as watch-and-notify only. Each chain module declares its capabilities (WATCH, SHOWTIMES, SIGNALS, BOOK); booking is added per site later.

**Why:** In India the need is to know the instant tickets open. BookMyShow and District sit behind anti-bot protection and phone-OTP login, and their terms forbid automated booking.

**Rejected:** Auto-holding seats on every Bangalore site now: fragile, and the most likely way to get an account banned.
