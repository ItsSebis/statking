import argparse
import os
import time

from . import cities, correlate, db, geo, jobs, stats, steam_job
from .sources import wg_gesucht

DB_PATH = os.environ.get("STATKING_DB", "data/statking.db")


def cmd_scrape(args):
    """Scrape now: statking scrape [--city kiel] [categories...]"""
    conn = db.connect(DB_PATH)
    for city in args.city or cities.enabled():
        for cat in args.categories or cities.CITIES[city]["categories"]:
            print(f"scraping wg-gesucht {city} [{cat}] ...")
            items, complete = wg_gesucht.scrape(city, cat)
            new, gone = db.save_run(conn, "wg-gesucht", cat, items, complete, city)
            print(f"  {len(items)} listings, {new} new, {gone} gone, complete={complete}")


def cmd_geocode(args):
    geo.geocode_missing(db.connect(DB_PATH))


def cmd_serve(args):
    import uvicorn
    uvicorn.run("statking.web.app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


def cmd_steam_scrape(args):
    steam_job.collect(db.connect(DB_PATH))


def cmd_steam_report(args):
    steam_job.report(db.connect(DB_PATH))


def cmd_daemon(args):
    jobs.daemon(DB_PATH)


def cmd_run(args):
    """Run named jobs now (default: all), ignoring intervals."""
    jobs.run_due(db.connect(DB_PATH), args.jobs or None, force=True)


def cmd_series(args):
    for name, n, lo, hi in correlate.list_series(db.connect(DB_PATH)):
        print(f"{name:45} {n:>7} points  {lo} .. {hi}")


def cmd_corr(args):
    r, n, res = correlate.correlate(db.connect(DB_PATH), args.a, args.b, args.lag)
    print(f"not enough overlap at {res} resolution" if r is None else f"r = {r:+.3f} over {n} {res}s (lag {args.lag} {res}s)")


def cmd_report(args):
    conn = db.connect(DB_PATH)
    for cat in args.categories or cities.CITIES[args.city]["categories"]:
        stats.print_report(conn, cat, args.city)


def cmd_export(args):
    stats.export_csv(db.connect(DB_PATH), args.path)
    print(f"wrote {args.path}")


def main():
    p = argparse.ArgumentParser(prog="statking")
    sub = p.add_subparsers(required=True)
    cats = list(wg_gesucht.CATEGORIES)
    sub.add_parser("daemon").set_defaults(fn=cmd_daemon)
    rn = sub.add_parser("run", help="run collectors now")
    rn.add_argument("jobs", nargs="*", choices=list(jobs.JOBS), help="default: all")
    rn.set_defaults(fn=cmd_run)
    sub.add_parser("series").set_defaults(fn=cmd_series)
    co = sub.add_parser("corr", help="correlate two series: statking corr A B --lag 1")
    co.add_argument("a"); co.add_argument("b"); co.add_argument("--lag", type=int, default=0)
    co.set_defaults(fn=cmd_corr)
    sc = sub.add_parser("scrape")
    sc.add_argument("--city", action="append", choices=list(cities.CITIES), help="repeatable; default: RENT_CITIES / all")
    sc.add_argument("categories", nargs="*", choices=cats)
    sc.set_defaults(fn=cmd_scrape)
    rp = sub.add_parser("report")
    rp.add_argument("--city", default="kiel", choices=list(cities.CITIES))
    rp.add_argument("categories", nargs="*", choices=cats)
    rp.set_defaults(fn=cmd_report)
    sub.add_parser("steam-scrape").set_defaults(fn=cmd_steam_scrape)
    sub.add_parser("steam-report").set_defaults(fn=cmd_steam_report)
    sub.add_parser("geocode").set_defaults(fn=cmd_geocode)
    sub.add_parser("serve").set_defaults(fn=cmd_serve)
    ex = sub.add_parser("export")
    ex.add_argument("path", nargs="?", default="data/listings.csv")
    ex.set_defaults(fn=cmd_export)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
