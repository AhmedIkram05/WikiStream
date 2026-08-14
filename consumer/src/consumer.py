"""Async consumer for the Wikimedia recentchange SSE stream (plan §6.1, §4A).

Connects to the EventStreams SSE endpoint, parses frames with src.sse.SSEParser,
validates each event (src.models), and batches inserts into
default.raw_events via the clickhouse-connect async client (async_insert,
at-most-once). Validation failures go to the dead-letter table. Durability is
tracked in /state/consumer_state.json (Last-Event-ID + totals) so restarts
resume without losing the cursor. SIGINT/SIGTERM flush + save before exit.
"""

import asyncio
import json
import logging
import os
import signal
import time
from contextlib import suppress
from datetime import UTC, datetime

import httpx2
from clickhouse_connect import get_async_client
from pydantic import ValidationError

from src.batcher import EventBatcher
from src.dead_letter import write_dead_letter
from src.heartbeat import heartbeat_loop
from src.models import WikiEvent, validate_timestamp
from src.sse import SSEParser

STREAM_URL = os.environ.get(
    "STREAM_URL",
    "https://stream.wikimedia.org/v2/stream/recentchange",
)
CLICKHOUSE_HOST = os.environ.get("CLICKHOUSE_HOST") or "localhost"
CLICKHOUSE_PORT = int(os.environ.get("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD")
USER_AGENT = os.environ.get(
    "USER_AGENT",
    "WikiStream/0.1 (personal data-engineering demo; "
    "https://github.com/AhmedIkram05/WikiStream)",
)
STATE_DIR = os.environ.get("STATE_DIR", "/state")
STATE_FILE = os.path.join(STATE_DIR, "consumer_state.json")

logger = logging.getLogger("wikistream.consumer")

# Idle-stats line cadence (seconds of stream silence before logging n=0).
# Module constant so tests can zero it instead of faking the clock.
IDLE_STATS_INTERVAL = 60.0


def _wait_seconds(retry_ms: int | None, retry_after: int | None) -> float:
    """Reconnect wait: SSE retry hint is the floor (1s default), Retry-After
    honored when present, everything capped at ~30s (plan intent: no
    1s hammering on 429/503, never wait longer than 30s)."""
    return min(
        max(retry_ms / 1000 if retry_ms else 1.0, retry_after if retry_after else 0.0),
        30.0,
    )


def _parse_retry_after(response) -> int | None:
    """Seconds-form Retry-After only; HTTP-date form falls back to the cap."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        return None


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> bool:
    """Wait up to `seconds`; True when a shutdown was requested during the wait."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        return stop.is_set()
    return True


def _cursor_ts(s: str) -> int:
    """Highest partition position in a Kafka composite cursor id.

    The id is a JSON array of per-partition cursors like
    `[{"topic":"eqiad...","timestamp":1786...},{"topic":"codfw...","offset":-1}]`.
    The ARRAY ORDER varies between events (eqiad-first or codfw-first), so a
    raw string compare is meaningless — compare the max numeric position.
    """
    try:
        cursors = json.loads(s)
    except (TypeError, ValueError):
        return 0
    best = 0
    if isinstance(cursors, list):
        for c in cursors:
            if isinstance(c, dict):
                for key in ("timestamp", "offset"):
                    v = c.get(key)
                    if isinstance(v, int) and v > best:
                        best = v
    return best


def _max_id(a: str | None, b: str | None) -> str | None:
    """Larger of two event ids: None-handling; numeric compare for digit-strings."""
    if a is None:
        return b
    if b is None:
        return a
    if a.isdigit() and b.isdigit():
        return a if int(a) >= int(b) else b
    if a.startswith("[") or b.startswith("["):
        return a if _cursor_ts(a) >= _cursor_ts(b) else b
    return a if a >= b else b


def load_state() -> dict | None:
    """Read durable state; None when absent, WARNING + None on any read error."""
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            state = json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("state_load_failed reason=%s", exc)
        return None
    if not isinstance(state, dict):
        return None
    if not isinstance(state.get("last_event_id"), (str, type(None))):
        state["last_event_id"] = None
    if not isinstance(state.get("total"), int):
        state["total"] = 0
    return state


def save_state(last_event_id: str | None, total: int) -> None:
    """Atomically persist state (tmp + os.replace); never crash."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        payload = {
            "last_event_id": last_event_id,
            "total": total,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        logger.warning("state_save_failed reason=%s", exc)


async def consume_forever(
    client,
    stop: asyncio.Event,
    resumed_from: str | None,
    counters: dict,
) -> None:
    """Stream + parse + validate + batch loop; reconnects forever on failure.

    counters carries {total, dead_lettered, insert_failed, duplicates_skipped}
    so the orchestrator (and tests) can observe progress. durable_id advances
    ONLY on durability: dead-letter writes (steps 1-3) or successful flushes
    (step 6) — never for an id still sitting in an unflushed batch.
    """
    batcher = EventBatcher()
    durable_id = resumed_from
    last_saved_id: str | None = None
    last_saved_mono = time.monotonic()
    last_stats_log = time.monotonic()
    retry_ms: int | None = None

    async with httpx2.AsyncClient(timeout=httpx2.Timeout(None, connect=10.0)) as http:
        while not stop.is_set():
            headers = {
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
                "User-Agent": USER_AGENT,
            }
            if durable_id is not None:
                headers["Last-Event-ID"] = durable_id
            retry_after: int | None = None
            try:
                async with http.stream("GET", STREAM_URL, headers=headers) as response:
                    if response.status_code != 200:
                        retry_after = _parse_retry_after(response)
                        reason = f"http {response.status_code}"
                    else:
                        logger.info(
                            "connected url=%s last_event_id=%s",
                            STREAM_URL,
                            durable_id,
                        )
                        parser = SSEParser()
                        async for chunk in response.aiter_bytes():
                            if stop.is_set():
                                break  # flush-on-exit: stop observed at every
                                # chunk boundary (events flow ~every 25ms on
                                # the real stream), so SIGTERM exits promptly
                            for ev in parser.feed(chunk):
                                if ev.retry is not None:
                                    retry_ms = ev.retry
                                ev_id = ev.id
                                obj: object | None = None
                                # Step 1: JSON validity. DL semantics are
                                # at-least-once: the dead_lettered counter and
                                # the durable cursor advance ONLY when the DL
                                # row landed (write_dead_letter returns bool);
                                # a failed write leaves the cursor behind so
                                # the replay re-runs this event (the crash
                                # window between a landed DL row and the cursor
                                # advance is microseconds — acceptable).
                                try:
                                    obj = json.loads(ev.data)
                                except json.JSONDecodeError:
                                    if await write_dead_letter(
                                        client,
                                        reason="validation:invalid_json",
                                        wiki="",
                                        title="",
                                        event=ev.data,
                                    ):
                                        counters["dead_lettered"] += 1
                                        if ev_id:
                                            durable_id = _max_id(durable_id, ev_id)
                                    continue
                                # Step 2: timestamp validity.
                                ts = (
                                    obj.get("timestamp")
                                    if isinstance(obj, dict)
                                    else None
                                )
                                ts_reason = validate_timestamp(ts)
                                if ts_reason is not None:
                                    wiki, title = _dl_fields(obj)
                                    if await write_dead_letter(
                                        client,
                                        reason=ts_reason,
                                        wiki=wiki,
                                        title=title,
                                        event=ev.data,
                                    ):
                                        counters["dead_lettered"] += 1
                                        if ev_id:
                                            durable_id = _max_id(durable_id, ev_id)
                                    continue
                                # Step 3: schema validity.
                                try:
                                    WikiEvent.model_validate(obj)
                                except ValidationError as ve:
                                    errors = ve.errors()
                                    reason = (
                                        f"validation:{errors[0]['type']}"
                                        if errors
                                        else "validation:error"
                                    )
                                    wiki, title = _dl_fields(obj)
                                    if await write_dead_letter(
                                        client,
                                        reason=reason,
                                        wiki=wiki,
                                        title=title,
                                        event=ev.data,
                                    ):
                                        counters["dead_lettered"] += 1
                                        if ev_id:
                                            durable_id = _max_id(durable_id, ev_id)
                                    continue
                                # Step 4: dedup.
                                if ev_id and batcher.seen(ev_id):
                                    counters["duplicates_skipped"] += 1
                                    continue
                                # Step 5: add to batch.
                                flush_due = batcher.add(
                                    (datetime.now(UTC), ev.data), ev_id
                                )
                                # Step 6: flush when due.
                                if flush_due:
                                    mid, flushed = await batcher.flush(client)
                                    if mid is not None:
                                        counters["total"] += flushed
                                        durable_id = _max_id(durable_id, mid)
                                    else:
                                        counters["insert_failed"] += flushed
                                        logger.warning(
                                            "insert_failed events=%d reason=%s",
                                            flushed,
                                            "batch dropped",
                                        )
                                    # Step 7: stats — every flush logs; a
                                    # heartbeat logs n=0 after 60s of silence.
                                    logger.info(
                                        "inserted events=%d total=%d "
                                        "dead_lettered=%d insert_failed=%d "
                                        "duplicates_skipped=%d resumed_from=%s",
                                        flushed,
                                        counters["total"],
                                        counters["dead_lettered"],
                                        counters["insert_failed"],
                                        counters["duplicates_skipped"],
                                        resumed_from or "none",
                                    )
                                    last_stats_log = time.monotonic()
                                now_mono = time.monotonic()
                                if now_mono - last_stats_log >= IDLE_STATS_INTERVAL:
                                    last_stats_log = now_mono
                                    logger.info(
                                        "inserted events=0 total=%d "
                                        "dead_lettered=%d insert_failed=%d "
                                        "duplicates_skipped=%d resumed_from=%s",
                                        counters["total"],
                                        counters["dead_lettered"],
                                        counters["insert_failed"],
                                        counters["duplicates_skipped"],
                                        resumed_from or "none",
                                    )
                                # Step 8: debounced durable save.
                                now_mono = time.monotonic()
                                if (
                                    durable_id != last_saved_id
                                    or now_mono - last_saved_mono >= 2.0
                                ):
                                    save_state(durable_id, counters["total"])
                                    last_saved_id = durable_id
                                    last_saved_mono = now_mono
                        reason = "stream ended"
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "reconnect reason=%s last_event_id=%s",
                reason,
                durable_id,
            )
            if await _sleep_or_stop(stop, _wait_seconds(retry_ms, retry_after)):
                break

    # Connection loop exited (stop set): FINAL FLUSH — zero loss on shutdown.
    if batcher.pending_count:
        mid, flushed = await batcher.flush(client)
        if mid is not None:
            counters["total"] += flushed
            durable_id = _max_id(durable_id, mid)
            logger.info(
                "inserted events=%d total=%d dead_lettered=%d insert_failed=%d "
                "duplicates_skipped=%d resumed_from=%s",
                flushed,
                counters["total"],
                counters["dead_lettered"],
                counters["insert_failed"],
                counters["duplicates_skipped"],
                resumed_from or "none",
            )
        else:
            counters["insert_failed"] += flushed
            logger.warning(
                "insert_failed events=%d reason=%s", flushed, "batch dropped"
            )
    save_state(durable_id, counters["total"])


def _dl_fields(obj: object) -> tuple[str, str]:
    """Dead-letter wiki/title from a raw JSON object (dict-shaped or not)."""
    if isinstance(obj, dict):
        return str(obj.get("wiki") or ""), str(obj.get("title") or "")
    return "", ""


async def main() -> None:
    logging.basicConfig(format="%(levelname)-5s %(message)s", level=logging.INFO)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    state = load_state()
    resumed_from = state.get("last_event_id") if state else None
    if resumed_from is not None:
        logger.info("resumed_from=%s", resumed_from)
    counters: dict = {
        "total": int(state.get("total", 0)) if state else 0,
        "dead_lettered": 0,
        "insert_failed": 0,
        "duplicates_skipped": 0,
    }
    counters["resumed_from"] = resumed_from or "none"

    # get_async_client eagerly probes CH (SELECT version()) — during cold
    # start that raises OperationalError; retry so cold-start surfaces as a
    # WARNING line, never a crash-loop (restore of the 1.6 fix).
    while True:
        try:
            client = await get_async_client(
                host=CLICKHOUSE_HOST,
                port=CLICKHOUSE_PORT,
                username=CLICKHOUSE_USER,
                password=CLICKHOUSE_PASSWORD,
            )
            break
        except Exception as exc:
            logger.warning("clickhouse_unavailable reason=%s", exc)
            if await _sleep_or_stop(stop, 2.0):
                return
    async with client:
        task = asyncio.create_task(
            consume_forever(client, stop, resumed_from, counters)
        )
        heartbeat_task = asyncio.create_task(heartbeat_loop(client, counters, stop))
        await stop.wait()
        # Graceful: up to 10s for the final flush + state save (SIGTERM path
        # must flush), then force-cancel if the stream read blocks.
        try:
            await asyncio.wait_for(asyncio.gather(task, heartbeat_task), 10.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            for t in (task, heartbeat_task):
                t.cancel()
                with suppress(asyncio.CancelledError):
                    await t


if __name__ == "__main__":
    asyncio.run(main())
