"""Entry point: check for new Harkins movies and email an alert if any appear."""
from emailer import send_email
from telegram import send_telegram
from scraper import get_now_playing
from tracker import find_new_movies, refresh_coming_soon, find_watchlisted_now_showing, remove_from_watchlist
from booker import book_movie



def main():
    refresh_coming_soon()
    current = get_now_playing()          # scrape Now Showing once

    # Watchlisted movies that are now bookable
    ready = find_watchlisted_now_showing(current)
    for movie in ready:
        msg = f"'{movie['title']}' is NOW SHOWING — ready to book!\n{movie['url']}"
        print(msg)
        try:
            send_telegram(msg)
        except Exception as e:
            print("Telegram failed:", e)
        book_movie(movie["url"])
        remove_from_watchlist(movie["title"])


    # Optional: still alert on any brand-new movie
    new_movies = find_new_movies(current)
    if not new_movies:
        print("No new movies.")



if __name__ == "__main__":
    main()
