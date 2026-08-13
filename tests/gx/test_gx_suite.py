"""CH-marked integration tests for the gx suite against local ClickHouse.

Each test builds a fixture table in default.*, runs gx/suite.py as a real
subprocess (exercising the exit-code contract), and drops the table in teardown.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import clickhouse_connect
import pytest

pytestmark = pytest.mark.ch

REPO_ROOT = Path(__file__).resolve().parents[2]
HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "wikistream_dev_password")

CREATE_SQL = """CREATE TABLE IF NOT EXISTS {table} (
    event String,
    wiki String,
    title String,
    user String,
    event_type String,
    is_bot UInt8,
    length_new UInt8,
    event_timestamp DateTime64(3, 'UTC'),
    inserted_at DateTime64(3, 'UTC')
) ENGINE = MergeTree
PARTITION BY toYYYYMMDD(inserted_at)
ORDER BY inserted_at"""

COLUMNS = [
    "event",
    "wiki",
    "title",
    "user",
    "event_type",
    "is_bot",
    "length_new",
    "event_timestamp",
    "inserted_at",
]


def _client():
    return clickhouse_connect.get_client(
        host=HOST, port=PORT, username="wikistream", password=PASSWORD
    )


def _window():
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return end - timedelta(hours=1), end


def _suite_command() -> list[str]:
    """Command to run gx.suite with the gx project's interpreter.

    Under ``uv run --project gx pytest`` ``sys.executable`` already is the gx
    venv python; when the file is collected by another project's pytest (e.g.
    the consumer ch run) resolve the gx venv via uv instead.
    """
    try:
        import great_expectations  # noqa: F401
    except ImportError:
        return ["uv", "run", "--project", "gx", "python", "-m", "gx.suite"]
    return [sys.executable, "-m", "gx.suite"]


def _run_suite(table: str) -> tuple[int, dict]:
    env = {
        **os.environ,
        "CLICKHOUSE_HOST": HOST,
        "CLICKHOUSE_PORT": str(PORT),
        "CLICKHOUSE_USER": "wikistream",
        "CLICKHOUSE_PASSWORD": PASSWORD,
        "GX_TABLE": table,
        "GX_WINDOW_HOURS": "1",
        "GX_ROW_MIN": "1",
        "GX_ROW_MAX": "500000",
    }
    proc = subprocess.run(
        _suite_command(),
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    lines = proc.stdout.strip().splitlines()
    if not lines:
        raise AssertionError(
            f"suite produced no stdout; rc={proc.returncode} stderr={proc.stderr[-2000:]}"
        )
    return proc.returncode, json.loads(lines[-1])


# Unique per-process so parallel pytest runs (consumer ch + gx ch) don't race
# on the same fixture tables.
_VALID_TABLE = f"gx_test_valid_{os.getpid()}"
_BAD_TABLE = f"gx_test_bad_{os.getpid()}"


def test_valid_fixture_passes():
    table = _VALID_TABLE
    client = _client()
    try:
        client.command(CREATE_SQL.format(table=table))
        client.command(f"TRUNCATE TABLE {table}")
        start, _ = _window()
        inserted_at = start + timedelta(minutes=30)
        event_ts = inserted_at - timedelta(seconds=60)
        rows = []
        for i in range(200):
            rows.append(
                [
                    f'{{"wiki": "wiki{i}"}}',
                    f"wiki{i}",
                    f"title {i}",
                    f"user{i}",
                    ("edit", "new", "log")[i % 3],
                    int(i % 7 == 0),
                    i % 2,
                    event_ts,
                    inserted_at,
                ]
            )
        client.insert(table, rows, column_names=COLUMNS)
        rc, report = _run_suite(table)
        assert rc == 0, report
        assert report["success"] is True
        assert report["row_count"] == 200
    finally:
        client.command(f"DROP TABLE IF EXISTS {table}")
        client.close()


def test_bad_fixture_fails():
    table = _BAD_TABLE
    client = _client()
    try:
        client.command(CREATE_SQL.format(table=table))
        client.command(f"TRUNCATE TABLE {table}")
        start, _ = _window()
        inserted_at = start + timedelta(minutes=30)
        rows = []
        for i in range(200):
            # Engineered failures: bad event_type, 5 wikis only, bot ratio 0.9,
            # and a +10min future skew on half the rows.
            rows.append(
                [
                    f'{{"wiki": "wiki{i % 5}"}}',
                    f"wiki{i % 5}",
                    f"title {i}",
                    f"user{i}",
                    ("edit", "new", "garbage")[i % 3],
                    1 if i >= 20 else 0,
                    i % 2,
                    inserted_at + timedelta(minutes=10)
                    if i % 2 == 0
                    else inserted_at - timedelta(seconds=60),
                    inserted_at,
                ]
            )
        client.insert(table, rows, column_names=COLUMNS)
        rc, report = _run_suite(table)
        assert rc != 0, report
        assert report["success"] is False
        assert report["expectations_failed"] >= 1
    finally:
        client.command(f"DROP TABLE IF EXISTS {table}")
        client.close()
