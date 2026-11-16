# 017 — Testing and CI

Date: 2026-11-16 · Status: accepted · Source: planning session, full plan in [../plan.md](../plan.md)

**Decision:** Unit, fixture (saved pages and screenshots), integration (Postgres through TEST_DATABASE_URL) and manual live smoke tests. CI on GitHub Actions: ruff, mypy, pytest with coverage, then build both images. Coverage of at least 85% on the logic modules listed in pyproject.toml, starting at M1.

**Why:** Today's code has no tests; each site breakage becomes a saved fixture and a failing test before the fix.

**Rejected:** Live tests in CI: slow, flaky, and they add ban risk.
