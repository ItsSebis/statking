"""Review State: the human judgement on a pair (unreviewed, Confirmed Link, dismissed).

Stored in pair_reviews, keyed by the pair alone (not Lag, not Snapshot), so it survives every
re-run. 'unreviewed' is the absence of a row."""
from . import db

STATES = ("unreviewed", "confirmed", "dismissed")


def set_review(conn, series_a, series_b, state, note=None):
    """Set a pair's Review State. A Confirmed Link needs a note (why a mechanism is plausible).
    The pair is unordered; 'unreviewed' clears it."""
    if state not in STATES:
        raise ValueError(f"state must be one of {', '.join(STATES)}")
    note = (note or "").strip() or None
    if state == "confirmed" and note is None:
        raise ValueError("a Confirmed Link needs a note")
    a, b = sorted((series_a, series_b))
    if state == "unreviewed":
        conn.execute("DELETE FROM pair_reviews WHERE series_a=? AND series_b=?", (a, b))
    else:
        conn.execute("INSERT OR REPLACE INTO pair_reviews (series_a, series_b, state, note, ts) VALUES (?,?,?,?,?)",
                     (a, b, state, note, db.now()))
    conn.commit()
