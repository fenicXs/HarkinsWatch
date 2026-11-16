"""Vision-assisted seat picking via Google Gemini (free tier).

Used as a fallback when the deterministic seat picker fails — e.g. large IMAX maps
where the canvas seats and the DOM buttons don't align, and where Harkins doesn't
mark unavailable seats as disabled (so only vision can tell available from taken).
"""
import os
import re
import time
import base64
import json
import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = "gemini-flash-latest"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

_PROMPT = (
    "This is a cinema seat-selection map. Gold/amber icons (or red rocker icons) are AVAILABLE seats. "
    "Grey helmet icons, blue squares, and wheelchair icons are NOT selectable. "
    "Return up to 20 points, each on a DIFFERENT available seat, concentrated near the CENTER of the "
    "available seating and spread across a few adjacent columns and rows there. "
    "Return ONLY a JSON array of [y, x] points normalized 0-1000 relative to THIS image."
)


def _gemini_points(png_bytes):
    """Ask Gemini for available-seat points; returns list of [y, x] (0-1000) or []."""
    if not API_KEY:
        return []
    body = {"contents": [{"parts": [
        {"text": _PROMPT},
        {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png_bytes).decode()}},
    ]}]}
    for attempt in range(2):
        try:
            r = requests.post(API_URL, headers={"x-goog-api-key": API_KEY}, json=body, timeout=60)
            data = r.json()
            cands = data.get("candidates")
            if not cands:                                   # error / rate-limited / blocked
                print("Gemini no candidates:", str(data)[:200])
                time.sleep(4)                               # back off then retry once
                continue
            parts = cands[0].get("content", {}).get("parts", [])
            text = next((p["text"] for p in parts if "text" in p), "")
            match = re.search(r"\[\s*\[.*\]\s*\]", text, re.S)
            if match:
                return json.loads(match.group(0))
        except Exception as e:
            print("Gemini error:", e)
            time.sleep(2)
    return []


def _selected(page):
    return set(page.eval_on_selector_all('button[aria-label^="Remove Seat"]', "els=>els.map(e=>e.id)"))


def pick_seats_by_vision(page, num_seats=2):
    """Find `num_seats` adjacent available seats near the centre and select them.

    Returns the list of selected seat ids (left→right) on success (seats left selected),
    or None. Only works on the canvas seat map.
    """
    canvas = page.locator('canvas[aria-label="Theatre seat map"]')
    if not canvas.count():
        return None
    box = canvas.bounding_box()
    if not box:
        return None

    points = _gemini_points(canvas.screenshot())
    if not points:
        return None

    # Click each point; record which real seat it selected + the exact click position.
    confirmed = {}   # id -> (row, num, screen_x, screen_y)
    before = _selected(page)
    for pt in points:
        try:
            y, x = pt[0], pt[1]
        except (TypeError, IndexError):
            continue
        sx = box["x"] + (x / 1000) * box["width"]
        sy = box["y"] + (y / 1000) * box["height"]
        page.mouse.click(sx, sy)
        page.wait_for_timeout(250)
        new = _selected(page) - before
        if len(new) == 1:
            sid = list(new)[0]
            m = re.match(r"([A-Z]+)(\d+)", sid or "")
            if m:
                confirmed[sid] = (m.group(1), int(m.group(2)), sx, sy)
            page.mouse.click(sx, sy)   # deselect so we start clean
            page.wait_for_timeout(100)
        before = _selected(page)

    if len(confirmed) < num_seats:
        return None

    # Centroid of confirmed available seats.
    cx = sum(v[2] for v in confirmed.values()) / len(confirmed)
    cy = sum(v[3] for v in confirmed.values()) / len(confirmed)

    # Find the run of `num_seats` consecutive same-row confirmed seats closest to the centroid.
    best, best_dist = None, float("inf")
    for sid, (row, num, sx, sy) in confirmed.items():
        run = [(sid, sx, sy)]
        n = num
        while len(run) < num_seats:
            nxt = next((s for s, (r, nn, _, _) in confirmed.items() if r == row and nn == n + 1), None)
            if not nxt:
                break
            run.append((nxt, confirmed[nxt][2], confirmed[nxt][3]))
            n += 1
        if len(run) == num_seats:
            mx = sum(p[1] for p in run) / num_seats
            my = sum(p[2] for p in run) / num_seats
            d = ((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5
            if d < best_dist:
                best_dist, best = d, run
    if not best:
        return None

    # Click the chosen seats at their known-good positions.
    for _, sx, sy in best:
        page.mouse.click(sx, sy)
        page.wait_for_timeout(250)
    ids = [r[0] for r in best]
    if all(i in _selected(page) for i in ids):
        return ids
    return None
