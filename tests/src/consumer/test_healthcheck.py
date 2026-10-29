"""Healthcheck unit + CH integration tests (plan §4B, 4.2.6).

Unit tests cover the is_fresh matrix (pure, no I/O) and the B7 two-condition
decision: SIGTERM requires raw_events staleness AND no fresh consumer
heartbeat (pipeline_health) — staleness alone can be an upstream outage.
The ch-marked tests run
against LIVE ClickHouse (same CH_* env contract as
test_malformed_to_dead_letter.py): the healthcheck queries are hardcoded to
default.raw_events/default.pipeline_health, so they insert marker rows into
private probe tables and delete them in teardown. os.kill is always MOCKED —
never signal real PID 1 from CI; the asserts are that main() chose the
SIGTERM path (recorder captured (1, signal.SIGTERM)) and the return code.
"""

import os
import signal
import subprocess
from datetime import datetime, timedelta, timezone

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


def probe_table(monkeypatch, env: dict, stale_seconds: int) -> tuple[str, str]:
    """Hermetic per-process probe tables, healthcheck pointed at both.

    default.raw_events is shared with other ch suites (mv tests insert rows at
    toDateTime64(now(), 3) and leave them) and with a live consumer, so the
    stale probe would see fresh rows and never exercise its SIGTERM path. A
    private table makes every ch run deterministic regardless of that noise;
    the pipeline_health-shaped twin isolates the B7 heartbeat probe the same
    way (an empty heartbeat table is deliberately NOT liveness — fail-safe).
    """
    table = f"healthcheck_probe_{os.getpid()}"
    hb_table = f"healthcheck_heartbeat_probe_{os.getpid()}"
    query(f"DROP TABLE IF EXISTS {table}", env)
    query(f"DROP TABLE IF EXISTS {hb_table}", env)
    query(
        f"CREATE TABLE {table} (event String, inserted_at DateTime64(3, 'UTC')) "
        "ENGINE = MergeTree ORDER BY inserted_at",
        env,
    )
    query(
        f"CREATE TABLE {hb_table} ("
        "ts DateTime64(3, 'UTC'), source LowCardinality(String), "
        "metric LowCardinality(String), value Float64, detail String) "
        "ENGINE = MergeTree ORDER BY ts",
        env,
    )
    monkeypatch.setattr(healthcheck, "HEALTHCHECK_TABLE", table)
    monkeypatch.setattr(healthcheck, "HEALTHCHECK_HEARTBEAT_TABLE", hb_table)
    patch_ch(monkeypatch, env, stale_seconds)
    return table, hb_table


def insert_marker(env: dict, table: str, expr: str, marker: str) -> None:
    query(f"DELETE FROM {table} WHERE event = '{marker}'", env)
    query(
        f"INSERT INTO {table} (inserted_at, event) VALUES ({expr}, '{marker}')",
        env,
    )


def insert_heartbeat(env: dict, hb_table: str, expr: str) -> None:
    query(
        f"INSERT INTO {hb_table} (ts, source, metric, value, detail) "
        f"VALUES ({expr}, 'consumer', 'heartbeat', 1.0, '{{}}')",
        env,
    )


def drop_probes(env: dict, table: str, hb_table: str) -> None:
    query(f"DROP TABLE IF EXISTS {table}", env)
    query(f"DROP TABLE IF EXISTS {hb_table}", env)


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
    table, hb_table = probe_table(monkeypatch, env, stale_seconds=1)
    calls = recorder(monkeypatch)
    insert_marker(env, table, "now64(3) - INTERVAL 2 MINUTE", STALE_MARKER)
    try:
        # Empty heartbeat probe table = no liveness evidence -> wedge path.
        assert healthcheck.main() == 0
        assert calls == [(1, signal.SIGTERM)]
    finally:
        drop_probes(env, table, hb_table)


@pytest.mark.ch
def test_fresh_row_no_kill(monkeypatch):
    env = ch_env()
    table, hb_table = probe_table(monkeypatch, env, stale_seconds=300)
    calls = recorder(monkeypatch)
    insert_marker(env, table, "now64(3)", FRESH_MARKER)
    try:
        assert healthcheck.main() == 0
        assert calls == []
    finally:
        drop_probes(env, table, hb_table)


