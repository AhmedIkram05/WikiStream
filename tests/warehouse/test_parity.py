"""Offline tests for warehouse/parity.py (no ClickHouse, no bq, no network).

The shell wrapper only execs this module; these tests cover the window math,
the freshness gate, JSON normalization, value comparison, the log contract,
and every main() exit path with faked CH/BQ/HTTP calls.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "warehouse"))
import parity  # noqa: E402

WINDOW = ["2026-09-10 08:00:00", "2026-09-10 10:00:00"]
WINDOW_END = "2026-09-10T10:00:00Z"
FRESH_OK = json.dumps([{"status": "success", "window_end": "2026-09-10T10:00:00.000Z"}])


# ---- window math ----


def test_default_window_trails_two_completed_hours():
    now = datetime(2026, 9, 10, 10, 37, 12, tzinfo=UTC)
    start, end, window_start, window_end = parity.window_bounds([], now=now)
    assert start == "2026-09-10 08:00:00"
    assert end == "2026-09-10 10:00:00"
    assert window_start == "2026-09-10T08:00:00Z"
    assert window_end == WINDOW_END


def test_explicit_window_is_verbatim():
    assert parity.window_bounds(WINDOW) == (
        "2026-09-10 08:00:00",
        "2026-09-10 10:00:00",
        "2026-09-10T08:00:00Z",
        WINDOW_END,
    )


@pytest.mark.parametrize("argv", [["one"], ["a", "b", "c"]])
def test_bad_arg_count_is_usage_error(argv):
    with pytest.raises(parity.UsageError, match="usage: parity.py"):
        parity.window_bounds(argv)


# ---- password / .env parsing ----


def test_password_env_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("CLICKHOUSE_PASSWORD=from-file\n")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "from-env")
    assert parity.read_password(str(env)) == "from-env"


def test_password_read_from_env_file_as_data(tmp_path, monkeypatch):
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    marker = tmp_path / "executed"
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "GCP_PROJECT=x\n"
        "CLICKHOUSE_PASSWORD=old\n"
        "CLICKHOUSE_PASSWORD=a=b&c\n"
        f"EVIL=$(touch {marker})\n"
    )
    # Last matching key wins; the value is data, not sourced shell.
    assert parity.read_password(str(env)) == "a=b&c"
    assert not marker.exists()


def test_password_missing_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    with pytest.raises(parity.ParityError, match="CLICKHOUSE_PASSWORD is not set"):
        parity.read_password(str(tmp_path / "missing.env"))


# ---- freshness / comparison primitives ----


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("2026-08-12T12:00:00.000Z", "2026-08-12T12:00:00Z"),
        ("2026-08-12 12:00:00", "2026-08-12T12:00:00Z"),
        ("2026-08-12T12:00:00.123456789Z", "2026-08-12T12:00:00Z"),
        ("", ""),
    ],
)
def test_norm_ts_equivalence(left, right):
    assert parity.norm_ts(left) == parity.norm_ts(right)


def test_freshness_gate_ok_empty_mismatch():
    assert parity.freshness_gate(FRESH_OK, WINDOW_END) == "OK"
    assert parity.freshness_gate("[]", WINDOW_END) == "EMPTY"
    bad = json.dumps([{"status": "failed", "window_end": "2026-09-10T09:00:00Z"}])
    assert parity.freshness_gate(bad, WINDOW_END).startswith("MISMATCH")


def test_bq_row_values_reads_named_columns_in_ch_order():
    # bq emits alphabetically; the caller passes the CH order.
    payload = json.dumps([{"bytes_delta": "123", "edits": "8"}])
    assert parity.bq_row_values(payload, "edits,bytes_delta") == "8 123"


def test_values_match():
    assert parity.values_match("8\t123\n", "8 123")
    assert not parity.values_match("8 123", "8 124")
    assert not parity.values_match("8", "8 123")
    assert not parity.values_match("x", "8")


def test_substitute_replaces_both_placeholders():
    assert parity.substitute("a {START} b {END}", "S", "E") == "a S b E"


def test_emit_log_appends_one_json_line(tmp_path):
    log_file = tmp_path / "parity.log"
    line = parity.emit_log(
        window_start="2026-09-10T08:00:00Z",
        window_end=WINDOW_END,
        freshness="ok",
        tables={"edits": "ok"},
        status="ok",
        log_file=log_file,
        now=datetime(2026, 9, 10, 10, 5, tzinfo=UTC),
    )
    assert json.loads(line) == {
        "ts": "2026-09-10T10:05:00Z",
        "window_start": "2026-09-10T08:00:00Z",
        "window_end": WINDOW_END,
        "freshness": "ok",
        "tables": {"edits": "ok"},
        "status": "ok",
    }
    assert log_file.read_text().strip() == line


# ---- main() with fakes ----


@pytest.fixture
def fake_io(tmp_path, monkeypatch):
    monkeypatch.setattr(parity, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(parity, "read_password", lambda: "pw")
    calls = {"ch": 0, "http": []}
    ch_outputs = ["8\t123\n", "8\t123\n", "7\n", "42\n"]
    bq_outputs = [
        FRESH_OK,
        json.dumps([{"edits": "8", "bytes_delta": "123"}]),
        json.dumps([{"edits": "8", "bytes_delta": "123"}]),
        json.dumps([{"edits": "7"}]),
        json.dumps([{"row_count": "42"}]),
    ]

    def fake_ch(sql, *, password, **kwargs):
        calls["ch"] += 1
        return ch_outputs[calls["ch"] - 1]

    def fake_bq(sql):
        return bq_outputs.pop(0)

    def fake_http(sql, *, password, **kwargs):
        calls["http"].append(sql)
        return "1\n" if sql.startswith("SELECT count()") else ""

    monkeypatch.setattr(parity, "ch_docker_query", fake_ch)
    monkeypatch.setattr(parity, "bq_query", fake_bq)
    monkeypatch.setattr(parity, "ch_http", fake_http)
    return calls


def test_main_ok_writes_log_and_health(fake_io, tmp_path):
    assert parity.main(WINDOW) == 0
    log = json.loads((tmp_path / parity.LOG_NAME).read_text().splitlines()[-1])
    assert log["status"] == "ok"
    assert log["freshness"] == "ok"
    assert log["window_start"] == "2026-09-10T08:00:00Z"
    assert log["window_end"] == WINDOW_END
    assert log["tables"] == {
        "edits": "ok",
        "top_pages": "ok",
        "sizes": "ok",
        "raw_sample": "ok",
    }
    assert len(fake_io["http"]) == 2  # INSERT + verification SELECT
    assert "INSERT INTO default.pipeline_health" in fake_io["http"][0]
    assert ", 1.0, " in fake_io["http"][0]
    assert fake_io["http"][1].startswith("SELECT count()")


def test_main_drift_exits_one_and_reports_zero(fake_io, tmp_path, monkeypatch):
    results = iter(["ok", "ok", "mismatch", "ok"])
    monkeypatch.setattr(parity, "compare_table", lambda *a, **k: next(results))

    assert parity.main(WINDOW) == 1
    log = json.loads((tmp_path / parity.LOG_NAME).read_text().splitlines()[-1])
    assert log["status"] == "drift"
    assert log["tables"]["sizes"] == "mismatch"
    assert ", 0.0, " in fake_io["http"][0]


def test_main_comparison_error_exits_one(fake_io, tmp_path, monkeypatch):
    results = iter(["ok", "error"])
    monkeypatch.setattr(parity, "compare_table", lambda *a, **k: next(results))

    assert parity.main(WINDOW) == 1
    log = json.loads((tmp_path / parity.LOG_NAME).read_text().splitlines()[-1])
    assert log["status"] == "error"
    assert log["tables"]["top_pages"] == "error"


def test_main_stale_freshness_exits_one(tmp_path, monkeypatch):
    monkeypatch.setattr(parity, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(parity, "read_password", lambda: "pw")
    monkeypatch.setattr(parity, "bq_query", lambda sql: "[]")
    monkeypatch.setattr(
        parity, "ch_docker_query", lambda *a, **k: pytest.fail("CH ran")
    )
    monkeypatch.setattr(parity, "ch_http", lambda *a, **k: pytest.fail("HTTP ran"))

    assert parity.main(WINDOW) == 1
    log = json.loads((tmp_path / parity.LOG_NAME).read_text().splitlines()[-1])
    assert log["freshness"] == "stale"
    assert log["status"] == "error"
    assert set(log["tables"].values()) == {"error"}


def test_main_health_write_failure_exits_one(fake_io, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise parity.ParityError("boom")

    monkeypatch.setattr(parity, "ch_http", boom)
    assert parity.main(WINDOW) == 1


def test_main_health_verification_missing_exits_one(fake_io, monkeypatch):
    monkeypatch.setattr(
        parity,
        "ch_http",
        lambda sql, **kw: "0\n" if sql.startswith("SELECT count()") else "",
    )
    assert parity.main(WINDOW) == 1


def test_main_missing_password_exits_one(monkeypatch):
    def missing():
        raise parity.ParityError("CLICKHOUSE_PASSWORD is not set")

    monkeypatch.setattr(parity, "read_password", missing)
    assert parity.main(WINDOW) == 1


def test_main_usage_error_exits_one(capsys):
    assert parity.main(["one"]) == 1
    assert "usage: parity.py" in capsys.readouterr().err


def test_compare_table_returns_error_on_query_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise parity.ParityError("ch down")

    monkeypatch.setattr(parity, "ch_docker_query", boom)
    assert (
        parity.compare_table(
            "edits",
            "export_edits.sql",
            "sum(edits) AS edits",
            "parity_bq_edits.sql",
            "edits",
            start="2026-09-10 08:00:00",
            end="2026-09-10 10:00:00",
            password="pw",
        )
        == "error"
    )


def test_compare_table_returns_error_on_malformed_bq_json(monkeypatch):
    monkeypatch.setattr(parity, "ch_docker_query", lambda *a, **k: "8\t123\n")
    monkeypatch.setattr(parity, "bq_query", lambda sql: "not json")
    assert (
        parity.compare_table(
            "edits",
            "export_edits.sql",
            "sum(edits) AS edits",
            "parity_bq_edits.sql",
            "edits",
            start="2026-09-10 08:00:00",
            end="2026-09-10 10:00:00",
            password="pw",
        )
        == "error"
    )


def test_compare_table_returns_error_on_missing_sql(monkeypatch, tmp_path):
    monkeypatch.setattr(parity, "SQL_DIR", tmp_path)
    assert (
        parity.compare_table(
            "edits",
            "export_edits.sql",
            "sum(edits) AS edits",
            "parity_bq_edits.sql",
            "edits",
            start="2026-09-10 08:00:00",
            end="2026-09-10 10:00:00",
            password="pw",
        )
        == "error"
    )
