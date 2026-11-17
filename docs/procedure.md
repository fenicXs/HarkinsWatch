> History of the original single-file bot, deleted after M4 (last seen at git tag `legacy-last`).
> Its `venv` and `pip install -r requirements.txt` steps no longer apply: the app now uses uv and
> Docker (see the README).

# 1. Create the virtual environment
python -m venv venv

# 2. Activate it
venv\Scripts\Activate.ps1        # PowerShell
# venv\Scripts\activate.bat      # cmd.exe

# 3. Upgrade pip (optional but recommended)
python -m pip install --upgrade pip

# 4. Install dependencies
pip install -r requirements.txt   # if you have a requirements file

# 5. When finished, deactivate
deactivate

## Playwright installation into sandbox(lets us build anything in a new env without making changes into others)

# 1. Install the Playwright Python package into sandbox
pip install playwright

# 2. Download the browser binaries
playwright install

# Or install just one browser:
playwright install chromium

# Create requirements.txt
# Create test_setup.py(tests if the playwright can handle a website)
1. Starts Playwright
2. Launches a Chromium browser with no visible window (headless=True)
3. Opens a new page and goes to "https://example.com"
4. Prints the page's title
5. Closes the browser

# Create explore.py
explores the Harkins website, sees how the website looks and check all the selectors(buttons, links, boxes etc). 
page.locator("a")
reads data in DOM(Document Object Model) format(a tree structure)

# Create scraper.py 
this does data collection, it reads the website and scrapes all the movies title from the Harkins page
also scrapes the "Coming Soon" page movies
'get_showtimes' scrapes the Arizona Mills showtimes for a movie (skips shows that already passed)
each showtime also has its screen type (CINÉ XL, 3D, IMAX, Digital)

# Create tracker.py
this looks into the existing list of movies which was scraped previously and compare with the new list to know the new movies in the list
"seen_movies.json" and "coming_soon.json" are the memory of "tracker.py"

# Created the Telegram bot
1. search '@BotFather', send '/newbot'
2. Selected bot name - "HarkinsWatchBot", got link to open my bot and got bot token 
3. Searched '@userinfobot', texted "hi" got the chat-id
4. Added the token and chat id to '.env' file

# Created Telegram.py 
sends a message to my chat using the bot token + chat id
Telegram works over a simple web request (HTTP), and requests is the standard Python library for that. (Then update your requirements: pip freeze > requirements.txt.)

# Creating a add_watch.py
this creates a add to watchlist file "watchlist.json"
'difflib.get_close_matches' finds the closest match to the movie you are trying to add to watchlist
pick a number in the watchlist to add that movie into watchlist if the movie is in the "Coming Soon" page of the Harkins website. If not in coming soon, it proceeds with the booking right away.

# Create explore_booking.py
Opens a visible Chrome using your saved profile (launch_persistent_context) — so you stay logged in
Goes to the Harkins movies page
Waits in the terminal — you click around the browser freely
Each time you press Enter, it saves the current page's full HTML to dump_1.html, dump_2.html, …
Type q to quit

'requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 30})' constently checks if there are any messages from Telegram

# Get a Gemini API key (free)
1. go to "https://aistudio.google.com/apikey", sign in with Google
2. click "Create API key" (no card needed)
3. add "GEMINI_API_KEY=your_key" to the '.env' file

# Create booker.py
this books the seats on the Harkins seat map and returns the payment page link
picks 2 seats next to each other, closest to the center (uses centroid)
never books the front rows unless there is no other option (front is Priority 2)
tries the normal way first (clicking the seats directly)
if that fails (big IMAX maps), it uses AI (seat_ai.py) to find the seats
"browser_profile" folder keeps me logged in

# Create seat_ai.py
the seat map is a canvas, so the code can't tell which seats are free
Gemini AI looks at a screenshot and tells which seats are available
we click those seats to confirm, then pick 2 next to each other in the center

# Create bot.py (this is the main file to run)
this is the whole app, keeps running and listening to Telegram. get_updates(offset) keeps running in loop to check if there is any message from telegram.
1. text a movie name -> shows matches -> reply a number to pick
2. pick a date -> pick screen type -> pick a showtime
3. it books 2 best seats and sends the payment link
4. review and pay on the phone
also checks the watchlist every 30 mins, if a watchlisted movie is now showing it sends a "Book seats" button
run it with: python bot.py

# Added new-release alerts to bot.py's 30 min watch check
'find_new_movies' (in tracker.py) already existed but nothing ever called it
now run_watch_check() calls it too, sends a plain Telegram message for any movie that's new to "Now Showing"
had to remove the old standalone save_seen() call in run_watch_check since find_new_movies saves seen_movies.json itself, doing both would wipe the list before the diff could run