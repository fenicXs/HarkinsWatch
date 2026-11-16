# 015 — Insider detection

Date: 2026-11-16 · Status: accepted · Source: planning session, full plan in [../plan.md](../plan.md)

**Decision:** Detect the earliest public signals: movie listed, dates, venues, sessions. Poll faster (60 s) per watched movie once its stage reaches listed, until sessions appear or 72 h pass. Only public pages and public JSON calls; no non-public endpoints, no CAPTCHA solving, no stealth fingerprinting, no proxy rotation. The user accepts the risk that BookMyShow's terms forbid automated access.

**Why:** Being first to know is the value of the product, and per-movie fast polling keeps the overall request rate low.

**Rejected:** Using non-public or leaked endpoints, or getting around bot protection.
