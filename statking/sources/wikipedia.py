"""Wikimedia pageviews API (no key; needs a descriptive User-Agent)."""
from urllib.parse import quote

import time
from datetime import date, timedelta

import requests

NAME = "wiki"  # Source interface: see statking/sources/__init__.py

UA = "statking/0.1 (personal research)"


def daily_views(lang: str, article: str, start: str, end: str):
    """start/end as YYYYMMDD -> [(YYYY-MM-DD, views)]. 404 means no data in range."""
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
           f"{lang}.wikipedia/all-access/user/{quote(article.replace(' ', '_'), safe='')}/daily/{start}/{end}")
    r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return [(f"{i['timestamp'][:4]}-{i['timestamp'][4:6]}-{i['timestamp'][6:8]}", i["views"]) for i in r.json()["items"]]


def collect(conn, params):
    """Daily view Series wiki.<lang>.<Title> per configured article ('lang:Title'); resumes at the last stored day."""
    backfill = params.get("backfill_from", "2024-01-01")
    yesterday = date.today() - timedelta(days=1)
    rows = []
    for a in params.get("articles", []):
        lang, title = a.split(":", 1)
        series = f"wiki.{lang}.{title.replace(' ', '_')}"
        last = conn.execute("SELECT MAX(ts) FROM metrics WHERE series=?", (series,)).fetchone()[0]
        start = date.fromisoformat(last) if last else date.fromisoformat(backfill)
        if start > yesterday:
            continue
        pts = daily_views(lang, title, start.strftime("%Y%m%d"), yesterday.strftime("%Y%m%d"))
        rows += [(series, d, v) for d, v in pts]
        print(f"wiki[{a}]: {len(pts)} days")
        time.sleep(1)
    return rows
