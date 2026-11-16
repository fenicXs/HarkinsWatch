# tools/ — dev-only scripts

Not imported by the bot. Run from the repo root, e.g. `python tools/test_setup.py`.

| Script | Purpose | Side effects |
|---|---|---|
| `test_setup.py` | Checks Playwright + Chromium can load a page | none |
| `explore.py` | Lists movie links on harkins.com/movies (selector discovery) | none |
| `explore_booking.py` | Opens visible Chromium with the bot's profile; Enter saves page HTML | writes `dump_N.html` to the current directory (gitignored) |
