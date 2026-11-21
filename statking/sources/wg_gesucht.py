"""WG-Gesucht Kiel scraper. Parses the print-only block of each list card,
which carries clean id/price/size/district fields."""
import random
import re
import time

import requests
from bs4 import BeautifulSoup

BASE = "https://www.wg-gesucht.de"
CITY_ID = 71  # Kiel
# category -> (url slug, wg-gesucht category id)
CATEGORIES = {
    "wg": ("wg-zimmer", 0),
    "1zi": ("1-zimmer-wohnungen", 1),
    "wohnung": ("wohnungen", 2),
}
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
MAX_PAGES = 15


def page_url(category: str, page: int) -> str:
    slug, cid = CATEGORIES[category]
    return f"{BASE}/{slug}-in-Kiel.{CITY_ID}.{cid}.1.{page}.html"


def parse_page(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.select("a.offer_list_item"):
        ad_id = card.get("data-id")
        pr = card.find_next("div", class_="printonly")
        if not ad_id or pr is None:
            continue
        bolds = [b.get_text(" ", strip=True) for b in pr.find_all("b")]
        text = pr.get_text(" ", strip=True)
        price = size = None
        m = re.search(r"(\d[\d.]*)\s*€", text)
        if m:
            price = int(m.group(1).replace(".", ""))
        m = re.search(r"(\d+(?:,\d+)?)\s*m²", text)
        if m:
            size = float(m.group(1).replace(",", "."))
        divs = pr.find_all("div", recursive=False)
        loc = divs[1].get_text(" ", strip=True) if len(divs) > 1 else ""
        parts = [p.strip() for p in loc.split("·")]
        flat_desc = parts[0] if parts else ""
        place = parts[1] if len(parts) > 1 else ""
        street = parts[2] if len(parts) > 2 else ""
        district = re.sub(r"^Kiel\s*", "", place).strip() or None
        av = re.search(r"Verfügbar:\s*([\d.]+)", text)
        href = card.get("href", "")
        items.append({
            "id": ad_id,
            "title": card.get("aria-label") or (bolds[0] if bolds else ""),
            "district": district,
            "street": street or None,
            "flat_desc": flat_desc,
            "price_eur": price,
            "size_m2": size,
            "available_from": av.group(1) if av else None,
            "url": f"{BASE}{href}" if href.startswith("/") else href,
        })
    return items


def scrape(category: str, delay=(3.0, 6.0)):
    """Return (items, complete). complete=False if a request failed midway
    (so we don't wrongly mark listings as gone)."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
    seen, items = set(), []
    for page in range(MAX_PAGES):
        try:
            r = s.get(page_url(category, page), timeout=30)
        except requests.RequestException as e:
            print(f"  page {page}: request failed: {e}")
            return items, False
        if r.status_code != 200:
            print(f"  page {page}: HTTP {r.status_code}")
            return items, False
        if "captcha" in r.text.lower()[:20000] and "offer_list_item" not in r.text:
            print("  blocked by captcha; stopping")
            return items, False
        page_items = [i for i in parse_page(r.text) if i["id"] not in seen]
        if not page_items:
            return items, True  # ran past the last page
        for i in page_items:
            seen.add(i["id"])
        items.extend(page_items)
        time.sleep(random.uniform(*delay))
    return items, False  # hit MAX_PAGES: can't be sure it's complete
