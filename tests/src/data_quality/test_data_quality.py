"""Unit tests for src.data_quality (no ClickHouse required).

Covers every exit path and all 11 checks with a fake clickhouse-connect client.
The CH-marked subprocess contract lives in test_data_quality_ch.py.
"""

import json
import runpy

import clickhouse_connect
import pytest

from src.data_quality import SQL_COLUMNS, main

WINDOW_END = "2026-08-01T01:00:00+00:00"


class _Result:
    def __init__(self, row):
        self.first_row = row


class _FakeClient:
    def __init__(self, row=None, error=None):
        self.row = row
        self.error = error
        self.rows = []

    def query(self, sql):
        if self.error is not None:
            raise self.error
        return _Result(self.row)

    def insert(self, *args, **kwargs):
        self.rows.extend(kwargs.get("data", []))


def _row(**overrides):
    base = {
        "row_count": 200,
        "null_wiki": 0,
        "null_title": 0,
        "null_event_type": 0,
        "null_is_bot": 0,
        "null_length_new": 0,
        "null_event_timestamp": 0,
        "bad_event_type": 0,
        "median_lag": 60,
        "max_skew": 0,
        "wiki_count": 200,
        "bot_ratio": 0.5,
    }
    base.update(overrides)
    return tuple(base[column] for column in SQL_COLUMNS)


def _env(monkeypatch, **overrides):
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "test")
    monkeypatch.setenv("DATA_QUALITY_WINDOW_END", WINDOW_END)
    monkeypatch.setenv("DATA_QUALITY_ROW_MIN", "1")
    monkeypatch.setenv("DATA_QUALITY_ROW_MAX", "500000")
    for key, value in overrides.items():
        monkeypatch.setenv(key, str(value))


def _patch_client(monkeypatch, client=None, error=None):
    if error is not None:

        def boom(**kwargs):
            raise error

        monkeypatch.setattr(clickhouse_connect, "get_client", boom)
    else:
        monkeypatch.setattr(clickhouse_connect, "get_client", lambda **kwargs: client)


def _verdict(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_missing_password_returns_1(capsys, monkeypatch):
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    assert main() == 1
    assert "CLICKHOUSE_PASSWORD is required" in capsys.readouterr().err


def test_connect_failure_prints_failure_verdict(capsys, monkeypatch):
    _env(monkeypatch)
    _patch_client(monkeypatch, error=RuntimeError("ch down"))
    assert main() == 1
    verdict = _verdict(capsys)
    assert verdict["success"] is False
    assert verdict["error"] == "ch down"


def test_missing_table_fails_with_missing_error(capsys, monkeypatch):
    _env(monkeypatch)
    _patch_client(
        monkeypatch,
        client=_FakeClient(error=RuntimeError("Unknown table default.nope")),
    )
    assert main() == 1
    verdict = _verdict(capsys)
    assert verdict["success"] is False
    assert verdict["expectations_failed"] == 1
    assert "missing" in verdict["error"]


def test_generic_query_error_fails(capsys, monkeypatch):
    _env(monkeypatch)
    _patch_client(
        monkeypatch,
        client=_FakeClient(error=RuntimeError("Code 60: syntax error")),
    )
    assert main() == 1
    verdict = _verdict(capsys)
    assert verdict["success"] is False
    assert verdict["expectations_failed"] == 1
    assert "missing" not in verdict["error"]


def test_zero_rows_skips_successfully(capsys, monkeypatch):
    _env(monkeypatch)
    _patch_client(monkeypatch, client=_FakeClient(row=_row(row_count=0)))
    assert main() == 0
    verdict = _verdict(capsys)
    assert verdict["skipped"] is True
    assert verdict["success"] is True


def test_row_bounds_violation_fails(capsys, monkeypatch):
    _env(monkeypatch, DATA_QUALITY_ROW_MIN=100, DATA_QUALITY_ROW_MAX=200)
    _patch_client(monkeypatch, client=_FakeClient(row=_row(row_count=10)))
    assert main() == 1
    verdict = _verdict(capsys)
    assert verdict["success"] is False
    assert verdict["expectations_passed"] == 0
    assert verdict["expectations_failed"] == 1
    assert "outside [100, 200]" in verdict["error"]


def test_all_checks_pass(capsys, monkeypatch):
    _env(monkeypatch)
    _patch_client(monkeypatch, client=_FakeClient(row=_row()))
    assert main() == 0
    verdict = _verdict(capsys)
    assert verdict["success"] is True
    assert verdict["expectations_passed"] == 11
    assert verdict["expectations_failed"] == 0
    assert verdict["row_count"] == 200


@pytest.mark.parametrize(
    "override",
    [
        {"null_wiki": 1},
        {"null_title": 1},
        {"null_event_type": 1},
        {"null_is_bot": 1},
        {"null_length_new": 1},
        {"null_event_timestamp": 1},
        {"bad_event_type": 1},
        {"median_lag": 301},
        {"max_skew": 301},
        {"wiki_count": 100},
        {"bot_ratio": 0.96},
        {"bot_ratio": 0.04},
    ],
)
def test_each_failed_check(capsys, monkeypatch, override):
    _env(monkeypatch)
    _patch_client(monkeypatch, client=_FakeClient(row=_row(**override)))
    assert main() == 1
    verdict = _verdict(capsys)
    assert verdict["success"] is False
    assert verdict["expectations_failed"] == 1


def test_stdout_is_one_json_line(capsys, monkeypatch):
    _env(monkeypatch)
    _patch_client(monkeypatch, client=_FakeClient(row=_row()))
    assert main() == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    json.loads(lines[0])


def test_module_main_guard(monkeypatch):
    """__main__ guard via in-process runpy, so pytest-cov sees the line.

    A missing password makes main() fail before any network call.
    """
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("src.data_quality", run_name="__main__")
    assert exc.value.code == 1
