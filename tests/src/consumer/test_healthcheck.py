"""Healthcheck unit + CH integration tests (plan §4B, 4.2.6).

Unit tests cover the is_fresh matrix (pure, no I/O). The ch-marked tests run
against LIVE ClickHouse (same CH_* env contract as
test_malformed_to_dead_letter.py): the healthcheck query is hardcoded to
default.raw_events, so they insert marker rows into the real table and delete
them in teardown. os.kill is always MOCKED — never signal real PID 1 from CI;
the asserts are that main() chose the SIGTERM path (recorder captured
(1, signal.SIGTERM)) and the return code.
"""

import os
import signal
import subprocess
from datetime import datetime, timedelta

import pytest

from src import healthcheck

STALE_MARKER = '{"healthcheck_test":"stale","timestamp":"2026-08-13T00:00:00Z"}'
FRESH_MARKER = '{"healthcheck_test":"fresh","timestamp":"2026-08-13T00:00:00Z"}'
NOW = datetime(2026, 8, 13, 12, 0, 0)


def ch_env():
    return {
        "CH_HOST": os.environ.get("CH_HOST", "localhost"),
        "CH_PORT": os.environ.get("CH_PORT", "8123"),
        "CH_USER": os.environ.get("CH_USER", "wikistream"),
        "CH_PASSWORD": os.environ.get("CH_PASSWORD", "wikistream_dev_password"),
    }


def query(sql: str, env: dict) -> str:
    """Run SQL via curl against the ClickHouse HTTP API; return stdout."""
    out = subprocess.run(
        [
            "curl",
            "-sS",
            "--fail-with-body",
            "-H",
            f"X-ClickHouse-User: {env['CH_USER']}",
            "-H",
            f"X-ClickHouse-Key: {env['CH_PASSWORD']}",
            "-X",
            "POST",
            "--data-binary",
            sql,
            f"http://{env['CH_HOST']}:{env['CH_PORT']}/",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        raise AssertionError(
            f"SQL failed (curl {out.returncode}): "
            f"{out.stderr.strip() or out.stdout.strip()}"
        )
    return out.stdout


def patch_ch(monkeypatch, env: dict, stale_seconds: int) -> None:
    monkeypatch.setattr(healthcheck, "CLICKHOUSE_HOST", env["CH_HOST"])
    monkeypatch.setattr(healthcheck, "CLICKHOUSE_PORT", int(env["CH_PORT"]))
    monkeypatch.setattr(healthcheck, "CLICKHOUSE_USER", env["CH_USER"])
    monkeypatch.setattr(healthcheck, "CLICKHOUSE_PASSWORD", env["CH_PASSWORD"])
    monkeypatch.setattr(healthcheck, "HEALTH_STALE_SECONDS", stale_seconds)


def recorder(monkeypatch) -> list:
    calls: list = []

    def fake_kill(pid, sig):
        calls.append((pid, sig))

    monkeypatch.setattr(healthcheck.os, "kill", fake_kill)
    return calls


def probe_table(monkeypatch, env: dict, stale_seconds: int) -> str:
    """Hermetic per-process probe table, healthcheck pointed at it.

    default.raw_events is shared with other ch suites (mv tests insert rows at
    toDateTime64(now(), 3) and leave them) and with a live consumer, so the
    stale probe would see fresh rows and never exercise its SIGTERM path. A
    private table makes every ch run deterministic regardless of that noise.
    """
    table = f"healthcheck_probe_{os.getpid()}"
    query(f"DROP TABLE IF EXISTS {table}", env)
    query(
        f"CREATE TABLE {table} (event String, inserted_at DateTime64(3, 'UTC')) "
        "ENGINE = MergeTree ORDER BY inserted_at",
        env,
    )
    monkeypatch.setattr(healthcheck, "HEALTHCHECK_TABLE", table)
    patch_ch(monkeypatch, env, stale_seconds)
    return table


def insert_marker(env: dict, table: str, expr: str, marker: str) -> None:
    query(f"DELETE FROM {table} WHERE event = '{marker}'", env)
    query(
        f"INSERT INTO {table} (inserted_at, event) VALUES ({expr}, '{marker}')",
        env,
    )


# ---- unit: is_fresh matrix (at-threshold = fresh, docstring'd) ----


def test_is_fresh_no_rows():
    assert healthcheck.is_fresh(None, NOW, 300)


def test_is_fresh_under_threshold():
    assert healthcheck.is_fresh(NOW - timedelta(seconds=299), NOW, 300)


def test_is_fresh_at_threshold():
    assert healthcheck.is_fresh(NOW - timedelta(seconds=300), NOW, 300)


def test_is_fresh_over_threshold():
    assert not healthcheck.is_fresh(NOW - timedelta(seconds=301), NOW, 300)


def test_is_fresh_zero_stale_seconds():
    assert healthcheck.is_fresh(NOW, NOW, 0)
    assert not healthcheck.is_fresh(NOW - timedelta(seconds=1), NOW, 0)


def test_is_fresh_negative_stale_seconds():
    assert healthcheck.is_fresh(NOW, NOW, -5)
    assert not healthcheck.is_fresh(NOW - timedelta(seconds=1), NOW, -5)


def test_is_fresh_future_max_inserted():
    assert healthcheck.is_fresh(NOW + timedelta(seconds=60), NOW, 300)


# ---- ch-marked: main() against live ClickHouse ----


@pytest.mark.ch
def test_stale_row_kills_pid1(monkeypatch):
    env = ch_env()
    table = probe_table(monkeypatch, env, stale_seconds=1)
    calls = recorder(monkeypatch)
    insert_marker(env, table, "now64(3) - INTERVAL 2 MINUTE", STALE_MARKER)
    try:
        assert healthcheck.main() == 0
        assert calls == [(1, signal.SIGTERM)]
    finally:
        query(f"DROP TABLE IF EXISTS {table}", env)


@pytest.mark.ch
def test_fresh_row_no_kill(monkeypatch):
    env = ch_env()
    table = probe_table(monkeypatch, env, stale_seconds=300)
    calls = recorder(monkeypatch)
    insert_marker(env, table, "now64(3)", FRESH_MARKER)
    try:
        assert healthcheck.main() == 0
        assert calls == []
    finally:
        query(f"DROP TABLE IF EXISTS {table}", env)


@pytest.mark.ch
def test_connection_failure_exits_1(monkeypatch):
    env = ch_env()
    table = probe_table(monkeypatch, env, stale_seconds=300)
    monkeypatch.setattr(healthcheck, "CLICKHOUSE_PORT", "59999")
    calls = recorder(monkeypatch)
    try:
        assert healthcheck.main() == 1
        assert calls == []
    finally:
        query(f"DROP TABLE IF EXISTS {table}", env)
