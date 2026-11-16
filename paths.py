"""Single home for every runtime-state path. Anchored to this file, so it works from any cwd."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

WATCHLIST_FILE = DATA_DIR / "watchlist.json"
SEEN_FILE = DATA_DIR / "seen_movies.json"
COMING_SOON_FILE = DATA_DIR / "coming_soon.json"
PROFILE_DIR = DATA_DIR / "browser_profile"
