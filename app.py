"""
Google Maps Business Scraper — Streamlit Web App
Existing gmaps_scraper.py ke SEL/helpers reuse karta hai.

Run:
    streamlit run app.py
"""
import io, csv, re, time, queue, threading, sys, subprocess
from pathlib import Path

# Cloud pe Chromium install karo (local pe already hoga, ignore karega)
subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
               capture_output=True, check=False)

import streamlit as st
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

sys.path.insert(0, str(Path(__file__).parent))
from gmaps_scraper import SEL, text_or_blank, attr_or_blank, scroll_feed

# ── Page ──────────────────────────────────────────────────────
st.set_page_config(page_title="GMaps Scraper", page_icon="🗺️", layout="wide")

# ── Data ──────────────────────────────────────────────────────
STATES = {
    "Maharashtra":    ["Mumbai","Pune","Nagpur","Nashik","Aurangabad","Kolhapur","Solapur","Amravati","Sangli","Ahmednagar"],
    "Gujarat":        ["Ahmedabad","Surat","Vadodara","Rajkot","Bhavnagar","Jamnagar","Junagadh","Gandhinagar"],
    "Rajasthan":      ["Jaipur","Jodhpur","Udaipur","Kota","Bikaner","Ajmer","Alwar"],
    "Uttar Pradesh":  ["Lucknow","Kanpur","Varanasi","Agra","Meerut","Allahabad","Bareilly","Moradabad"],
    "Bihar":          ["Patna","Gaya","Muzaffarpur","Bhagalpur","Darbhanga","Motihari"],
    "Punjab":         ["Ludhiana","Amritsar","Jalandhar","Patiala","Bathinda","Mohali"],
    "Haryana":        ["Ambala","Hisar","Rohtak","Karnal","Panipat","Yamunanagar","Sirsa"],
    "Madhya Pradesh": ["Bhopal","Indore","Jabalpur","Gwalior","Ujjain","Rewa","Sagar"],
    "Delhi NCR":      ["Delhi","Noida","Gurgaon","Ghaziabad","Faridabad","Greater Noida"],
    "Karnataka":      ["Bangalore","Mysore","Hubli","Mangalore","Belgaum","Davangere"],
    "Tamil Nadu":     ["Chennai","Coimbatore","Madurai","Salem","Tiruchirappalli","Tirunelveli"],
    "Andhra Pradesh": ["Hyderabad","Visakhapatnam","Vijayawada","Guntur","Nellore","Kurnool"],
    "Chhattisgarh":   ["Raipur","Bilaspur","Bhilai","Korba","Durg","Rajnandgaon"],
    "Odisha":         ["Bhubaneswar","Cuttack","Berhampur","Rourkela","Sambalpur"],
    "West Bengal":    ["Kolkata","Howrah","Asansol","Siliguri","Durgapur","Kharagpur"],
}

PRESETS = [
    "cattle feed dealers",
    "poultry feed dealers",
    "animal feed suppliers",
    "fertilizer dealers",
    "pesticide dealers",
    "seeds dealers",
    "agro chemicals dealers",
    "dairy equipment suppliers",
    "tractor dealers",
    "veterinary medicine shops",
    "irrigation equipment dealers",
    "farm machinery dealers",
    "cold storage services",
    "food processing units",
    "grain traders",
]

