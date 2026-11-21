"""Collectors + scheduler. Each job has its own interval and its last run is
persisted (job_runs), so restarting a container never triggers an extra scrape."""
import os
import time
from datetime import date, datetime, timedelta, timezone

from . import db, geo, steam_job
from .sources import chess, weather, wikipedia, wg_gesucht

H = lambda name, default: float(os.environ.get(name, default))


def job_rent(conn):
    for cat in wg_gesucht.CATEGORIES:
        items, complete = wg_gesucht.scrape(cat)
        new, gone = db.save_run(conn, "wg-gesucht", cat, items, complete)
        print(f"rent[{cat}]: {len(items)} listings, {new} new, {gone} gone, complete={complete}")
    geo.geocode_missing(conn)


def job_steam(conn):
    steam_job.collect(conn)


def job_weather(conn):
    backfill = os.environ.get("WEATHER_BACKFILL_FROM", "2024-01-01")
    today = date.today()
    for name, station in weather.STATIONS.items():
        last = conn.execute("SELECT MAX(ts) FROM metrics WHERE series=?", (f"weather.{name}.temp_mean",)).fetchone()[0]
        start = date.fromisoformat(last) - timedelta(days=1) if last else date.fromisoformat(backfill)
        rows = []
        while start <= today:
            end = min(start + timedelta(days=89), today)
            for day, v in weather.daily(weather.fetch_hourly(station, start.isoformat(), (end + timedelta(days=1)).isoformat()), today.isoformat()).items():
                rows += [(f"weather.{name}.{k}", day, val) for k, val in v.items() if val is not None]
            start = end + timedelta(days=1)
            time.sleep(1)
        db.put_metrics(conn, rows)
        conn.commit()
        print(f"weather[{name}]: {len(rows)} points")


def job_wiki(conn):
    # WIKI_ARTICLES="de:Weihnachtsmann,de:Trennung"
    arts = [a.strip() for a in os.environ.get("WIKI_ARTICLES", "de:Weihnachtsmann,de:Trennung").split(",") if a.strip()]
    backfill = os.environ.get("WIKI_BACKFILL_FROM", "2024-01-01")
    yesterday = date.today() - timedelta(days=1)
    for a in arts:
        lang, title = a.split(":", 1)
        series = f"wiki.{lang}.{title.replace(' ', '_')}"
        last = conn.execute("SELECT MAX(ts) FROM metrics WHERE series=?", (series,)).fetchone()[0]
        start = date.fromisoformat(last) if last else date.fromisoformat(backfill)
        if start > yesterday:
            continue
        pts = wikipedia.daily_views(lang, title, start.strftime("%Y%m%d"), yesterday.strftime("%Y%m%d"))
        db.put_metrics(conn, [(series, d, v) for d, v in pts])
        conn.commit()
        print(f"wiki[{a}]: {len(pts)} days")
        time.sleep(1)


def job_chess(conn):
    user = os.environ.get("CHESSCOM_USER")
    if not user:
        print("chess: CHESSCOM_USER not set, skipping")
        return
    last = conn.execute("SELECT MAX(end_time) FROM chess_games").fetchone()[0]
    since = last[:7].replace("-", "/") if last else "0000/00"
    n = 0
    for url in chess.archives(user):
        if url[-7:] < since:  # url ends with YYYY/MM; only re-read months from the last stored game on
            continue
        rows = chess.parse_games(user, chess.month(url))
        conn.executemany("INSERT OR IGNORE INTO chess_games VALUES (?,?,?,?,?,?,?)", rows)
        db.put_metrics(conn, [(f"chess.rating.{r[2]}", r[1], r[5]) for r in rows if r[5]])
        conn.commit()
        n += len(rows)
        time.sleep(1)
    print(f"chess[{user}]: {n} games read")


# name -> (function, default interval in hours, env var overriding it)
JOBS = {
    "rent": (job_rent, 24, "RENT_INTERVAL_HOURS"),
    "steam": (job_steam, 0.25, "STEAM_INTERVAL_HOURS"),
    "weather": (job_weather, 12, "WEATHER_INTERVAL_HOURS"),
    "wiki": (job_wiki, 24, "WIKI_INTERVAL_HOURS"),
    "chess": (job_chess, 6, "CHESS_INTERVAL_HOURS"),
}


def due(conn, name, force=False):
    _, default, env = JOBS[name]
    row = conn.execute("SELECT ts FROM job_runs WHERE job=?", (name,)).fetchone()
    if force or row is None:
        return True
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).total_seconds()
    return age >= H(env, default) * 3600


def run_job(conn, name):
    fn = JOBS[name][0]
    try:
        fn(conn)
        conn.execute("INSERT OR REPLACE INTO job_runs VALUES (?,?,?,?)", (name, db.now(), 1, None))
    except Exception as e:  # one failing source must not stop the others
        print(f"{name} failed: {e!r}")
        conn.execute("INSERT OR REPLACE INTO job_runs VALUES (?,?,?,?)", (name, db.now(), 0, repr(e)[:300]))
    conn.commit()


def run_due(conn, names=None, force=False):
    for name in names or JOBS:
        if due(conn, name, force):
            run_job(conn, name)


def daemon(path):
    conn = db.connect(path)
    while True:
        run_due(conn)
        time.sleep(60)
