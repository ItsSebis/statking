"""Open-data portal Schleswig-Holstein (CKAN, no key): Kiel statistics office + Statistikamt Nord.
Three CSV layouts are handled:
  kiel_yearly   Land;Stadt;...;Jahr;<value cols>              (one row per year)
  kiel_district Land;...;Stadtteil;<year or 31.12.YYYY cols>  (one row per Stadtteil)
  nord          Statistikamt Nord 'Zeitreihe' export (title lines, 'Zeit' header row)"""
import csv
import io
import re
import unicodedata

import requests

NAME = "stat"  # Source interface: see statking/sources/__init__.py
CKAN = "https://opendata.schleswig-holstein.de/api/3/action/package_show"
UA = "statking/0.1 (personal research)"

# slug -> (ckan dataset id, layout)
DATASETS = {
    "kiel.bevoelkerung": ("wohnberechtigte-bevolkerung-in-kiel", "kiel_yearly"),
    "kiel.zuzuege": ("zuzuge-nach-kiel", "kiel_yearly"),
    "kiel.wegzuege": ("wegzuge-aus-kiel", "kiel_yearly"),
    "kiel.studierende": ("studierende-in-kiel", "kiel_yearly"),
    "kiel.wohnungsbestand": ("bestand-an-wohnungen-in-kiel", "kiel_yearly"),
    "kiel.arbeitslosenquote": ("arbeitslosenquote-in-kiel", "kiel_yearly"),
    "kiel.wohngeld": ("wohngeld-in-kiel", "kiel_yearly"),
    "nord.kiel.genehmigte_wohnungen": ("anzahl-der-genehmigten-wohnungen-insgesamt-neubau-und-baumassnahmen-in-kiel", "nord"),
    "nord.kiel.wohngebaeudebestand": ("wohngebaudebestand-nach-anzahl-der-wohnungen-in-kiel", "nord"),
    "nord.sh.studierende": ("studierende-in-schleswig-holstein-im-wintersemester", "nord"),
}
# Stadtteil-level datasets -> district_metrics (shown as map layers)
DISTRICT_DATASETS = {
    "einwohner": ("einwohnerinnen-und-einwohner-in-den-kieler-stadtteilen", "Einwohner", "Personen"),
    "auslaender": ("auslanderinnen-und-auslander-in-den-kieler-stadtteilen", "Ausländer", "Personen"),
    "arbeitslose": ("arbeitslose-in-den-stadtteilen-in-kiel", "Arbeitslose", "Personen"),
    "betroffenheit": ("arbeitslose-betroffenheitsquote-in-den-stadtteilen", "Arbeitslosen-Betroffenheit", "%"),
}


def slug(s):
    s = unicodedata.normalize("NFKD", s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    s = s.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def num(x):
    x = x.strip().replace(" ", "").replace(" ", "")
    if x in ("", "-", ".", "x", "/", "...", "(x)"):
        return None
    try:
        return float(x.replace(".", "").replace(",", ".")) if re.search(r"\d\.\d{3}(\D|$)", x) else float(x.replace(",", "."))
    except ValueError:
        return None


def to_ts(label):
    """'2019' / '31.12.2019' / '2015/16' -> ISO date; None if not a time label."""
    label = label.strip()
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", label)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.fullmatch(r"(\d{4})/\d{2,4}", label)
    if m:
        return f"{m.group(1)}-10-15"  # winter semester
    if re.fullmatch(r"\d{4}", label):
        return f"{label}-12-31"
    return None


def fetch_csv(ckan_id):
    r = requests.get(CKAN, params={"id": ckan_id}, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    res = [x for x in r.json()["result"]["resources"] if (x.get("format") or "").upper().endswith("CSV")]
    if not res:
        raise RuntimeError(f"{ckan_id}: no CSV resource")
    c = requests.get(res[0]["url"], headers={"User-Agent": UA}, timeout=60)
    c.raise_for_status()
    raw = c.content
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1")


def parse(text, layout):
    """-> [(column_name, ts, value)]  (for kiel_district use parse_district)"""
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    out = []
    if layout == "kiel_yearly":
        head = rows[0]
        tcol = next(i for i, h in enumerate(head) if h in ("Jahr", "Wintersemester"))
        skip = {"Land", "Stadt", "Kategorie", "Merkmal", "Jahr", "Wintersemester"}
        for r in rows[1:]:
            ts = to_ts(r[tcol]) if len(r) > tcol else None
            if not ts:
                continue
            for i, h in enumerate(head):
                if h not in skip and i < len(r) and (v := num(r[i])) is not None:
                    out.append((h, ts, v))
    elif layout == "nord":
        zi = next(i for i, r in enumerate(rows) if r and r[0].strip() == "Zeit")
        cells = [c.strip() for c in rows[zi][1:]]
        if all(c in ("Anzahl", "%", "Prozent", "") for c in cells) and zi > 0:  # group row above the unit row
            groups, last = [], ""
            for g in rows[zi - 1][1:]:
                last = g.strip() or last
                groups.append(last)
            names = [f"{g} Anteil Prozent" if u in ("%", "Prozent") else g for g, u in zip(groups, cells)]
        else:
            names = [re.sub(r"\s+", " ", c) for c in cells]
        for r in rows[zi + 1:]:
            ts = to_ts(r[0]) if r else None
            if not ts:
                break
            for name, cell in zip(names, r[1:]):
                if name and (v := num(cell)) is not None:
                    out.append((name, ts, v))
    else:
        raise ValueError(layout)
    return out


def parse_district(text):
    """-> [(merkmal, stadtteil, ts, value)]"""
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    head = rows[0]
    di, mi = head.index("Stadtteil"), head.index("Merkmal")
    tcols = [(i, to_ts(h)) for i, h in enumerate(head) if to_ts(h)]
    out = []
    for r in rows[1:]:
        if len(r) <= di or not r[di].strip() or r[di].strip().lower() == "unbekannt":
            continue
        for i, ts in tcols:
            if i < len(r) and (v := num(r[i])) is not None:
                out.append((r[mi].strip(), r[di].strip(), ts, v))
    return out


def collect(conn, params):
    """stat.<dataset>.<column> Series; Stadtteil datasets go to district_metrics. One broken dataset must not block the rest."""
    out = []
    for key, (cid, layout) in DATASETS.items():
        try:
            pts = parse(fetch_csv(cid), layout)
        except Exception as e:
            print(f"stat[{key}] failed: {e!r}")
            continue
        out += [(f"stat.{key}.{slug(col)}", ts, v) for col, ts, v in pts]
    for key, (cid, _label, _unit) in DISTRICT_DATASETS.items():
        try:
            pts = parse_district(fetch_csv(cid))
        except Exception as e:
            print(f"stat[{key}] failed: {e!r}")
            continue
        conn.executemany("INSERT OR REPLACE INTO district_metrics VALUES (?,?,?,?,?)",
                         [(key, m, d, ts, v) for m, d, ts, v in pts])
    print(f"stat: {len(out)} points")
    return out
