"""Unit tests for gx.suite.main() (no ClickHouse required).

Covers the non-GX verdict paths: missing password, connection failure,
count-query failures (missing table vs generic), zero-row skip, and
row-bounds violation. The GX-validation path itself needs a live stack and is
exercised by test_gx_suite.py against fixtures; coverage for suite.py's GX
lines is completed by the ch run (AC4 combines both).
"""

import json
import sys
from pathlib import Path

import clickhouse_connect
import pytest

# pythonpath in pytest.ini only exposes consumer/gx (the `src` layout); gx.suite
# lives at repo root, so insert it explicitly (mirrors tests/conftest.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# gx.suite imports great_expectations at module level, which only the gx venv
# has. The unit-tests CI job collects this file under the *consumer* venv, so
# skip the module there instead of failing collection (the gx venv runs the
# unit half in CI via its own `-m "not ch" tests/gx` step).
pytest.importorskip("great_expectations")

from gx.suite import main


class _RowCount:
    def __init__(self, n):
        self.first_row = (n,)


class _FakeClient:
    def __init__(self, n=0, error=None):
        self._n = n
        self._error = error

    def query(self, sql):
        if self._error is not None:
            raise self._error
        return _RowCount(self._n)

    def insert(self, *args, **kwargs):
        # main() registers report_status(verdict, client) via atexit; the
        # callback fires at interpreter exit with the fake client — a no-op
        # insert keeps the shutdown log clean.
        pass


def _patch_client(monkeypatch, client=None, error=None):
    if error is not None:
        def boom(**kw):
            raise error
        monkeypatch.setattr(clickhouse_connect, "get_client", boom)
    else:
        monkeypatch.setattr(
            clickhouse_connect, "get_client", lambda **kw: client
        )


def _verdict(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_missing_password_returns_1(capsys, monkeypatch):
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    assert main() == 1
    assert "CLICKHOUSE_PASSWORD is required" in capsys.readouterr().err


def test_connect_failure_prints_failure_verdict(capsys, monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "test")
    _patch_client(monkeypatch, error=RuntimeError("ch down"))
    assert main() == 1
    v = _verdict(capsys)
    assert v["success"] is False
    assert v["error"] == "ch down"


def test_missing_table_fails_with_missing_error(capsys, monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "test")
    _patch_client(
        monkeypatch, client=_FakeClient(error=RuntimeError("Unknown table default.nope"))
    )
    assert main() == 1
    v = _verdict(capsys)
    assert v["success"] is False
    assert "missing" in v["error"]
    assert v["expectations_failed"] == 1


def test_generic_count_error_fails(capsys, monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "test")
    _patch_client(
        monkeypatch, client=_FakeClient(error=RuntimeError("Code 60: syntax error"))
    )
    assert main() == 1
    v = _verdict(capsys)
    assert v["success"] is False
    assert "missing" not in v["error"]


def test_zero_rows_skips_successfully(capsys, monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "test")
    _patch_client(monkeypatch, client=_FakeClient(n=0))
    assert main() == 0
    v = _verdict(capsys)
    assert v["skipped"] is True
    assert v["success"] is True


def test_row_bounds_violation_fails(capsys, monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "test")
    monkeypatch.setenv("GX_ROW_MIN", "100")
    monkeypatch.setenv("GX_ROW_MAX", "200")
    _patch_client(monkeypatch, client=_FakeClient(n=10))
    assert main() == 1
    v = _verdict(capsys)
    assert v["success"] is False
    assert "outside [100, 200]" in v["error"]
