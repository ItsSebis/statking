"""Analysis over the SQLite data."""
import csv
import statistics
from datetime import datetime


def _days(a, b):
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 86400


def district_table(conn, category="wg", active_only=True):
    q = "SELECT district, price_eur, size_m2, first_seen, last_seen, gone_at FROM listings WHERE category=? AND price_eur>0 AND size_m2>0"
    if active_only:
        q += " AND gone_at IS NULL"
    by = {}
    for r in conn.execute(q, (category,)):
        by.setdefault(r["district"] or "?", []).append(r)
    rows = []
    for d, rs in by.items():
        eur_m2 = [r["price_eur"] / r["size_m2"] for r in rs]
        rows.append((d, len(rs), statistics.median(eur_m2), statistics.median(r["price_eur"] for r in rs)))
    return sorted(rows, key=lambda x: -x[2])


def lifetimes(conn, category="wg"):
    """Days listed for listings that have vanished (lower bound: resolution = scrape interval)."""
    return [
        _days(r["first_seen"], r["gone_at"])
        for r in conn.execute("SELECT first_seen, gone_at FROM listings WHERE category=? AND gone_at IS NOT NULL", (category,))
    ]


def print_report(conn, category="wg"):
    print(f"\n=== {category}: median €/m² by district (active listings) ===")
    print(f"{'district':28} {'n':>4} {'€/m²':>7} {'€ median':>9}")
    for d, n, m2, p in district_table(conn, category):
        print(f"{d:28} {n:>4} {m2:>7.1f} {p:>9.0f}")
    lt = lifetimes(conn, category)
    if lt:
        print(f"\nvanished listings: {len(lt)}, median lifetime {statistics.median(lt):.1f} days")
    else:
        print("\nno vanished listings yet - needs at least two scrapes")


def export_csv(conn, path):
    cur = conn.execute("SELECT * FROM listings ORDER BY first_seen")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([c[0] for c in cur.description])
        w.writerows(cur.fetchall())