@pytest.mark.ch
def test_stale_row_with_fresh_heartbeat_no_kill(monkeypatch):
    # The B7 headline scenario: upstream Wikimedia outage — raw_events goes
    # stale while the consumer's heartbeat keeps ticking. A restart buys
    # nothing, so main() must NOT SIGTERM.
    env = ch_env()
    table, hb_table = probe_table(monkeypatch, env, stale_seconds=1)
    calls = recorder(monkeypatch)
    insert_marker(env, table, "now64(3) - INTERVAL 2 MINUTE", STALE_MARKER)
    insert_heartbeat(env, hb_table, "now64(3)")
    try:
        assert healthcheck.main() == 0
        assert calls == []
    finally:
        drop_probes(env, table, hb_table)


@pytest.mark.ch
def test_stale_row_with_stale_heartbeat_kills(monkeypatch):
    # Both signals stale = genuine wedge (event loop dead): restart.
    env = ch_env()
    table, hb_table = probe_table(monkeypatch, env, stale_seconds=1)
    calls = recorder(monkeypatch)
    insert_marker(env, table, "now64(3) - INTERVAL 2 MINUTE", STALE_MARKER)
    insert_heartbeat(env, hb_table, "now64(3) - INTERVAL 6 MINUTE")
    try:
        assert healthcheck.main() == 0
        assert calls == [(1, signal.SIGTERM)]
    finally:
        drop_probes(env, table, hb_table)


@pytest.mark.ch
def test_connection_failure_exits_1(monkeypatch):
    env = ch_env()
    table, hb_table = probe_table(monkeypatch, env, stale_seconds=300)
    monkeypatch.setattr(healthcheck, "CLICKHOUSE_PORT", "59999")
    calls = recorder(monkeypatch)
    try:
        assert healthcheck.main() == 1
        assert calls == []
    finally:
        drop_probes(env, table, hb_table)


# ---- unit: main() paths with a faked ClickHouse client (no CH needed) ----
# get_client is monkeypatched so main() never touches the network; the kill
# recorder proves the SIGTERM decision was made (Phase 6: close the 55% unit
# gap on main() without running ch). The fake scripts one first_row per
# query() call, in order — the B7 heartbeat probe is the second query, made
# ONLY when raw_events is stale (lazy: a fresh table never pays for it).

HEARTBEAT_SQL_FRAGMENT = "default.pipeline_health"


class _FakeHealthClient:
    """Duck-typed clickhouse_connect client: .query -> {first_row}, .close().

    Each script entry is the list to return as `first_row` (e.g. [datetime])
    or an Exception instance to raise (heartbeat-probe failure path). Query
    SQL is recorded so tests can pin the lazy-probe contract; a query beyond
    the script pops an empty list and IndexErrors the test.
    """

    def __init__(self, first_rows):
        self._script = list(first_rows)
        self.queries: list[str] = []
        self.closed = False

    def query(self, sql):
        self.queries.append(sql)
        row = self._script.pop(0)
        if isinstance(row, Exception):
            raise row
        return type("_QueryResult", (), {"first_row": row})()

    def close(self):
        self.closed = True


def _utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stale_row():
    return [_utcnow_naive() - timedelta(seconds=301)]


def test_main_stale_and_heartbeat_stale_kills(monkeypatch):
    patch_ch(monkeypatch, ch_env(), stale_seconds=300)
    calls = recorder(monkeypatch)
    fake = _FakeHealthClient([_stale_row(), _stale_row()])
    monkeypatch.setattr(healthcheck, "get_client", lambda **kw: fake)
    assert healthcheck.main() == 0
    assert calls == [(1, signal.SIGTERM)]
    assert fake.closed
    assert len(fake.queries) == 2
    assert HEARTBEAT_SQL_FRAGMENT in fake.queries[1]


