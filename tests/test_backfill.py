"""Backfill on registration: a Source declares how far back it can go; the first run reaches that far, never further."""
import pytest

from statking import jobs, sources
from statking.sources import SourceConfig


def _recording_source(backfill_from="2020-01-01"):
    seen = []

    def collect(conn, params):
        seen.append(dict(params))
        return [("bf.a.x", "2024-03-01", 1.0), ("bf.a.x", "2024-03-02", 2.0)]

    return sources.Source(name="bf", collect=collect, backfill_from=backfill_from), seen


def _pkg(tmp_path, monkeypatch, name, body):
    (tmp_path / name).mkdir()
    (tmp_path / name / "__init__.py").write_text("")
    (tmp_path / name / "m.py").write_text(body)
    monkeypatch.syspath_prepend(str(tmp_path))
    return name


def test_source_declares_backfill_range_via_module_constant(tmp_path, monkeypatch):
    pkg = _pkg(tmp_path, monkeypatch, "bfpkg", "NAME='m'\nBACKFILL_FROM='2015-06-01'\ncollect=lambda c, p: []\n")
    assert sources.discover(pkg)["m"].backfill_from == "2015-06-01"


def test_invalid_backfill_declaration_is_rejected(tmp_path, monkeypatch):
    pkg = _pkg(tmp_path, monkeypatch, "bfbad", "NAME='m'\nBACKFILL_FROM='soon'\ncollect=lambda c, p: []\n")
    with pytest.raises(sources.InvalidSource, match="BACKFILL_FROM"):
        sources.discover(pkg)


def test_first_registration_backfills_to_declared_start(conn):
    src, seen = _recording_source("2020-01-01")
    jobs._source_job(src, SourceConfig(12, {}))(conn)
    assert seen[0]["backfill_from"] == "2020-01-01"


def test_config_may_narrow_but_not_exceed_declared_range(conn):
    src, seen = _recording_source("2020-01-01")
    jobs._source_job(src, SourceConfig(12, {"backfill_from": "2022-05-01"}))(conn)
    assert seen[0]["backfill_from"] == "2022-05-01"
    conn.execute("DELETE FROM metrics")
    jobs._source_job(src, SourceConfig(12, {"backfill_from": "1990-01-01"}))(conn)
    assert seen[1]["backfill_from"] == "2020-01-01"


def test_later_runs_do_not_backfill_again(conn):
    src, seen = _recording_source()
    job = jobs._source_job(src, SourceConfig(12, {}))
    job(conn)
    job(conn)
    assert "backfill_from" in seen[0] and "backfill_from" not in seen[1]


def test_backfill_is_idempotent_and_does_not_duplicate_points(conn):
    src, _ = _recording_source()
    job = jobs._source_job(src, SourceConfig(12, {}))
    job(conn)
    n = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    job(conn)
    job(conn)
    assert conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == n == 2


def test_source_without_declared_range_gets_params_untouched(conn):
    src, seen = _recording_source(None)
    jobs._source_job(src, SourceConfig(12, {"k": 1}))(conn)
    assert seen[0] == {"k": 1}


def test_place_metric_only_source_counts_as_registered(conn):
    from statking import db
    src, seen = _recording_source()
    src.collect = lambda c, p: seen.append(dict(p)) or []
    db.put_place_metrics(conn, [("bf.pop", "kiel", None, "2024-01-01", 1.0)])
    jobs._source_job(src, SourceConfig(12, {}))(conn)
    assert "backfill_from" not in seen[0]


def test_successful_empty_run_does_not_backfill_again_but_failed_one_does(conn):
    src, seen = _recording_source()
    src.collect = lambda c, p: seen.append(dict(p)) or []
    job = jobs._source_job(src, SourceConfig(12, {}))
    job(conn)
    conn.execute("INSERT OR REPLACE INTO job_runs VALUES ('bf', 'x', 0, 'boom')")
    job(conn)
    conn.execute("INSERT OR REPLACE INTO job_runs VALUES ('bf', 'x', 1, NULL)")
    job(conn)
    assert ["backfill_from" in s for s in seen] == [True, True, False]
