"""Source interface, auto-discovery and the sources config file.

A Source is ONE module in this package that defines:

    NAME     str   required. Unique; also the first segment of every Series it writes.
    collect  func  required. collect(conn, params) -> iterable of (series, ts, value) rows.
                   `params` is the dict from this Source's entry in the config file;
                   `conn` is the DB connection (read it for incremental collection,
                   e.g. the last stored ts, but do not write metrics: the runner does).
    FAMILY   int   optional. Series under NAME sharing their first FAMILY dotted segments
                   form a Family (e.g. rent.kiel.wg with FAMILY = 3); pairs inside one
                   are Derived Pairs. Omit when the Source's Series are independent measures.
    BACKFILL_FROM  str  optional. ISO date: how far back the Source's official history goes.

Backfill: a Source with BACKFILL_FROM gets `params["backfill_from"]` set by the runner on its first
run (no Series stored under NAME yet): the declared date, or the config's own `backfill_from` when
that is later (config may narrow the range, never exceed it). Later runs get the config params
unchanged. `collect` takes its start date from `params["backfill_from"]` when nothing is stored.
Backfill is idempotent: rows are keyed (series, ts) so re-collected points replace, never duplicate.
Scraped Sources omit BACKFILL_FROM and just accumulate.

Schedule and parameters live in the sources config file (sources.json, override the path
with STATKING_SOURCES_CONFIG), never in environment variables:

    {"weather": {"interval_hours": 12, "params": {"backfill_from": "2024-01-01"}}}

A Source absent from the config file is discovered but not scheduled. Modules without NAME
are plain helpers and ignored; modules starting with "_" are skipped (see _example.py for
the documented example). A module with NAME that breaks the interface raises InvalidSource.
"""
import importlib
import json
import os
import pkgutil
from dataclasses import dataclass, field
from datetime import date

CONFIG_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sources.json")


class InvalidSource(Exception):
    pass


@dataclass
class Source:
    name: str
    collect: object
    family: object = None
    module: str = ""
    backfill_from: object = None


@dataclass
class SourceConfig:
    interval_hours: float
    params: dict = field(default_factory=dict)


def load_source(module_name):
    """Import one module and validate it against the Source interface."""
    mod = importlib.import_module(module_name)
    name = getattr(mod, "NAME", None)
    if not isinstance(name, str) or not name:
        raise InvalidSource(f"{module_name}: NAME must be a non-empty string")
    if not callable(getattr(mod, "collect", None)):
        raise InvalidSource(f"{module_name}: missing collect(conn, params)")
    family = getattr(mod, "FAMILY", None)
    if family is not None and (not isinstance(family, int) or family < 1):
        raise InvalidSource(f"{module_name}: FAMILY must be a positive int (leading dotted segments)")
    backfill_from = getattr(mod, "BACKFILL_FROM", None)
    if backfill_from is not None:
        try:
            date.fromisoformat(backfill_from)
        except (TypeError, ValueError):
            raise InvalidSource(f"{module_name}: BACKFILL_FROM must be an ISO date string")
    return Source(name=name, collect=mod.collect, family=family, module=module_name, backfill_from=backfill_from)


def run_params(conn, source, params):
    """Params for one run of `source`: on its first run, add the backfill start (see module docstring)."""
    if not source.backfill_from:
        return params
    stored = conn.execute("SELECT 1 FROM metrics WHERE series >= ? AND series < ? LIMIT 1",
                          (source.name + ".", source.name + "/")).fetchone()
    if stored:
        return params
    return {**params, "backfill_from": max(source.backfill_from, params.get("backfill_from", ""))}


def discover(package="statking.sources"):
    """{name: Source} for every module in `package` that declares NAME."""
    pkg = importlib.import_module(package)
    found = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        full = f"{package}.{info.name}"
        if not hasattr(importlib.import_module(full), "NAME"):
            continue
        s = load_source(full)
        if s.name in found:
            raise InvalidSource(f"duplicate Source name {s.name!r}")
        found[s.name] = s
    return found


def load_config(path=None):
    path = path or os.environ.get("STATKING_SOURCES_CONFIG", CONFIG_DEFAULT)
    with open(path) as f:
        raw = json.load(f)
    return {n: SourceConfig(float(c["interval_hours"]), dict(c.get("params", {}))) for n, c in raw.items()}


def family_segments():
    """{Series prefix: leading segments identifying the Family} for discovered Sources that declare one."""
    return {s.name: s.family for s in discover().values() if s.family}
