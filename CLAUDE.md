# HarkinsWatch — agent entry

Telegram bot that watches Harkins Theatres (Arizona Mills, theatre id 16) and auto-holds 2 seats, returning a payment link. Python + Playwright; Gemini vision as a seat-picking fallback.

This file only routes. Content lives in the files it points to.

## Where things live

| Path | What it is |
|---|---|
| `bot.py` | Entry point (`python bot.py`). Telegram polling loop + button flow |
| `scraper.py`, `tracker.py`, `add_watch.py` | Scrape Harkins; diff/cache movie lists; fuzzy title match + watchlist |
| `booker.py`, `seat_ai.py` | Seat-map booking (DOM picker, then Gemini fallback) |
| `telegram.py` | Standalone single-message sender |
| `paths.py` | The only home for data-file and browser-profile paths |
| `data/` | Runtime state (JSON caches, watchlist, browser profile) |
| `tools/` | Dev-only scripts, never imported by the bot |
| `docs/procedure.md` | Owner's build log, in order |
| `README.md` | Human-facing overview, setup, benchmark results |

## Where to go for task X

| Task | Read first |
|---|---|
| Understand what a module reads/writes, or what a change hits | `CONTEXT.md` |
| Anything touching JSON state or the login profile | `data/CONTEXT.md`, then `paths.py` |
| Change the Telegram conversation flow | `CONTEXT.md` → bot.py section |
| Change seat selection | `CONTEXT.md` → booker.py / seat_ai.py sections |
| Harkins site changed (selectors broke) | `CONTEXT.md` → scraper.py section; `tools/` to re-explore |
| Setup / run instructions | `README.md` → Setup |

## Conventions

- New state paths go in `paths.py`; never hardcode a cwd-relative filename in a module.
- Benchmark/status numbers live only in `README.md` → Results; don't copy them here.
