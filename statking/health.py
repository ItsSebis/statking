"""Source Health: per-Source last success and consecutive failures (job_runs only keeps the latest run).
Outage gaps need no code here: the Search compares only buckets with real observations, so a missing
bucket is never filled."""
from . import db


def record(conn, name, ok, error=None):
    """Update `name`'s health after one run: a success resets the failure count, a failure adds one."""
    if ok:
        conn.execute("""INSERT INTO source_health (source, last_success, consecutive_failures, last_error)
                        VALUES (?,?,0,NULL) ON CONFLICT(source) DO UPDATE SET
                        last_success=excluded.last_success, consecutive_failures=0, last_error=NULL""", (name, db.now()))
    else:
        conn.execute("""INSERT INTO source_health (source, last_success, consecutive_failures, last_error)
                        VALUES (?,NULL,1,?) ON CONFLICT(source) DO UPDATE SET
                        consecutive_failures=consecutive_failures+1, last_error=excluded.last_error""", (name, error))


def all_health(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM source_health ORDER BY source")]
