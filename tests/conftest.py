"""Synthetic Series fixtures. All generators take a fixed seed so tests are deterministic."""
import random
from datetime import date, timedelta

import pytest

from statking import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    yield c
    c.close()


def _days(n, start=date(2024, 1, 1)):
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def put_series(conn, name, values, start=date(2024, 1, 1)):
    db.put_metrics(conn, [(name, d, v) for d, v in zip(_days(len(values), start), values)])
    conn.commit()


def random_walk(rng, n):
    x, out = 0.0, []
    for _ in range(n):
        x += rng.gauss(0, 1)
        out.append(x)
    return out


def related_walks(seed, n=200, strength=0.9):
    """Two random walks whose daily *changes* are driven by the same shocks."""
    rng = random.Random(seed)
    shocks = [rng.gauss(0, 1) for _ in range(n)]
    a, b, xa, xb = [], [], 0.0, 0.0
    for s in shocks:
        xa += s
        xb += strength * s + (1 - strength) * rng.gauss(0, 1)
        a.append(xa)
        b.append(xb)
    return a, b


@pytest.fixture
def seed_series(conn):
    def _seed(**series):
        for name, values in series.items():
            put_series(conn, name.replace("__", "."), values)
    return _seed


def ar1_walk(rng, n, phi=0.8):
    """Random walk whose daily *changes* are AR(1): autocorrelated Adjusted Series, independent across calls."""
    d, x, out = 0.0, 0.0, []
    for _ in range(n):
        d = phi * d + rng.gauss(0, 1)
        x += d
        out.append(x)
    return out
