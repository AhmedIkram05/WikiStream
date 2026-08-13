"""Heartbeat telemetry tests (plan §5A).

Unit tests cover build_row (deltas, first-tick zeros, previous-key defaults,
resumed_from passthrough, 9-key JSON detail, single-quote-safe output) and
heartbeat_loop's swallow-not-crash contract on insert failure. The ch-marked
test drives a real client so one row per tick lands in default.pipeline_health
(created by migration 008 — if it is missing, the SELECT below fails loudly).
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone

import pytest
from clickhouse_connect import get_async_client

from src.heartbeat import build_row, heartbeat_loop


def ch_env():
    return {
        "CH_HOST": os.environ.get("CH_HOST", "localhost"),
        "CH_PORT": os.environ.get("CH_PORT", "8123"),
        "CH_USER": os.environ.get("CH_USER", "wikistream"),
        "CH_PASSWORD": os.environ.get("CH_PASSWORD", "wikistream_dev_password"),
    }


def counters(
    total=0,
    dead_lettered=0,
    insert_failed=0,
    duplicates_skipped=0,
    resumed_from="none",
):
    return {
        "total": total,
        "dead_lettered": dead_lettered,
        "insert_failed": insert_failed,
        "duplicates_skipped": duplicates_skipped,
        "resumed_from": resumed_from,
    }


def detail_of(row):
    assert row[1:4] == ("consumer", "heartbeat", 1.0)
    return json.loads(row[4])


def test_build_row_first_tick_all_deltas_zero():
    row = build_row(
        counters(total=150, dead_lettered=2, insert_failed=1, duplicates_skipped=3),
        None,
        datetime.now(timezone.utc),
    )
    assert detail_of(row) == {
        "inserted_delta": 0,
        "dead_lettered_delta": 0,
        "insert_failed_delta": 0,
        "duplicates_skipped_delta": 0,
        "total": 150,
        "dead_lettered": 2,
        "insert_failed": 1,
        "duplicates_skipped": 3,
        "resumed_from": "none",
    }


def test_build_row_deltas_between_ticks():
    prev = counters(total=100, dead_lettered=2, insert_failed=1, duplicates_skipped=5)
    cur = counters(total=140, dead_lettered=4, insert_failed=3, duplicates_skipped=9)
    detail = detail_of(build_row(cur, prev, datetime.now(timezone.utc)))
    assert detail["inserted_delta"] == 40
    assert detail["dead_lettered_delta"] == 2
    assert detail["insert_failed_delta"] == 2
    assert detail["duplicates_skipped_delta"] == 4
    assert detail["total"] == 140
    assert detail["dead_lettered"] == 4
    assert detail["insert_failed"] == 3
    assert detail["duplicates_skipped"] == 9


def test_build_row_previous_missing_keys_are_zero():
    # previous.get(key, 0): an empty previous dict means the first delta
    # equals the full counter value.
    detail = detail_of(
        build_row(counters(total=50, dead_lettered=2), {}, datetime.now(timezone.utc))
    )
    assert detail["inserted_delta"] == 50
    assert detail["dead_lettered_delta"] == 2
    assert detail["insert_failed_delta"] == 0
    assert detail["duplicates_skipped_delta"] == 0


def test_build_row_detail_json_nine_keys():
    detail = detail_of(build_row(counters(), None, datetime.now(timezone.utc)))
    assert set(detail) == {
        "inserted_delta",
        "dead_lettered_delta",
        "insert_failed_delta",
        "duplicates_skipped_delta",
        "total",
        "dead_lettered",
        "insert_failed",
        "duplicates_skipped",
        "resumed_from",
    }


def test_build_row_resumed_from_passthrough():
    detail = detail_of(
        build_row(
            counters(resumed_from="2026-08-13T10:00:00Z"),
            None,
            datetime.now(timezone.utc),
        )
    )
    assert detail["resumed_from"] == "2026-08-13T10:00:00Z"


def test_build_row_detail_single_quote_safe():
    # json.dumps uses double quotes: the detail never embeds a raw single
    # quote that would break the parity-style INSERT in the migration suite.
    row = build_row(
        counters(total=1, resumed_from="2026-08-13T10:00:00Z"),
        None,
        datetime.now(timezone.utc),
    )
    assert "'" not in row[4]


class RecordingClient:
    def __init__(self):
        self.insert_calls = 0

    async def insert(self, table, data, **kwargs):
        self.insert_calls += 1


class RaisingClient:
    def __init__(self):
        self.insert_calls = 0

    async def insert(self, table, data, **kwargs):
        self.insert_calls += 1
        raise RuntimeError("clickhouse down")


async def _set_after(stop: asyncio.Event, delay: float) -> None:
    await asyncio.sleep(delay)
    stop.set()


def test_heartbeat_loop_exits_on_stop():
    # stop set before the loop starts: no tick sleeps, only the final flush.
    fake = RecordingClient()
    stop = asyncio.Event()
    stop.set()
    asyncio.run(
        asyncio.wait_for(heartbeat_loop(fake, counters(), stop, interval=0.01), 2.0)
    )
    assert fake.insert_calls == 1


def test_heartbeat_loop_swallows_insert_errors(caplog):
    fake = RaisingClient()
    stop = asyncio.Event()

    async def scenario():
        stopper = asyncio.create_task(_set_after(stop, 0.05))
        await heartbeat_loop(fake, counters(), stop, interval=0.01)
        await stopper

    asyncio.run(scenario())
    # Several in-loop ticks plus the final flush each hit the failing insert
    # and are swallowed: the loop kept going and still flushed on exit.
    assert fake.insert_calls >= 2
    assert "heartbeat_insert_failed" in caplog.text


@pytest.mark.ch
def test_heartbeat_loop_live_insert():
    env = ch_env()

    async def scenario():
        client = await get_async_client(
            host=env["CH_HOST"],
            port=int(env["CH_PORT"]),
            username=env["CH_USER"],
            password=env["CH_PASSWORD"],
        )
        try:
            # Self-contained: the migration suite drops this table in its
            # trailing reset(), so recreate it if missing (idempotent).
            await client.command(
                "CREATE TABLE IF NOT EXISTS default.pipeline_health ("
                " source LowCardinality(String),"
                " metric LowCardinality(String),"
                " ts DateTime64(3, 'UTC'),"
                " value Float64,"
                " detail String)"
                " ENGINE = MergeTree"
                " PARTITION BY toYYYYMMDD(ts)"
                " ORDER BY (source, ts)"
                " TTL ts + INTERVAL 7 DAY"
            )
            stop = asyncio.Event()
            stopper = asyncio.create_task(_set_after(stop, 1.0))
            await heartbeat_loop(client, counters(), stop, interval=0.1)
            await stopper
            # async_insert is fire-and-forget (wait_for_async_insert=0): poll
            # for visibility instead of asserting immediately. pipeline_health
            # comes from migration 008 — if it is missing, this SELECT raises.
            deadline = time.monotonic() + 10.0
            n = 0
            while n == 0 and time.monotonic() < deadline:
                n = (
                    await client.query(
                        "SELECT count() FROM default.pipeline_health"
                        " WHERE source = 'consumer' AND metric = 'heartbeat'"
                        " AND ts > now() - INTERVAL 1 MINUTE"
                    )
                ).result_rows[0][0]
                await asyncio.sleep(0.5)
            assert n >= 1
        finally:
            await client.close()

    asyncio.run(scenario())
