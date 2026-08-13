"""Consumer -> dead-letter integration test (plan §4A).

Runs against a LIVE ClickHouse at localhost:8123 (same env contract as
tests/migrations/test_migrations.py: CH_HOST/CH_PORT/CH_USER/CH_PASSWORD).
Drives the real consumer (src.consumer.consume_forever) over a local SSE
fixture streaming 5 valid events + 1 malformed one, and asserts the
malformed event lands in default.dead_letter with reason
"validation:invalid_json" while the valid ones reach default.raw_events.

Deviations from the spec'd ordering, forced by real behavior:

1. The batcher's age-based flush is only evaluated when a NEW event
   arrives (batcher.add() in consumer.py), so a quiet stream tail never
   flushes mid-stream. The batch drains via the consumer's FINAL flush at
   stream end, so raw_events/counters are asserted after the graceful
   shutdown (stop + stream end), like test_resume_dedup.py.
2. SSEFixture.stop() deadlocks on a live connection: asyncio's
   Server.wait_closed() (3.12.1+) waits for all connections to drop, but
   the fixture closes its writers only after wait_closed. The test closes
   the fixture's writers first (EOF -> the consumer's graceful stream-end
   path) and only then stops the fixture.
3. A live wikistream-consumer container feeds the same tables from the
   real Wikimedia stream (~40 rows/s), so bare table counts are never
   stable. Every count/row query filters to this test's synthetic events
   (title IN Page1..Page5, event = '{broken json') instead.
"""

import asyncio
import json
import os
import subprocess
import time
from contextlib import suppress
from pathlib import Path

import pytest
from clickhouse_connect import get_async_client

from src.consumer import consume_forever
from sse_fixture import SSEFixture

REPO_ROOT = Path(__file__).parents[3]

DEADLINE_S = 20.0
POLL_S = 0.2


def ch_env():
    return {
        "CH_HOST": os.environ.get("CH_HOST", "localhost"),
        "CH_PORT": os.environ.get("CH_PORT", "8123"),
        "CH_USER": os.environ.get("CH_USER", "wikistream"),
        "CH_PASSWORD": os.environ.get("CH_PASSWORD", "wikistream_dev_password"),
    }


def truncate(env: dict) -> None:
    """Empty both tables (safe: the dev DB is disposable)."""
    query("TRUNCATE TABLE default.raw_events", env)
    query("TRUNCATE TABLE default.dead_letter", env)


def run_apply(env=None):
    """Run the migration runner; returns the CompletedProcess (caller asserts rc)."""
    e = {**os.environ, **ch_env()}
    if env:
        e.update(env)
    return subprocess.run(
        ["bash", str(REPO_ROOT / "migrations" / "apply.sh")],
        env=e,
        capture_output=True,
        text=True,
        check=False,
    )


def query(sql: str, env: dict) -> str:
    """Run SQL via curl against the ClickHouse HTTP API; return stdout on success."""
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


def scalar(sql: str, env: dict) -> str:
    return query(sql, env).strip()


def valid_event(i: int) -> str:
    return json.dumps(
        {
            "type": "edit",
            "title": f"Page{i}",
            "user": f"User{i}",
            "wiki": "enwiki",
            "timestamp": "2026-08-13T00:00:00Z",
            "bot": False,
        }
    )


@pytest.mark.ch
def test_malformed_event_lands_in_dead_letter(monkeypatch, tmp_path):
    env = ch_env()
    r = run_apply(env)
    assert r.returncode == 0, f"apply.sh exited {r.returncode}:\n{r.stdout}\n{r.stderr}"
    truncate(env)

    events: list[tuple[str | None, str]] = [
        (str(i), valid_event(i)) for i in range(1, 6)
    ]
    events.append(("6", "{broken json"))
    fixture = SSEFixture(events, hold_open=True)

    async def end_stream():
        """End the SSE stream so the consumer exits via its graceful path.
        Closing the fixture's writers sends EOF; fixture.stop() alone would
        deadlock on the live connection (asyncio wait_closed, 3.12.1+)."""
        for writer in fixture._writers:
            writer.close()
        await fixture.stop()

    async def scenario():
        await fixture.start()
        monkeypatch.setattr("src.consumer.STREAM_URL", fixture.url)
        # State writes go to tmp_path — never /state (STATE_DIR too: save_state
        # makedirs STATE_DIR before touching STATE_FILE).
        monkeypatch.setattr("src.consumer.STATE_DIR", str(tmp_path))
        monkeypatch.setattr(
            "src.consumer.STATE_FILE", str(tmp_path / "consumer_state.json")
        )
        client = await get_async_client(
            host=env["CH_HOST"],
            port=int(env["CH_PORT"]),
            username=env["CH_USER"],
            password=env["CH_PASSWORD"],
        )
        counters = {
            "total": 0,
            "dead_lettered": 0,
            "insert_failed": 0,
            "duplicates_skipped": 0,
        }
        stop = asyncio.Event()
        task = asyncio.create_task(consume_forever(client, stop, None, counters))
        try:
            deadline = time.monotonic() + DEADLINE_S
            while (
                scalar(
                    "SELECT count() FROM default.dead_letter"
                    " WHERE event = '{broken json'",
                    env,
                )
                != "1"
            ):
                assert time.monotonic() < deadline, "dead_letter row never appeared"
                await asyncio.sleep(POLL_S)
            row = query(
                "SELECT reason, event, wiki, title FROM default.dead_letter"
                " WHERE event = '{broken json' FORMAT TSV",
                env,
            ).rstrip("\n")  # not scalar(): strip() would eat the empty-field tabs
            assert row.split("\t") == [
                "validation:invalid_json",
                "{broken json",
                "",
                "",
            ], row

            assert not task.done(), "consumer task ended — crash or reconnect storm"
            assert fixture.connections <= 2, fixture.connections
            assert counters["dead_lettered"] == 1, counters

            # Graceful shutdown: stop + stream end; the pending 5-row batch is
            # drained by the consumer's final flush before the task completes.
            stop.set()
            await end_stream()
            await asyncio.wait_for(task, 15.0)

            deadline = time.monotonic() + DEADLINE_S
            while (
                scalar(
                    "SELECT count() FROM default.raw_events"
                    " WHERE title IN ('Page1','Page2','Page3','Page4','Page5')",
                    env,
                )
                != "5"
            ):
                assert time.monotonic() < deadline, "valid events never inserted"
                await asyncio.sleep(POLL_S)
            assert counters["total"] == 5, counters
            assert counters["insert_failed"] == 0, counters
            assert counters["duplicates_skipped"] == 0, counters
        finally:
            stop.set()
            for writer in fixture._writers:
                writer.close()
            try:
                await asyncio.wait_for(fixture.stop(), 5.0)
            except asyncio.TimeoutError:
                pass
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5.0)
            await asyncio.wait_for(client.close(), 5.0)

    try:
        asyncio.run(asyncio.wait_for(scenario(), timeout=60.0))
    finally:
        truncate(env)
