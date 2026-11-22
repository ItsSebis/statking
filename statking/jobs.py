"""Collectors + scheduler. Each job has its own interval and its last run is
persisted (job_runs), so restarting a container never triggers an extra scrape."""
import os
import time
from datetime import datetime, timezone

from . import db, search, sources

H = lambda name, default: float(os.environ.get(name, default))


def job_search(conn):
    snap = search.run_search(conn)
    print(f"search: Snapshot {snap['id']}, {snap['hypotheses']} Hypotheses, {snap['real_count']} real")


# name -> (function, default interval in hours, env var overriding it). Sources add themselves from the config file.
JOBS = {
    "search": (job_search, 24, "SEARCH_INTERVAL_HOURS"),  # nightly; the Snapshots are the history
}


def _source_job(source, cfg):
    def job(conn):
        db.put_metrics(conn, source.collect(conn, cfg.params))
        conn.commit()
    return job


def register_sources():
    """Schedule every discovered Source that the sources config file lists (interval + params from there)."""
    cfg = sources.load_config()
    for name, src in sources.discover().items():
        if name in cfg:
            JOBS[name] = (_source_job(src, cfg[name]), cfg[name].interval_hours, None)


register_sources()


def due(conn, name, force=False):
    _, default, env = JOBS[name]
    row = conn.execute("SELECT ts FROM job_runs WHERE job=?", (name,)).fetchone()
    if force or row is None:
        return True
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).total_seconds()
    return age >= (H(env, default) if env else default) * 3600


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