# ── Session State ─────────────────────────────────────────────
_defaults = {
    "running":        False,
    "results":        [],
    "log":            [],
    "scraped_count":  0,
    "total_expected": 0,
    "q":              None,
    "city_list":      [],
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Background Scraper ────────────────────────────────────────
def scrape_worker(cities, term, max_per_city, q):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(
                locale="en-US", viewport={"width": 1280, "height": 900}
            ).new_page()

            all_rows = []

            for city in cities:
                q.put(("log", f"\n🔍  {city}  —  searching..."))
                try:
                    page.goto(
                        "https://www.google.com/maps/search/"
                        + f"{term} in {city}".replace(" ", "+"),
                        wait_until="domcontentloaded", timeout=60000,
                    )
                    try:
                        page.click(
                            'button[aria-label*="Accept"], button:has-text("Accept all")',
                            timeout=3000,
                        )
                    except PWTimeout:
                        pass

                    try:
                        page.wait_for_selector(SEL["feed"], timeout=20000)
                    except PWTimeout:
                        q.put(("log", f"   ⚠️  {city}: results nahi mile (CAPTCHA?)"))
                        continue

                    scroll_feed(page, max_per_city)

                    hrefs, seen = [], set()
                    for el in page.query_selector_all(SEL["card_link"]):
                        href = el.get_attribute("href") or ""
                        if href and href not in seen:
                            seen.add(href)
                            hrefs.append(href)
                        if len(hrefs) >= max_per_city:
                            break

                    q.put(("log", f"   📋  {len(hrefs)} listings mili — detail nikal raha hoon..."))

                    city_count = 0
                    for href in hrefs:
                        try:
                            page.goto(href, wait_until="domcontentloaded", timeout=30000)
                            page.wait_for_selector(SEL["name"], timeout=10000)
                            time.sleep(0.6)
                            all_rows.append({
                                "name":     text_or_blank(page, SEL["name"]),
                                "rating":   text_or_blank(page, SEL["rating"]),
                                "category": text_or_blank(page, SEL["category"]),
                                "address":  text_or_blank(page, SEL["address"]),
                                "phone":    text_or_blank(page, SEL["phone"]),
                                "website":  attr_or_blank(page, SEL["website"], "href"),
                                "maps_url": page.url,
                                "city":     city,
                            })
                            city_count += 1
                            q.put(("tick", 1))
                        except PWTimeout:
                            continue

                    q.put(("log", f"   ✅  {city}: {city_count} complete"))
                    time.sleep(2)

                except Exception as e:
                    q.put(("log", f"   ❌  {city}: {e}"))

            browser.close()

        # Dedup by name + address
        seen_k, uniq = set(), []
        for r in all_rows:
            k = (r["name"].lower().strip(), r["address"].lower().strip())
            if k not in seen_k:
                seen_k.add(k)
                uniq.append(r)

        q.put(("done", uniq))

    except Exception as e:
        q.put(("error", str(e)))


def make_csv(rows):
    fields = ["name","rating","category","address","phone","website","maps_url","city"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


# ── Sidebar ───────────────────────────────────────────────────
with st.sidebar:
    st.title("🗺️ GMaps Scraper")
    st.caption("Google Maps se business data scrape karo")
    st.divider()

    # 1. Search Term
    st.subheader("1️⃣  Search Term")
    preset = st.selectbox("Preset", ["— custom —"] + PRESETS)
    default_term = "" if preset == "— custom —" else preset
    term = st.text_input(
        "Search term",
        value=default_term,
        placeholder="e.g. cattle feed dealers",
        label_visibility="collapsed",
    )
    st.divider()

    # 2. Cities
    st.subheader("2️⃣  Cities")
    state_pick = st.selectbox("State", list(STATES.keys()))

    raw_cities = st.text_area(
        "City list",
        value="\n".join(st.session_state.city_list),
        height=150,
        placeholder="Pune\nMumbai\nNagpur\n...",
        label_visibility="collapsed",
    )
    cities = [c.strip() for c in raw_cities.splitlines() if c.strip()]

    c1, c2 = st.columns(2)
    if c1.button("➕ Add State", use_container_width=True):
        merged = list(cities)
        for c in STATES[state_pick]:
            if c not in merged:
                merged.append(c)
        st.session_state.city_list = merged
        st.rerun()
    if c2.button("🗑️ Clear", use_container_width=True):
        st.session_state.city_list = []
        st.rerun()

    st.caption(f"{len(cities)} cities selected")
    st.divider()

    # 3. Settings
    st.subheader("3️⃣  Settings")
    max_per = st.slider("Max results per city", 10, 200, 80, step=10)
    st.divider()

    # Run button
    ready = bool(term.strip() and cities)

    if not st.session_state.running:
        if st.button("🚀  Start Scraping", type="primary",
                     use_container_width=True, disabled=not ready):
            q = queue.Queue()
            st.session_state.q              = q
            st.session_state.running        = True
            st.session_state.results        = []
            st.session_state.scraped_count  = 0
            st.session_state.total_expected = len(cities) * max_per
            st.session_state.log = [
                f"Search : {term}",
                f"Cities : {', '.join(cities)}",
                f"Max/city: {max_per}",
                "─" * 44,
            ]
            threading.Thread(
                target=scrape_worker,
                args=(cities, term.strip(), max_per, q),
                daemon=True,
            ).start()
            st.rerun()

        if not ready:
            if not term.strip():
                st.warning("Search term daalo")
            if not cities:
                st.warning("Kam se kam 1 city chahiye")
    else:
        st.button("⏳  Scraping chal raha hai…", disabled=True, use_container_width=True)

# ── Main Area ─────────────────────────────────────────────────
st.header("Google Maps Business Scraper", divider="gray")

# Poll queue
needs_rerun = False
if st.session_state.running and st.session_state.q:
    q = st.session_state.q
    while not q.empty():
        msg, payload = q.get_nowait()
        needs_rerun = True
        if msg == "log":
            st.session_state.log.append(payload)
        elif msg == "tick":
            st.session_state.scraped_count += payload
        elif msg == "done":
            st.session_state.running = False
            st.session_state.results = payload
            n = len(payload)
            st.session_state.log += [
                "─" * 44,
                f"✅  Done!  {n} unique listings",
                f"📞  Phone: {sum(1 for r in payload if r['phone'])}  "
                f"| 🌐  Website: {sum(1 for r in payload if r['website'])}",
            ]
        elif msg == "error":
            st.session_state.running = False
            st.session_state.log.append(f"\n❌  Error: {payload}")

# Progress bar
if st.session_state.total_expected > 0:
    pct = min(st.session_state.scraped_count / st.session_state.total_expected, 1.0)
    label = (
        f"Scraping… {st.session_state.scraped_count} / ~{st.session_state.total_expected} records"
        if st.session_state.running else
        f"Done — {st.session_state.scraped_count} records scraped"
    )
    st.progress(pct, text=label)

# Tabs
tab_log, tab_results = st.tabs(["📋  Live Log", "📊  Results"])

with tab_log:
    if st.session_state.log:
        st.code("\n".join(st.session_state.log), language=None)
    else:
        st.info("👈  Left side se configure karo → Start Scraping dabao")

with tab_results:
    results = st.session_state.results
    if results:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Listings", len(results))
        m2.metric("With Phone",    sum(1 for r in results if r["phone"]))
        m3.metric("With Website",  sum(1 for r in results if r["website"]))
        m4.metric("With Rating",   sum(1 for r in results if r["rating"]))

        st.download_button(
            label="⬇️  Download CSV",
            data=make_csv(results),
            file_name=re.sub(r"[^a-z0-9]+", "_", term.lower()).strip("_") + ".csv",
            mime="text/csv",
            type="primary",
        )

        all_cities = sorted({r["city"] for r in results})
        if len(all_cities) > 1:
            city_f = st.selectbox("Filter by City", ["All"] + all_cities)
            show = results if city_f == "All" else [r for r in results if r["city"] == city_f]
        else:
            show = results

        st.dataframe(
            show,
            use_container_width=True,
            hide_index=True,
            column_config={
                "maps_url": st.column_config.LinkColumn("Maps",    display_text="🔗 Open"),
                "website":  st.column_config.LinkColumn("Website", display_text="🌐 Open"),
                "rating":   st.column_config.TextColumn("⭐ Rating"),
            },
        )
    else:
        st.info("Results scraping ke baad yahan dikhenge")

# Auto-refresh while running
if st.session_state.running:
    time.sleep(1)
    st.rerun()
elif needs_rerun:
    st.rerun()
