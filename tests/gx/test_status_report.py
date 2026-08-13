"""Unit + CH-marked tests for gx.suite.report_status (pipeline_health write).

Row-builder and failure-swallowing behavior is pure and runs everywhere; the
live insert and the fake-client insert-failure test are ch-marked so the
``-m ch`` verification run exercises them against the running stack.
"""

import json
import sys
import time
from datetime import datetime, timezone
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

from gx.suite import report_status

HOST = "localhost"
PORT = 8123
PASSWORD = "wikistream_dev_password"

# Mirrors migrations/008_pipeline_health.sql so the live test is self-sufficient
# regardless of whether the migrations ch suite ran first in this pytest run.
PIPELINE_HEALTH_SQL = """CREATE TABLE IF NOT EXISTS default.pipeline_health
(
    source LowCardinality(String),
    metric LowCardinality(String),
    ts     DateTime64(3, 'UTC'),
    value  Float64,
    detail String
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(ts)
ORDER BY (source, ts)
TTL ts + INTERVAL 7 DAY"""

VERDICT = {
    "success": True,
    "window_start": "2026-08-01T00:00:00+00:00",
    "window_end": "2026-08-01T01:00:00+00:00",
    "run_id": "unit-test",
    "expectations_passed": 3,
    "expectations_failed": 0,
    "row_count": 42,
}


class _FakeClient:
    def __init__(self):
        self.rows = []

    def insert(self, table, data, column_names=None, settings=None):
        self.rows.extend(data)


class _RaisingClient:
    def insert(self, *args, **kwargs):
        raise RuntimeError("boom")


def test_row_shape_and_source_metric():
    row = report_status(VERDICT, _FakeClient())
    assert isinstance(row, tuple) and len(row) == 5
    assert isinstance(row[0], datetime) and row[0].tzinfo is timezone.utc
    assert row[1] == "gx"
    assert row[2] == "result"


def test_value_is_1_0_when_success_true():
    row = report_status({**VERDICT, "success": True}, _FakeClient())
    assert row[3] == 1.0


def test_value_is_0_0_when_success_false():
    row = report_status({**VERDICT, "success": False}, _FakeClient())
    assert row[3] == 0.0


def test_detail_has_six_keys_and_parses():
    row = report_status(VERDICT, _FakeClient())
    detail = json.loads(row[4])
    assert set(detail) == {
        "window_start",
        "window_end",
        "run_id",
        "expectations_passed",
        "expectations_failed",
        "row_count",
    }
    assert detail == {
        "window_start": "2026-08-01T00:00:00+00:00",
        "window_end": "2026-08-01T01:00:00+00:00",
        "run_id": "unit-test",
        "expectations_passed": 3,
        "expectations_failed": 0,
        "row_count": 42,
    }


def test_detail_single_quote_escaped():
    row = report_status(
        {**VERDICT, "window_start": "2026-08-01T00:00:0'0+00:00"}, _FakeClient()
    )
    # No raw quote survives: every ' is backslash-escaped (SQL string literal).
    assert "'" not in row[4].replace("\\'", "")


def test_missing_verdict_keys_default_to_zero():
    row = report_status({"success": True}, _FakeClient())
    detail = json.loads(row[4])
    assert detail["expectations_passed"] == 0
    assert detail["expectations_failed"] == 0
    assert detail["row_count"] == 0


def test_client_none_returns_none_without_raising():
    assert report_status(VERDICT, None) is None


@pytest.mark.ch
def test_insert_failure_is_swallowed():
    assert report_status(VERDICT, _RaisingClient()) is not None


@pytest.mark.ch
def test_live_insert_to_pipeline_health():
    client = clickhouse_connect.get_client(
        host=HOST, port=PORT, username="wikistream", password=PASSWORD
    )
    try:
        client.command(PIPELINE_HEALTH_SQL)
        row = report_status(
            {
                "success": True,
                "window_start": "2026-08-01T00:00:00+00:00",
                "window_end": "2026-08-01T01:00:00+00:00",
                "run_id": "live-insert-test",
                "expectations_passed": 3,
                "expectations_failed": 0,
                "row_count": 42,
            },
            client,
        )
        assert row is not None
        # async_insert: 1 buffers on the server and flushes shortly after —
        # poll briefly instead of asserting on a racy first read.
        deadline = time.monotonic() + 10
        while True:
            n = client.query(
                "SELECT count() FROM default.pipeline_health "
                "WHERE source='gx' AND metric='result' "
                "AND ts > now() - INTERVAL 1 MINUTE"
            ).first_row[0]
            if n >= 1:
                break
            assert time.monotonic() < deadline, "pipeline_health row not flushed"
            time.sleep(0.5)
    finally:
        client.close()
