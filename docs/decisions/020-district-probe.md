# 020 — District probe (2026-11-16)

## Result

District (`www.district.in`) loads in a normal browser without login and without a challenge.

| Request | Result |
|---|---|
| `/movies/` | 200, movie links `/movies/<slug>-movie-tickets-MV<id>` (default city New Delhi) |
| `/movies/avengers-doomsday-movie-tickets-in-bengaluru-MV176735` | 200, Bengaluru showtimes page: venues, times, formats, date strip |

## Findings

- Bangalore pages use `-in-bengaluru-` in the URL; `MV<id>` is the stable movie id.
- A not-yet-released movie (Avengers: Doomsday, release 17 Dec 2026) already listed venues and times for Dec 17 with the note "Advance bookings open for December". BookMyShow showed nothing for the same movie, so District can be the earlier signal.
- No JSON calls were tried; page HTML is enough.

## Decision

Build District watch-only alongside BookMyShow in M5, with the same signal ladder. Poll politely (5–10 minutes), back off on 403/429.
