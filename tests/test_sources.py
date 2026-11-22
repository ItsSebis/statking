"""Source interface: discovery enforces it, config drives schedule/params, weather runs through it."""
import json
import textwrap
from datetime import date

import pytest

from statking import cities, jobs, sources
from statking.sources import weather


def _package(tmp_path, monkeypatch, name, **modules):
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for mod, body in modules.items():
        (pkg / f"{mod}.py").write_text(textwrap.dedent(body))
    monkeypatch.syspath_prepend(str(tmp_path))
    return name


def test_discovery_finds_weather_and_skips_helper_modules():
    found = sources.discover()
    assert "weather" in found
    assert found["weather"].name == "weather"
    assert "chess" not in found  # plain helper module: no NAME, not a Source


def test_discovery_rejects_a_source_missing_collect(tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, "badsrc_a", broken="NAME = 'broken'\n")
    with pytest.raises(sources.InvalidSource, match="collect"):
        sources.discover(pkg)


def test_discovery_accepts_a_complete_source_and_optional_family(tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, "goodsrc", ok="""
        NAME = 'ok'
        FAMILY = 2
        def collect(conn, params):
            return [('ok.a.b', '2024-01-01', 1.0)]
    """)
    s = sources.discover(pkg)["ok"]
    assert s.family == 2
    assert s.collect(None, {}) == [("ok.a.b", "2024-01-01", 1.0)]


def test_documented_example_satisfies_the_interface():
    assert sources.load_source("statking.sources._example").name == "example"


def test_config_gives_schedule_and_params(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"weather": {"interval_hours": 6, "params": {"backfill_from": "2020-01-01"}}}))
    cfg = sources.load_config(str(p))
    assert cfg["weather"].interval_hours == 6
    assert cfg["weather"].params["backfill_from"] == "2020-01-01"


def test_repo_config_schedules_weather():
    assert sources.load_config()["weather"].interval_hours == 12


def test_weather_runs_through_the_interface(conn, tmp_path, monkeypatch):
    cfg = tmp_path / "s.json"
    cfg.write_text(json.dumps({"weather": {"interval_hours": 12,
                                           "params": {"backfill_from": "2024-01-01", "pause_seconds": 0}}}))
    monkeypatch.setenv("STATKING_SOURCES_CONFIG", str(cfg))
    monkeypatch.setattr(cities, "CITIES", {"kiel": cities.CITIES["kiel"]})
    monkeypatch.setattr(weather, "fetch_hourly", lambda station, start, end: [
        {"timestamp": f"2024-01-01T{h:02d}:00:00+00:00", "temperature": 10.0 + h, "precipitation": 0.5,
         "sunshine": 1, "wind_gust_speed": h} for h in range(24)])
    monkeypatch.setattr(weather, "_today", lambda: date(2024, 1, 3))
    jobs.register_sources()
    assert jobs.JOBS["weather"][1] == 12
    jobs.run_due(conn, ["weather"], force=True)
    got = dict(conn.execute("SELECT series, value FROM metrics WHERE ts='2024-01-01'").fetchall())
    assert got["weather.kiel.temp_mean"] == pytest.approx(21.5)
    assert got["weather.kiel.temp_min"] == 10.0
    assert got["weather.kiel.temp_max"] == 33.0
    assert got["weather.kiel.precip_mm"] == pytest.approx(12.0)
    assert got["weather.kiel.sunshine_min"] == 24
    assert got["weather.kiel.wind_gust_max"] == 23
    assert conn.execute("SELECT ok FROM job_runs WHERE job='weather'").fetchone()[0] == 1
