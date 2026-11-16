"""Opens a movie's showtimes; you pick the time, it picks 2 center seats."""
import re
from playwright.sync_api import sync_playwright
from telegram import send_telegram
from seat_ai import pick_seats_by_vision
from paths import PROFILE_DIR
NUM_SEATS = 2


def _split_regions(seats):
    """Split into [back, front] by the aisle (largest row-gap); one region if no clear split."""
    ys = sorted({s["y"] for s in seats})
    if len(ys) < 3:
        return [seats]
    gaps = [(ys[i + 1] - ys[i], ys[i]) for i in range(len(ys) - 1)]
    max_gap, split_after = max(gaps)
    median_gap = sorted(g[0] for g in gaps)[len(gaps) // 2]
    if max_gap > median_gap * 1.6:                     # a real aisle separation
        back = [s for s in seats if s["y"] > split_after]
        front = [s for s in seats if s["y"] <= split_after]
        return [back, front]                           # P1 = back, P2 = front
    return [seats]


def _region_pairs(region):
    """Consecutive same-row pairs, nearest-to-centroid first."""
    if not region:
        return []
    cx = sum(s["x"] for s in region) / len(region)
    cy = sum(s["y"] for s in region) / len(region)
    by_row = {}
    for s in region:
        by_row.setdefault(s["row"], []).append(s)
    ranked = []
    for rs in by_row.values():
        rs.sort(key=lambda s: s["num"])
        for a, b in zip(rs, rs[1:]):
            if b["num"] == a["num"] + 1:
                mx = (a["x"] + b["x"]) / 2
                dist = ((mx - cx) ** 2 + (a["y"] - cy) ** 2) ** 0.5
                ranked.append((dist, [a, b]))
    ranked.sort(key=lambda t: t[0])
    return [p[1] for p in ranked]


def candidate_pairs(seats):
    """All pairs, Priority 1 (back, center-out) first, then Priority 2 (front)."""
    pairs = []
    for region in _split_regions(seats):
        pairs.extend(_region_pairs(region))
    return pairs

def _finalize(page):
    """Set the ticket count to NUM_SEATS and return True once checkout is enabled."""
    checkout = page.locator('button:has-text("Continue to Checkout")')
    add = page.locator('[data-testid^="ticketing-type-picker-add-"]:not([disabled])').first
    if add.count() and not checkout.is_enabled():
        for _ in range(NUM_SEATS):
            add.click()
            page.wait_for_timeout(200)
    for _ in range(15):
        if checkout.is_enabled():
            return True
        page.wait_for_timeout(200)
    return checkout.is_enabled()


def _select_deterministic(page):
    """Read DOM seats and try to select 2 central adjacent seats by clicking.
    Returns [id, id] with tickets set & checkout enabled, or None."""
    raw = page.eval_on_selector_all(
        'button[aria-label^="Select Seat"]:not([disabled])',
        "els => els.map(e => ({id:e.id, x:parseFloat(e.getAttribute('x')), y:parseFloat(e.getAttribute('y'))}))")
    seats = []
    for s in raw:
        m = re.match(r"([A-Z]+)(\d+)", s["id"] or "")
        if m:
            seats.append({"id": s["id"], "row": m.group(1), "num": int(m.group(2)), "x": s["x"], "y": s["y"]})

    select_fails = 0
    for pair in candidate_pairs(seats)[:8]:              # cap attempts for speed
        _clear_selection(page)
        if not all(_select_seat(page, s["id"]) for s in pair):
            select_fails += 1
            if select_fails >= 2:                        # clicks not landing (large/scaled map)
                _clear_selection(page)
                return None
            continue
        if _finalize(page):
            return [pair[0]["id"], pair[1]["id"]]
    return None


def book_session(session_url):
    """Headless: open seat map, select 2 central available seats, return payment info (or manual)."""
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(PROFILE_DIR, headless=False)
        page = browser.pages[0]
        page.goto(session_url)
        try:
            page.wait_for_selector('button[aria-label^="Select Seat"]', timeout=60_000)
        except Exception:
            browser.close()
            return {"manual": True, "url": session_url}
        page.wait_for_timeout(3000)   # let the seat map become interactive (React hydration)

        # Fast deterministic picker first; fall back to Gemini vision on hard maps (IMAX etc.)
        selected = _select_deterministic(page)
        if not selected:
            # Deterministic clicks corrupt the canvas state — reload for a clean slate before vision.
            page.goto(session_url)
            try:
                page.wait_for_selector('button[aria-label^="Select Seat"]', timeout=60_000)
            except Exception:
                browser.close()
                return {"manual": True, "url": session_url}
            page.wait_for_timeout(3000)
            vids = pick_seats_by_vision(page, NUM_SEATS)
            if vids and _finalize(page):
                selected = vids

        if not selected:
            _clear_selection(page)
            browser.close()
            return {"manual": True, "url": session_url}

        page.locator('button:has-text("Continue to Checkout")').click()
        page.wait_for_url("**/session/*/ticketing/*", timeout=30_000)
        result = {"seats": " + ".join(selected), "url": page.url}
        browser.close()
        return result



def _select_seat(page, sid):
    """Click a seat like a real user (force-click at its position); confirm it registered."""
    btn = page.locator(f'button#{sid}')
    for _ in range(3):
        try:
            btn.click(force=True, timeout=3000)
        except Exception:
            try:
                btn.dispatch_event("click")   # fallback
            except Exception:
                return False
        page.wait_for_timeout(300)
        try:
            if btn.get_attribute("aria-pressed") == "true":
                return True
        except Exception:
            return False
    return False

def _clear_selection(page):
    """Deselect every currently-selected seat (removes leftovers)."""
    for _ in range(8):
        pressed = page.locator('button[aria-pressed="true"]')
        if pressed.count() == 0:
            return
        pressed.first.dispatch_event("click")
        page.wait_for_timeout(200)



if __name__ == "__main__":
    r = book_session("https://harkins.com/ticketing/theatre/16/movie/HO00015450/session/568953/date/2026-07-23")
    print(r)
