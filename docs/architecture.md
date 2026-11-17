# Architecture

Summary of [plan.md](plan.md). The plan is the source of truth for details; decisions live in [decisions/](decisions/).

## Processes

```
Telegram ⇄ bot ──writes──▶ jobs table ◀──claims── worker ──▶ chain sites (Playwright / HTTP)
              │                                        │
              └───────── Postgres (users, watches, movies, showtimes, signals, alerts) ──┘
                                                       └──▶ Telegram Bot API (alerts, booking results)
```

| Process | Does | Never does |
|---|---|---|
| `bot` | Handles Telegram updates; creates job rows for slow work | Scrape, open a browser |
| `worker` | Schedules checks, claims jobs, scrapes, books, sends alerts through `notify` | Handle Telegram updates |
| `migrate` | One-shot `alembic upgrade head` before the others start | Anything else |

## Chains

Each chain (Harkins, BookMyShow, District; PVR INOX was dropped, decision 021) is a module that declares what it can do: `WATCH`, `SHOWTIMES`, `SIGNALS`, `BOOK`. The bot, watchlists and alerts never know which chain they talk to. Calling something a chain cannot do raises `CapabilityNotSupported`.

## Insider detection

A watched movie moves through `listed → dates → venues → sessions`. Each new stage alerts once per user. While a watch is in `listed`, `dates` or `venues`, it is polled at the fast interval (60 s); otherwise the base check runs every 30 minutes. All intervals are jittered, and each chain has a request budget and back-off (`chains/politeness.py`).

## Running it

| What | Command |
|---|---|
| Start everything | `docker compose up --build` |
| Postgres from this PC | `127.0.0.1:5433` |
| Checks | `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest` |
| Old bot | deleted after M4; last seen at git tag `legacy-last` (`git show legacy-last:legacy/CONTEXT.md`) |

## Where to look

| Question | File |
|---|---|
| Why was X decided | `docs/decisions/` |
| What each milestone builds and how it is verified | `docs/plan.md` |
| Review history of the plan | `docs/review/` |
