# 022 – Harkins seat hold needs no login

**Date:** 2026-11-16 · **Milestone:** M4 pre-check (a)

**Question:** does holding seats on a Harkins session require a logged-in profile?

**Probe:** a fresh, logged-out Chromium (Playwright MCP, no saved profile) opened a live Arizona Mills session page (theatre 16).

| Step | Result |
|---|---|
| Seat page loads | yes; canvas `Theatre seat map` present, 119 selectable seat buttons |
| Select two adjacent seats (I-5, I-6) | yes; both `aria-pressed`, labels became `Remove Seat …` |
| Ticket-type step | yes; `ticketing-type-picker-add-*` buttons present, 2 Adult added |
| `Continue to Checkout` | enabled; navigated to the checkout page with the two seats listed and an email field (guest checkout) |
| Payment | not attempted; the browser was closed, so the hold lapses on its own |

**Notes**
- Normal pointer clicks on the seat buttons time out because the canvas overlay intercepts pointer events. Clicks through the DOM (`element.click()`) work. The legacy booker already handles this; keep that behaviour in the port.
- A "sign in" string appears somewhere on the checkout page. It is optional: the page offers email entry without it.

**Decision:** no login is required. The noVNC `login` compose profile (plan C8) and `scripts/harkins_login.py` are not built. The worker still uses a persistent profile at `/profiles/harkins`, only so a session cookie survives between jobs.
