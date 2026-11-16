# 006 — LLM layer

Date: 2026-11-16 · Status: accepted · Source: planning session, full plan in [../plan.md](../plan.md)

**Decision:** One OpenAI-compatible client with per-task endpoint, model, key and timeout. ASU RC gateway for phases A and B; Gemini as fallback and as the phase C default. Uses: seat-map vision, plain-language requests, cross-language title matching. Reasoning models stay off user-facing paths (3-7 minutes per call in trials). Self-repairing selectors are parked.

**Why:** A Gemini timeout caused the 0 of 3 IMAX result. The RC gateway offers vision models, but it is a university service and cannot be the phase C backend.

**Rejected:** A single hard-wired provider.
