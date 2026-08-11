"""Async consumer for the Wikimedia recentchange SSE stream (plan §6.1).

Connects to the EventStreams SSE endpoint, parses frames with src.sse.SSEParser,
and inserts each event's raw JSON into default.raw_events via the
clickhouse-connect async client (async_insert, at-most-once). Reconnects
forever with a bounded wait. SIGINT/SIGTERM exit cleanly.
"""

import asyncio
import logging
import os
import signal
import time
from datetime import datetime, timezone

import httpx2
from clickhouse_connect import get_async_client

from src.sse import SSEEvent, SSEParser

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

logger = logging.getLogger("wikistream.consumer")


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
    except asyncio.TimeoutError:
        return stop.is_set()
    return True


async def _insert_event(
    client, ev: SSEEvent, total: int, batch: int, last_log: float
) -> tuple[int, int, float]:
    """Insert one row; on failure warn and drop (at-most-once). Returns counters."""
    try:
        await client.insert(
            "default.raw_events",
            [[datetime.now(timezone.utc), ev.data]],
            column_names=["inserted_at", "event"],
            settings={"async_insert": 1, "wait_for_async_insert": 0},
        )
    except Exception as exc:
        logger.warning("insert_failed event=%s reason=%s", ev.id, exc)
        return total, batch, last_log
    total += 1
    batch += 1
    now = time.monotonic()
    if batch >= 100 or now - last_log >= 60.0:
        logger.info("inserted events=%d total=%d", batch, total)
        batch = 0
        last_log = now
    return total, batch, last_log


async def consume_forever(client, stop: asyncio.Event) -> None:
    """Stream + parse + insert loop; reconnects forever on any stream failure."""
    last_event_id: str | None = None  # in-memory only, per plan
    retry_ms: int | None = None
    total = 0

    async with httpx2.AsyncClient(timeout=httpx2.Timeout(None, connect=10.0)) as http:
        while not stop.is_set():
            headers = {
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
                "User-Agent": USER_AGENT,
            }
            if last_event_id is not None:
                headers["Last-Event-ID"] = last_event_id
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
                            last_event_id,
                        )
                        parser = SSEParser()
                        batch = 0
                        last_log = time.monotonic()
                        async for chunk in response.aiter_bytes():
                            for ev in parser.feed(chunk):
                                if ev.retry is not None:
                                    retry_ms = ev.retry
                                if ev.id is not None:
                                    last_event_id = ev.id
                                total, batch, last_log = await _insert_event(
                                    client, ev, total, batch, last_log
                                )
                        reason = "stream ended"
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "reconnect reason=%s last_event_id=%s",
                reason,
                last_event_id,
            )
            if await _sleep_or_stop(stop, _wait_seconds(retry_ms, retry_after)):
                break


async def main() -> None:
    logging.basicConfig(format="%(levelname)-5s %(message)s", level=logging.INFO)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

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
        task = asyncio.create_task(consume_forever(client, stop))
        await stop.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
