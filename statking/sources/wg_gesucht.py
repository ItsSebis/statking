"""WG-Gesucht Kiel scraper. Parses the print-only block of each list card,
which carries clean id/price/size/district fields."""
import os
import random
import re
import time

import requests
from bs4 import BeautifulSoup

from .. import cities

BASE = "https://www.wg-gesucht.de"
# category -> (url slug, wg-gesucht category id)
CATEGORIES = {
    "wg": ("wg-zimmer", 0),
    "1zi": ("1-zimmer-wohnungen", 1),
    "wohnung": ("wohnungen", 2),
}
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
MAX_PAGES = 15


class Blocked(Exception):
    """The site is serving a bot check / error instead of listings. Stop and back off."""


def page_url(city: str, category: str, page: int) -> str:
    slug, cid = CATEGORIES[category]
    c = cities.CITIES[city]
    name = c["name"].replace(" ", "-").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return f"{BASE}/{slug}-in-{name}.{c['wg_id']}.{cid}.1.{page}.html"


def parse_page(html: str, city_name: str = "Kiel") -> list:
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
        district = re.sub(r"^" + re.escape(city_name) + r"\s*", "", place).strip() or None
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


def scrape(city: str, category: str, delay=(4.0, 8.0), max_pages=None):
    """Return (items, complete). complete=False if the crawl was cut short (page cap or error), so
    we never mark listings as gone based on a partial view. Raises Blocked on a bot check."""
    max_pages = max_pages or int(os.environ.get("RENT_MAX_PAGES", MAX_PAGES))
    cname = cities.CITIES[city]["name"]
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
    seen, items = set(), []
    for page in range(max_pages):
        try:
            r = s.get(page_url(city, category, page), timeout=30)
        except requests.RequestException as e:
            print(f"  {city}/{category} page {page}: request failed: {e}")
            return items, False
        if r.status_code in (403, 429, 503):
            raise Blocked(f"HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"  {city}/{category} page {page}: HTTP {r.status_code}")
            return items, False
        parsed = parse_page(r.text, cname)
        if not parsed and "offer_list_item" not in r.text:
            # an empty page past the end still has the normal layout (title mentions the city); a check page doesn't
            title = re.search(r"<title>([^<]*)", r.text)
            if not title or cname.split()[0].lower() not in title.group(1).lower():
                raise Blocked(f"unexpected page: {title.group(1).strip() if title else 'no title'}")
        page_items = [i for i in parsed if i["id"] not in seen]
        if not page_items:
            return items, True  # ran past the last page
        for i in page_items:
            seen.add(i["id"])
        items.extend(page_items)
        time.sleep(random.uniform(*delay))
    return items, False  # hit the page cap: can't be sure it's complete
