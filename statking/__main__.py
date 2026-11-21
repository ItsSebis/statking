import argparse
import os
import time

from . import db, geo, stats, steam_job
from .sources import wg_gesucht

DB_PATH = os.environ.get("STATKING_DB", "data/statking.db")


def cmd_scrape(args):
    conn = db.connect(DB_PATH)
    for cat in args.categories:
        print(f"scraping wg-gesucht Kiel [{cat}] ...")
        items, complete = wg_gesucht.scrape(cat)
        new, gone = db.save_run(conn, "wg-gesucht", cat, items, complete)
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
    interval = float(os.environ.get("SCRAPE_INTERVAL_HOURS", "24")) * 3600
    while True:
        try:
            cmd_scrape(args)
        except Exception as e:  # keep the loop alive
            print(f"scrape failed: {e}")
        try:
            cmd_geocode(args)
        except Exception as e:
            print(f"geocode failed: {e}")
        try:
            cmd_steam_scrape(args)
        except Exception as e:
            print(f"steam failed: {e}")
        time.sleep(interval)


def cmd_report(args):
    conn = db.connect(DB_PATH)
    for cat in args.categories:
        stats.print_report(conn, cat)


def cmd_export(args):
    stats.export_csv(db.connect(DB_PATH), args.path)
    print(f"wrote {args.path}")


def main():
    p = argparse.ArgumentParser(prog="statking")
    sub = p.add_subparsers(required=True)
    cats = list(wg_gesucht.CATEGORIES)
    for name, fn in (("scrape", cmd_scrape), ("daemon", cmd_daemon), ("report", cmd_report)):
        sp = sub.add_parser(name)
        sp.add_argument("categories", nargs="*", default=cats, help=f"any of: {cats}")
        sp.set_defaults(fn=fn)
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
