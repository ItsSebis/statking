"""Source interface: discovery enforces it, config drives schedule/params, weather runs through it."""
import json
import textwrap
from datetime import date

import pytest

from statking import cities, jobs, sources
from statking.sources import chess, opendata_sh, steam, weather, wg_gesucht, wikipedia


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
    assert "kiel" not in found  # nothing but modules with NAME are Sources


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


# --- remaining Sources -----------------------------------------------------------------------

def _schedule(tmp_path, monkeypatch, name, params, interval=1):
    cfg = tmp_path / "s.json"
    cfg.write_text(json.dumps({name: {"interval_hours": interval, "params": params}}))
    monkeypatch.setenv("STATKING_SOURCES_CONFIG", str(cfg))
    jobs.register_sources()


def _run(conn, name):
    jobs.run_due(conn, [name], force=True)
    row = conn.execute("SELECT ok, note FROM job_runs WHERE job=?", (name,)).fetchone()
    assert row[0] == 1, row[1]


def _series(conn, prefix):
    return dict(conn.execute("SELECT series||'@'||ts, value FROM metrics WHERE series LIKE ?", (prefix + "%",)).fetchall())


def test_every_existing_source_is_discovered_and_scheduled_from_the_repo_config():
    found = sources.discover()
    cfg = sources.load_config()
    for name in ("rent", "steam", "wiki", "chess", "stat", "weather"):
        assert name in found and name in cfg
    assert cfg["steam"].interval_hours == 0.25
    assert cfg["chess"].interval_hours == 6
    assert cfg["stat"].interval_hours == 168
    assert cfg["rent"].interval_hours == 24
    assert cfg["wiki"].interval_hours == 24


def test_rent_declares_its_family_so_pairs_inside_a_city_category_are_derived():
    assert sources.family_segments()["rent"] == 3


def test_wiki_keeps_series_names_and_resumes_from_last_stored_day(conn, tmp_path, monkeypatch):
    calls = []

    def views(lang, title, start, end):
        calls.append((lang, title, start))
        return [("2024-01-01", 5), ("2024-01-02", 7)]
    monkeypatch.setattr(wikipedia, "daily_views", views)
    monkeypatch.setattr("time.sleep", lambda s: None)
    _schedule(tmp_path, monkeypatch, "wiki", {"articles": ["de:Trennung", "en:Santa Claus"], "backfill_from": "2024-01-01"})
    _run(conn, "wiki")
    got = _series(conn, "wiki.")
    assert got["wiki.de.Trennung@2024-01-02"] == 7
    assert got["wiki.en.Santa_Claus@2024-01-01"] == 5
    assert calls[0] == ("de", "Trennung", "20240101")
    _run(conn, "wiki")
    assert calls[2] == ("de", "Trennung", "20240102")  # resumes at the last stored day


def test_chess_writes_games_and_rating_series_and_skips_without_user(conn, tmp_path, monkeypatch):
    game = {"url": "https://chess.com/g/1", "end_time": 1704110400, "time_class": "rapid",
            "white": {"username": "Me", "result": "win", "rating": 1500},
            "black": {"username": "Them", "result": "resigned", "rating": 1480}}
    monkeypatch.setattr(chess, "archives", lambda user: ["https://api.chess.com/pub/player/me/games/2024/01"])
    monkeypatch.setattr(chess, "month", lambda url: {"games": [game]})
    monkeypatch.setattr("time.sleep", lambda s: None)
    _schedule(tmp_path, monkeypatch, "chess", {"user": "me"})
    _run(conn, "chess")
    assert _series(conn, "chess.rating.rapid") == {"chess.rating.rapid@2024-01-01T12:00:00+00:00": 1500}
    assert conn.execute("SELECT COUNT(*) FROM chess_games").fetchone()[0] == 1
    _schedule(tmp_path, monkeypatch, "chess", {})
    _run(conn, "chess")  # no user configured: nothing happens, no failure


KIEL_CSV = "Land;Stadt;Jahr;Einwohner Gesamt\nSH;Kiel;2022;247.000\nSH;Kiel;2023;250.000\n"


def test_stat_keeps_series_names(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(opendata_sh, "DATASETS", {"kiel.bevoelkerung": ("x", "kiel_yearly")})
    monkeypatch.setattr(opendata_sh, "DISTRICT_DATASETS", {})
    monkeypatch.setattr(opendata_sh, "fetch_csv", lambda cid: KIEL_CSV)
    _schedule(tmp_path, monkeypatch, "stat", {})
    _run(conn, "stat")
    assert _series(conn, "stat.") == {"stat.kiel.bevoelkerung.einwohner_gesamt@2022-12-31": 247000.0,
                                      "stat.kiel.bevoelkerung.einwohner_gesamt@2023-12-31": 250000.0}


def test_steam_keeps_series_names_and_tracked_apps_come_from_config(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(steam, "global_online", lambda: [(1704110400, 1000)])
    monkeypatch.setattr(steam, "top_games", lambda: [(1, 730, 50, 60), (2, 570, 30, 40)])
    monkeypatch.setattr(steam, "app_name", lambda appid: f"app{appid}")
    monkeypatch.setattr(steam, "current_players", lambda appid: appid * 2)
    monkeypatch.setattr("time.sleep", lambda s: None)
    _schedule(tmp_path, monkeypatch, "steam", {"tracked": [730]})
    _run(conn, "steam")
    got = _series(conn, "steam.")
    assert got["steam.online@2024-01-01T12:00:00+00:00"] == 1000
    assert [v for k, v in got.items() if k.startswith("steam.in_game_top100")] == [80]
    assert [v for k, v in got.items() if k.startswith("steam.players.730")] == [1460]
    assert not [k for k in got if k.startswith("steam.players.570")]


def test_rent_scrapes_via_params_and_records_series(conn, tmp_path, monkeypatch):
    item = {"id": "1", "title": "t", "district": "Gaarden", "street": None, "flat_desc": "WG", "price_eur": 400,
            "size_m2": 20.0, "available_from": None, "url": "u"}
    seen = []
    monkeypatch.setattr(wg_gesucht, "scrape", lambda city, cat, **kw: (seen.append((city, cat, kw)) or [item], True))
    monkeypatch.setattr("statking.geo.geocode_missing", lambda c: None)
    monkeypatch.setattr("time.sleep", lambda s: None)
    _schedule(tmp_path, monkeypatch, "rent", {"cities": ["kiel"], "max_pages": 3, "pause_seconds": 0})
    _run(conn, "rent")
    assert {s[0] for s in seen} == {"kiel"}
    assert seen[0][2]["max_pages"] == 3
    got = _series(conn, "rent.kiel.wg.")
    assert any(k.startswith("rent.kiel.wg.active@") and v == 1 for k, v in got.items())


def test_rent_block_fails_the_job_so_it_retries_next_interval(conn, tmp_path, monkeypatch):
    def blocked(city, cat, **kw):
        raise wg_gesucht.Blocked("HTTP 429")
    monkeypatch.setattr(wg_gesucht, "scrape", blocked)
    _schedule(tmp_path, monkeypatch, "rent", {"cities": ["kiel"], "pause_seconds": 0})
    jobs.run_due(conn, ["rent"], force=True)
    assert conn.execute("SELECT ok FROM job_runs WHERE job='rent'").fetchone()[0] == 0


def test_last_run_persistence_still_prevents_extra_scrapes(conn, tmp_path, monkeypatch):
    _schedule(tmp_path, monkeypatch, "wiki", {"articles": []}, interval=24)
    jobs.run_due(conn, ["wiki"], force=True)
    assert not jobs.due(conn, "wiki")
