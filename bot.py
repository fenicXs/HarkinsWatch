"""Telegram bot: add movies, pick showtimes, auto-book 2 seats, send payment link."""
import os
import json
import requests
import time
import datetime
from tracker import refresh_coming_soon, find_watchlisted_now_showing, remove_from_watchlist, save_seen
from dotenv import load_dotenv
from add_watch import find_matches, add_title
from scraper import get_now_playing, get_coming_soon, get_showtimes
from booker import book_session

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API = f"https://api.telegram.org/bot{TOKEN}"

sessions = {}                 # short id -> showtime url
current_movie_url = None      # movie whose dates we're picking
pending_movies = {}   # short id -> movie title, for "Book seats" buttons
date_shows = {}       # format name -> list of shows (for the current date)


def send(text):
    try:
        requests.post(f"{API}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": text}, timeout=30)
    except Exception as e:
        print("send failed:", e)


def get_updates(offset):
    try:
        r = requests.get(f"{API}/getUpdates",
                         params={"offset": offset, "timeout": 30}, timeout=40)
        return r.json().get("result", [])
    except Exception as e:
        print("getUpdates failed:", e)
        time.sleep(5)
        return []
def send_buttons(text, buttons):
    """Send a message with inline buttons."""
    kb = {"inline_keyboard": [[{"text": l, "callback_data": d}] for l, d in buttons]}
    try:
        requests.post(f"{API}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": text, "reply_markup": json.dumps(kb)}, timeout=30)
    except Exception as e:
        print("send_buttons failed:", e)

def offer_dates(movie_title, movie=None):
    """Remember the movie's url, send the next 7 dates as buttons."""
    global current_movie_url
    if movie is None:
        movie = next((m for m in get_now_playing() + get_coming_soon()
                      if m["title"] == movie_title), None)
    if not movie:
        send(f"Couldn't find {movie_title}.")
        return
    current_movie_url = movie["url"]
    today = datetime.date.today()
    buttons = []
    for i in range(7):
        d = today + datetime.timedelta(days=i)
        buttons.append((d.strftime("%a %b %d"), f"date:{d.isoformat()}"))
    send_buttons(f"Pick a date for {movie_title}:", buttons)


def _send_time_buttons(shows):
    """Send the given showtimes as tappable booking buttons."""
    buttons = []
    for i, s in enumerate(shows):
        sessions[str(i)] = s["url"]
        buttons.append((s["time"], f"book:{i}"))
    send_buttons("Tap a showtime to book 2 seats:", buttons)


def handle_date(date_str):
    """Scrape that date's showtimes; ask screen type first if there's more than one."""
    global date_shows
    if not current_movie_url:
        send("Search for a movie first.")
        return
    shows = get_showtimes(f"{current_movie_url}/{date_str}")
    if not shows:
        send("No Arizona Mills showtimes that day.")
        return
    date_shows = {}
    for s in shows:
        date_shows.setdefault(s["format"], []).append(s)
    if len(date_shows) == 1:                       # one format → times directly
        _send_time_buttons(next(iter(date_shows.values())))
    else:                                          # multiple → ask format first
        buttons = [(fmt, f"fmt:{i}") for i, fmt in enumerate(date_shows)]
        send_buttons("Pick a screen type:", buttons)


def handle_format(idx):
    """Send the showtimes for the chosen screen type."""
    formats = list(date_shows)
    i = int(idx)
    if 0 <= i < len(formats):
        _send_time_buttons(date_shows[formats[i]])


def handle_book(idx):
    url = sessions.get(idx)
    if not url:
        send("That showtime expired — search again.")
        return
    send("Booking the best seats for you... hang on.")
    result = book_session(url)
    if result is None:
        send("Booking failed — try again.")
    elif result.get("manual"):
        send(f"Couldn't auto-pick seats for this screen — choose 2 yourself here:\n{result['url']}")
    else:
        send(f"Seats {result['seats']} held! Pay here:\n{result['url']}")


CHECK_INTERVAL = 1800   # check every 30 min


def run_watch_check():
    """Refresh caches; notify (with Book button) for watchlisted movies now playing."""
    try:
        refresh_coming_soon()
        current = get_now_playing()
        save_seen({m["title"] for m in current})
        for movie in find_watchlisted_now_showing(current):
            mid = str(len(pending_movies))
            pending_movies[mid] = movie["title"]
            send_buttons(f"🎬 '{movie['title']}' is now playing at Arizona Mills!",
                         [("🎟 Book seats", f"pick:{mid}")])
            remove_from_watchlist(movie["title"])
    except Exception as e:
        print("watch check error:", e)


def main():
    print("Bot listening...")
    offset = 0
    pending = []
    last_check = 0
    while True:
        if time.time() - last_check > CHECK_INTERVAL:   # startup + every 30 min
            run_watch_check()
            last_check = time.time()
        for update in get_updates(offset):
            offset = update["update_id"] + 1

            # Button tap
            if "callback_query" in update:
                cq = update["callback_query"]
                requests.post(f"{API}/answerCallbackQuery",
                              data={"callback_query_id": cq["id"]})
                data = cq.get("data", "")
                if data.startswith("pick:"):
                    title = pending_movies.get(data.split(":", 1)[1])
                    if title:
                        offer_dates(title)
                elif data.startswith("date:"):
                    handle_date(data.split(":", 1)[1])
                elif data.startswith("fmt:"):
                    handle_format(data.split(":", 1)[1])
                elif data.startswith("book:"):
                    handle_book(data.split(":")[1])
                continue

            # Text message
            text = update.get("message", {}).get("text", "").strip()
            if not text:
                continue
            print("Received:", text)

            if text.isdigit() and pending:
                i = int(text)
                if 1 <= i <= len(pending):
                    title = pending[i - 1]
                    movie = next((m for m in get_now_playing() if m["title"] == title), None)
                    if movie is None:                        # not now showing
                        movie = next((m for m in get_coming_soon() if m["title"] == title), None)
                        if movie:
                            add_title(title)                 # keep on watchlist for auto-notify
                    if movie:
                        send(f"'{title}' — pick a date:")
                        offer_dates(title, movie)            # try to book (advance or now)
                    else:
                        send("Couldn't find that movie.")
                pending = []
            else:
                pending = find_matches(text)
                if pending:
                    lines = [f"{i}. {t}" for i, t in enumerate(pending, 1)]
                    send("Reply with a number to add:\n" + "\n".join(lines))
                else:
                    send("No Harkins movie found matching that.")



if __name__ == "__main__":
    main()
