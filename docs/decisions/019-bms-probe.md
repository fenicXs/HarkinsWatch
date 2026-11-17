# 019 — BookMyShow probe (2026-11-16)

## Result

BookMyShow's **rendered HTML pages load in a normal, headed browser without login**, but its **JSON APIs and some pages are Cloudflare-blocked**. Plain HTTP or headless access is blocked outright.

| # | Request | Client | Result |
|---|---|---|---|
| 1 | `/explore/movies-bengaluru` | headless Chromium (bundled) | 403, Cloudflare "Attention Required!" challenge, no content |
| 2 | same | headed installed Chrome, fresh profile | 200, 12 movie links |
| 3 | `/explore/upcoming-movies-bengaluru` (second request in that session) | headed installed Chrome | 403 Cloudflare challenge |
| 4 | `/explore/movies-bengaluru` | Playwright MCP browser | 200, 28 distinct movie links |
| 5 | `/movies/bengaluru/avengers-doomsday/ET00439706` | Playwright MCP browser | 200 |

Seven requests in all, spaced out, well under the 10 per minute budget.

## What the pages expose

- Movie list page: links of the form `/movies/bengaluru/<slug>/ET<id>`. `ET<id>` is the stable movie id (the slug can change).
- Movie page: shows `Releasing on 17 Dec, 2026`, an `I'm interested` counter (673K+), formats, languages, and a `Book tickets` button, even for a movie releasing a month away. Clicking `Book tickets` produced no visible change in a plain click; the booking flow beyond this was not explored (it may need sign-in or a format and language pick).
- JSON-LD `Movie` block on the movie page with `datePublished` (a release-date signal).
- The site's own JSON calls (`/api/explore/v1/discover/...`, `/api/v1/mobile/secondary/init`) returned 403 even inside a real browser. Only the static `assets-in.bmscdn.com/discovery-catalog/response-cache/regions.json` returned 200. These are not usable under the "public calls only" rule.

## Findings that affect the design

- Access works only through a real headed browser with human-like pacing, and Cloudflare still challenged one of the first requests in a session (request 3). A polling loop at the planned 30-minute and 1-minute intervals would likely hit challenges often.
- Per the plan: no CAPTCHA solving, no stealth, no proxy rotation. A challenge is treated like a 403/429: back off.
- The "listed" and "dates" signals (movie page appears, release date shows) are readable from the movie page HTML. The "venues" and "sessions" stages were not reached in the probe.

## Decision

- Do not build BookMyShow as the first Bangalore chain without an owner decision, because the blocking is unreliable for a polling bot.
- Probe District (020) and PVR INOX (021) next, as the plan's option A.

## Addendum: showtimes signal (found later the same day)

- Showtimes page: `/movies/bang/<slug>-bengaluru/buytickets/<ET id>/<YYYYMMDD>` (the old `/buytickets/.../movie-bang-<ET id>-MT/<date>` form redirects to it). Loads (200) in a normal browser, no login.
- **Tickets open** = the page lists venues with times (Ramayana: INOX, PVR, Cinepolis, AMB…, each with times and formats).
- **Not open** = the page says "Uh-oh! We couldn't find anything. There are no shows available for this date" (Avengers: Doomsday, checked 2026-11-16; District already listed Dec 17 sessions for it).
- The date strip shows the next 7 days only, and the page redirects a far-future date to today, so "open" is detected from the first 7 days.
- Updated decision: **build BookMyShow as watch-only** (owner confirmed, 2026-11-16: the owner books by hand). Poll slowly (about every 5–10 minutes), back off on any challenge or 403, never solve it.
