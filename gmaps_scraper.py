"""
Google Maps scraper — business listings (naam, address, phone, website, rating).
Lead-gen ke liye. Playwright (sync) use karta hai.

Usage:
    python gmaps_scraper.py "cattle feed dealers in Gujarat"
    python gmaps_scraper.py "cattle feed dealers in Ahmedabad" --max 100 --headed

Output: results CSV (search term ke naam se) usi folder mein.

NOTE: Google Maps scraping Google ki ToS ke khilaf hai. Block/CAPTCHA aa sakta hai.
Apne use ke liye, dheere chalao. Bulk/commercial ke liye Places API behtar hai.
"""
import argparse
import csv
import re
import sys
import time

# ponytail: Windows console cp1252 Gujarati/emoji naam encode nahi kar pata -> crash.
# stdout ko utf-8 (replace) pe set karo taaki print kabhi na toote.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ponytail: selectors Google ke current DOM pe based hain. Google DOM badle to ye
# toot sakte hain — uss waqt browser inspect karke class naam update karna.
SEL = {
    "feed": 'div[role="feed"]',
    "card_link": 'a.hfpxzc',                       # har listing ka link
    "name": 'h1.DUwDvf',
    "rating": 'div.F7nice span[aria-hidden="true"]',
    "reviews": 'div.F7nice span[aria-label]',
    "address": 'button[data-item-id="address"]',
    "phone": 'button[data-item-id^="phone"]',
    "website": 'a[data-item-id="authority"]',
    "category": 'button[jsaction*="category"]',
}


def text_or_blank(page, selector):
    el = page.query_selector(selector)
    if not el:
        return ""
    # ponytail: button text mein icon + newline aata hai -> whitespace collapse
    return re.sub(r"\s+", " ", el.inner_text()).strip()


def attr_or_blank(page, selector, attr):
    el = page.query_selector(selector)
    return (el.get_attribute(attr) or "").strip() if el else ""


def scroll_feed(page, max_results):
    """Results panel ko scroll karo jab tak list khatam ya max_results na ho."""
    feed = page.query_selector(SEL["feed"])
    if not feed:
        return
    stale = 0
    last = 0
    while stale < 4:
        links = page.query_selector_all(SEL["card_link"])
        if len(links) >= max_results:
            break
        page.eval_on_selector(SEL["feed"], "el => el.scrollBy(0, el.scrollHeight)")
        time.sleep(2.0)  # ponytail: fixed sleep; flaky ho to network-idle wait pe upgrade
        # "end of the list" marker
        if page.query_selector('span.HlvSq, p.fontBodyMedium span.HlvSq'):
            break
        count = len(page.query_selector_all(SEL["card_link"]))
        stale = stale + 1 if count == last else 0
        last = count


def scrape(query, max_results, headed):
    out_file = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_") + ".csv"
    rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        ctx = browser.new_context(
            locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()
        page.goto("https://www.google.com/maps/search/" + query.replace(" ", "+"),
                  wait_until="domcontentloaded", timeout=60000)

        # consent page (EU/first visit) — accept agar dikhe
        try:
            page.click('button[aria-label*="Accept"], button:has-text("Accept all")', timeout=4000)
        except PWTimeout:
            pass

        try:
            page.wait_for_selector(SEL["feed"], timeout=20000)
        except PWTimeout:
            print("Results feed nahi mila — query check karo ya CAPTCHA aaya hoga.", file=sys.stderr)
            browser.close()
            return

        scroll_feed(page, max_results)
        links = page.query_selector_all(SEL["card_link"])[:max_results]
        print(f"{len(links)} listings mile. Detail nikaal raha hu...")

        for i, link in enumerate(links, 1):
            try:
                link.click()
                page.wait_for_selector(SEL["name"], timeout=10000)
                time.sleep(0.8)
                rows.append({
                    "name": text_or_blank(page, SEL["name"]),
                    "rating": text_or_blank(page, SEL["rating"]),
                    "category": text_or_blank(page, SEL["category"]),
                    "address": text_or_blank(page, SEL["address"]),
                    "phone": text_or_blank(page, SEL["phone"]),
                    "website": attr_or_blank(page, SEL["website"], "href"),
                    "maps_url": page.url,
                })
                print(f"  [{i}/{len(links)}] {rows[-1]['name']}")
            except PWTimeout:
                print(f"  [{i}] skip (timeout)", file=sys.stderr)
                continue

        browser.close()

    with open(out_file, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["name", "rating", "category", "address",
                                          "phone", "website", "maps_url"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nDone: {len(rows)} rows -> {out_file}")


def _demo():
    # ponytail: parser ka self-check, network ke bina. CSV-naam logic verify karta hai.
    name = re.sub(r"[^a-z0-9]+", "_", "Cattle Feed Dealers in Gujarat".lower()).strip("_")
    assert name == "cattle_feed_dealers_in_gujarat", name
    print("demo OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", help='e.g. "cattle feed dealers in Gujarat"')
    ap.add_argument("--max", type=int, default=60, help="max listings (default 60)")
    ap.add_argument("--headed", action="store_true", help="browser dikhao (debug)")
    ap.add_argument("--demo", action="store_true", help="offline self-check")
    a = ap.parse_args()
    if a.demo or not a.query:
        _demo()
        if not a.query:
            print('Usage: python gmaps_scraper.py "cattle feed dealers in Gujarat"')
    else:
        scrape(a.query, a.max, a.headed)
