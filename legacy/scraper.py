"""Scrapes movie lists from Harkins (Now Showing and Coming Soon)."""
from playwright.sync_api import sync_playwright

NOW_PLAYING_URL = "https://www.harkins.com/movies"
COMING_SOON_URL = "https://www.harkins.com/movies/coming-soon"


def scrape_movies(url):
    """Return a list of movies from the given Harkins page: {'title', 'url'}."""
    movies = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        links = page.locator("a[href^='/movies/']")
        for i in range(links.count()):
            link = links.nth(i)
            title = link.get_attribute("aria-label")
            href = link.get_attribute("href")
            if not title:
                continue
            movies.append({
                "title": title,
                "url": "https://www.harkins.com" + href,
            })
        browser.close()
    return movies


def get_now_playing():
    """Movies currently in theaters."""
    return scrape_movies(NOW_PLAYING_URL)


def get_coming_soon():
    """Upcoming movies not yet released."""
    return scrape_movies(COMING_SOON_URL)

ARIZONA_MILLS_ID = "16"

# Climb from a time link to its format group and read the format label (logo alt / heading / testid).
FORMAT_JS = """el => {
  let node = el;
  while (node) {
    if (node.className && String(node.className).includes('sc-d9066650-9')) {
      const img = node.querySelector('img[alt]');
      if (img && img.alt) return img.alt.trim();
      const h = node.querySelector('h3,h4');
      if (h && h.textContent) return h.textContent.trim();
      return (node.getAttribute('data-testid') || '').trim();
    }
    node = node.parentElement;
  }
  return '';
}"""


def get_showtimes(movie_url):
    """Return today's bookable Arizona Mills showtimes: [{'time', 'url', 'format'}]."""
    showtimes = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(movie_url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        links = page.locator(f'a[href*="/ticketing/theatre/{ARIZONA_MILLS_ID}/"]')
        for i in range(links.count()):
            link = links.nth(i)
            if "disabled" in (link.get_attribute("class") or ""):
                continue                      # showtime already passed
            fmt = link.evaluate(FORMAT_JS) or "Standard"
            showtimes.append({
                "time": link.inner_text().strip(),
                "url": link.get_attribute("href"),
                "format": fmt,
            })
        browser.close()
    return showtimes


if __name__ == "__main__":
    for s in get_showtimes("https://www.harkins.com/movies/moana"):
        print(s["time"], "->", s["url"])

