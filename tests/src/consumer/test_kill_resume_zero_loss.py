"""AC4: kill/resume — zero loss AND zero duplication (plan §6.1).

Drives the REAL consumer (src.consumer.consume_forever) over the local SSE
fixture with 200 synthetic events (ids "1".."200"): a mid-stream server drop
(reconnect + Last-Event-ID replay, deduped by the in-memory ring), a hard
task.cancel() simulating SIGKILL (no graceful flush — the pending batch is
lost by design, at-most-once), and a FRESH consume_forever resuming from the
saved state (process restart, fresh dedup ring). Final state must be exactly
200 rows for 200 emitted events: nothing lost, nothing duplicated
(count == 200 == total, where total counts inserts only).

Deviations from the planned flow, forced by real behavior:

1. LEG1 ends at the FIRST poll observing (raw >= 50 AND duplicates_skipped
   > 0): the duplicate counter is the only observable proof that the
   post-drop replay actually happened. Killing at raw >= 50 alone would
   cancel the consumer BEFORE the drop (flush 1 lands ~5s in, the drop is
   at ~10s) and duplicates_skipped would be 0.
2. The batcher's age flush fires only on event arrival, so the LAST batch
   (~47 events, 4.7s of streaming < 5s age cap) never flushes mid-stream;
   the consumer drains it only on its FINAL flush, which fires on stop.
   So in LEG2b the raw count plateaus at the last flushed boundary (~153)
   and cannot reach 200 while streaming: the test waits for that plateau
   (count >= 100 unchanged for 8s — longer than the 5.1s max inter-flush
   gap and the 4.7s tail delivery), then sets stop2 so the graceful exit
   drains the tail, and only then asserts raw == 200.
3. Every count is filtered by title LIKE 'Zkill%': a live consumer
   container may feed the same tables from the real stream (~40 rows/s).
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

POLL_S = 0.5
LEG1_DEADLINE_S = 40.0
LEG2B_DEADLINE_S = 40.0
FINAL_DEADLINE_S = 15.0
STATE_DEADLINE_S = 5.0
STABLE_POLLS = 16  # 8s unchanged: replay exhausted, tail batch is pending
TASK2_STOP_S = 15.0
SCENARIO_TIMEOUT_S = 150.0


def ch_env():
    return {
        "CH_HOST": os.environ.get("CH_HOST", "localhost"),
        "CH_PORT": os.environ.get("CH_PORT", "8123"),
        "CH_USER": os.environ.get("CH_USER", "wikistream"),
        "CH_PASSWORD": os.environ.get("CH_PASSWORD", "wikistream_dev_password"),
    }


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


def truncate(env: dict) -> None:
    """Empty both tables (safe: the dev DB is disposable)."""
    query("TRUNCATE TABLE default.raw_events", env)
    query("TRUNCATE TABLE default.dead_letter", env)


def zkill_event(i: int) -> str:
    return json.dumps(
        {
            "type": "edit",
            "title": f"Zkill{i}",
            "user": "U",
            "wiki": "enwiki",
            "timestamp": "2020-01-01T00:00:00Z",
            "bot": False,
        }
    )


def zkill_count(env: dict) -> int:
    return int(
        scalar(
            "SELECT count() FROM default.raw_events WHERE title LIKE 'Zkill%'",
            env,
        )
    )


@pytest.mark.ch
def test_kill_resume_zero_loss(monkeypatch, tmp_path):
    env = ch_env()
    r = run_apply(env)
    assert r.returncode == 0, f"apply.sh exited {r.returncode}:\n{r.stdout}\n{r.stderr}"
    truncate(env)

    events: list[tuple[str | None, str]] = [
        (str(i), zkill_event(i)) for i in range(1, 201)
    ]
    fixture = SSEFixture(events, disconnect_after=100, event_interval_s=0.1)

    async def scenario():
        await fixture.start()
        monkeypatch.setattr("src.consumer.STREAM_URL", fixture.url)
        # State writes go to tmp_path — never /state (STATE_DIR too: save_state
        # makedirs STATE_DIR before touching STATE_FILE).
        monkeypatch.setattr("src.consumer.STATE_DIR", str(tmp_path))
        state_file = tmp_path / "consumer_state.json"
        monkeypatch.setattr("src.consumer.STATE_FILE", str(state_file))
        # Test-only: make CH inserts synchronous so "flush returned" means
        # "rows visible". Default SETTINGS are fire-and-forget (async_insert +
        # wait_for_async_insert=0) — fine in prod, a 15s wall-clock race here.
        monkeypatch.setattr(
            "src.batcher.SETTINGS", {"async_insert": 1, "wait_for_async_insert": 1}
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
        stop2 = asyncio.Event()
        task2 = None
        try:
            # LEG1: server drop at event 100 + reconnect; the replay over
            # (durable, 200] hits the dedup ring. Exit the moment a flush
            # (raw >= 50) AND a deduped replay are both observable.
            deadline = time.monotonic() + LEG1_DEADLINE_S
            while not (zkill_count(env) >= 50 and counters["duplicates_skipped"] > 0):
                assert time.monotonic() < deadline, (
                    f"leg1: raw={zkill_count(env)} "
                    f"dupes={counters['duplicates_skipped']} "
                    "never reached flush + deduped replay"
                )
                await asyncio.sleep(POLL_S)
            assert not task.done(), "leg1 task ended — crash or reconnect storm"
            assert counters["duplicates_skipped"] > 0, counters
            assert counters["insert_failed"] == 0, counters

            # LEG2: kill-sim — hard cancel, NO graceful flush; the pending
            # batch is dropped (at-most-once), the state file keeps the last
            # durable id.
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

            state = json.loads(state_file.read_text())
            durable = state["last_event_id"]
            assert durable is None or (durable.isdigit() and int(durable) < 200), (
                durable
            )
            deadline = time.monotonic() + STATE_DEADLINE_S
            while durable is None or not durable.isdigit():
                assert time.monotonic() < deadline, (
                    f"state never saved a flushed id: {durable}"
                )
                await asyncio.sleep(POLL_S)
                state = json.loads(state_file.read_text())
                durable = state["last_event_id"]

            # LEG2b: resume = process restart (fresh consume_forever, fresh
            # ring, same counters). Replay covers (durable, 200]; the tail
            # batch only drains on the graceful final flush, so wait for the
            # count plateau (replay exhausted) and then stop.
            # ponytail: disconnect_after fires per-connection, not once — leave
            # it armed and LEG2b eats extra drops/replays, so the 8s plateau
            # can fire mid-replay (backoff gap) and the tail never arrives.
            fixture.disconnect_after = None
            task2 = asyncio.create_task(
                consume_forever(client, stop2, durable, counters)
            )
            last_raw = -1
            stable = 0
            deadline = time.monotonic() + LEG2B_DEADLINE_S
            while True:
                raw = zkill_count(env)
                if raw == 200:
                    break
                if raw >= 100 and raw == last_raw:
                    stable += 1
                    if stable >= STABLE_POLLS:
                        break
                elif raw != last_raw:
                    stable = 0
                last_raw = raw
                assert time.monotonic() < deadline, (
                    f"leg2b: raw={raw} stable={stable} — no plateau, no 200"
                )
                await asyncio.sleep(POLL_S)

            stop2.set()
            await asyncio.wait_for(task2, TASK2_STOP_S)
            # Deadline is checked BEFORE the query: a slow count (cold CH,
            # reconnect-storm aftermath) must not trip the assert mid-window —
            # the loop waits out the full budget, then one confirming query
            # settles the verdict (correctness is still enforced by the
            # counters/total asserts below).
            deadline = time.monotonic() + FINAL_DEADLINE_S
            while time.monotonic() < deadline:
                if zkill_count(env) == 200:
                    break
                await asyncio.sleep(POLL_S)
            assert zkill_count(env) == 200, "final flush never drained the tail batch"

            # FINAL: zero loss (200 of 200 emitted) AND zero duplication
            # (count == 200 == total; total counts inserts only).
            assert counters["total"] == 200, counters
            assert counters["duplicates_skipped"] > 0, counters
            assert counters["insert_failed"] == 0, counters
        finally:
            stop.set()
            stop2.set()
            for writer in fixture._writers:
                writer.close()
            try:
                await asyncio.wait_for(fixture.stop(), 5.0)
            except asyncio.TimeoutError:
                pass
            for t in (task, task2):
                if t is not None and not t.done():
                    t.cancel()
                    with suppress(asyncio.CancelledError):
                        await asyncio.wait_for(t, 5.0)
            await asyncio.wait_for(client.close(), 5.0)

    try:
        asyncio.run(asyncio.wait_for(scenario(), timeout=SCENARIO_TIMEOUT_S))
    finally:
        truncate(env)