def test_main_fresh_max_inserted_no_kill(monkeypatch):
    patch_ch(monkeypatch, ch_env(), stale_seconds=300)
    calls = recorder(monkeypatch)
    fake = _FakeHealthClient([[_utcnow_naive() - timedelta(seconds=60)]])
    monkeypatch.setattr(healthcheck, "get_client", lambda **kw: fake)
    assert healthcheck.main() == 0
    assert calls == []
    assert fake.closed
    # Lazy probe: fresh raw_events must not query pipeline_health at all.
    assert len(fake.queries) == 1


def test_main_stale_but_heartbeat_fresh_no_kill(monkeypatch):
    patch_ch(monkeypatch, ch_env(), stale_seconds=300)
    calls = recorder(monkeypatch)
    fresh_heartbeat = [_utcnow_naive() - timedelta(seconds=15)]
    fake = _FakeHealthClient([_stale_row(), fresh_heartbeat])
    monkeypatch.setattr(healthcheck, "get_client", lambda **kw: fake)
    assert healthcheck.main() == 0
    assert calls == []
    assert fake.closed


def test_main_heartbeat_probe_failure_failsafe_kills(monkeypatch):
    # No probe result is NOT liveness: fail safe to the plain wedge contract.
    patch_ch(monkeypatch, ch_env(), stale_seconds=300)
    calls = recorder(monkeypatch)
    fake = _FakeHealthClient([_stale_row(), RuntimeError("pipeline_health gone")])
    monkeypatch.setattr(healthcheck, "get_client", lambda **kw: fake)
    assert healthcheck.main() == 0
    assert calls == [(1, signal.SIGTERM)]
    assert fake.closed


def test_main_empty_heartbeat_table_kills(monkeypatch):
    # max(ts) over zero rows -> None -> no liveness evidence -> wedge path.
    patch_ch(monkeypatch, ch_env(), stale_seconds=300)
    calls = recorder(monkeypatch)
    fake = _FakeHealthClient([_stale_row(), [None]])
    monkeypatch.setattr(healthcheck, "get_client", lambda **kw: fake)
    assert healthcheck.main() == 0
    assert calls == [(1, signal.SIGTERM)]
    assert fake.closed


# ---- unit: heartbeat_alive matrix (opposite-None semantics vs is_fresh) ----


def test_heartbeat_alive_no_evidence_is_not_alive():
    assert not healthcheck.heartbeat_alive(None, NOW, 300)


def test_heartbeat_alive_fresh_tick():
    assert healthcheck.heartbeat_alive(NOW - timedelta(seconds=15), NOW, 300)


def test_heartbeat_alive_at_threshold():
    assert healthcheck.heartbeat_alive(NOW - timedelta(seconds=300), NOW, 300)


def test_heartbeat_alive_stale_tick():
    assert not healthcheck.heartbeat_alive(NOW - timedelta(seconds=301), NOW, 300)


def test_main_get_client_failure_exits_1(monkeypatch):
    patch_ch(monkeypatch, ch_env(), stale_seconds=300)
    calls = recorder(monkeypatch)

    def boom(**kwargs):
        raise RuntimeError("connect refused")

    monkeypatch.setattr(healthcheck, "get_client", boom)
    assert healthcheck.main() == 1
    assert calls == []


def test_main_bad_port_exits_1(monkeypatch):
    monkeypatch.setattr(healthcheck, "CLICKHOUSE_PORT", "not-a-number")
    calls = recorder(monkeypatch)
    assert healthcheck.main() == 1
    assert calls == []


def test_module_main_guard(monkeypatch):
    """The `python -m src.healthcheck` entry point (lines 89-90).

    runpy re-executes the module source in-process with __name__ == '__main__',
    so pytest-cov sees the guard; a bad port makes main() fail pre-network.
    Module attrs are rebuilt from env inside the fresh namespace, so the env
    var (not monkeypatched attrs) drives this.
    """
    import runpy

    monkeypatch.setenv("CLICKHOUSE_PORT", "not-a-number")
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("src.healthcheck", run_name="__main__")
    assert exc.value.code == 1
