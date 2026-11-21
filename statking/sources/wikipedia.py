"""Wikimedia pageviews API (no key; needs a descriptive User-Agent)."""
from urllib.parse import quote

import requests

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
