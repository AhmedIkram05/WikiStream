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
from src.cursors import _max_id, _min_id
from src.dead_letter import DeadLetterBatcher
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

# DL buffer age threshold: the flush timer's tick. Rows flush within this
# window even when no further events arrive (quiet stream tail). Module
# constant so tests can shrink it instead of faking the clock.
DL_FLUSH_INTERVAL = 5.0

# Ceiling on concurrent flush tasks (main + DL inserts share the client):
# a stalled ClickHouse backpressures the loop at acquire instead of piling
# up tasks.
FLUSH_CONCURRENCY = 2

# Bounded in-place retry for a failed main-batch insert: rows are re-queued by
# EventBatcher.flush and re-attempted after these backoff delays before the
# batch is abandoned (dropped + unmarked, counted as insert_failed). Kept
# short so the shutdown drain stays inside main()'s 10s budget. Empty
# schedule disables retry (tests that exercise the reconnect-replay fallback).
FLUSH_RETRY_SCHEDULE = (0.5, 1.0, 2.0)


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
            # fsync before replace: without it a power loss can leave the
            # renamed file empty/stale, losing the cursor → mass replay.
            fh.flush()
            os.fsync(fh.fileno())
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
    ONLY on durability: dead-letter flushes (steps 1-3) or successful batch
    flushes (step 6) — never for an id still sitting in an unflushed batch.
    """
    batcher = EventBatcher()
    dl_batcher = DeadLetterBatcher()
    durable_id = resumed_from
    # Highest id successfully flushed by the MAIN batcher, plus whether the
    # main batcher has ever buffered a row. A DL flush may never move
    # durable_id past main_high_water once main has been seen (a pending,
    # in-flight or dropped main batch may hold lower ids); DL-only traffic has
    # no such gap. See _do_dl_flush.
    main_high_water: str | None = None
    main_seen = False
    # Mirror guard (main -> DL): while a DL row carrying an id is buffered but
    # not yet landed (or was dropped), a successful MAIN flush must not advance
    # durable_id past it, or a restart's Last-Event-ID replay would skip that DL
    # row forever (a loss, not the DL->main direction's duplicate). dl_hold_id
    # is the durable watermark captured before the first unlanded DL row; a
    # None hold means nothing was durable yet, so no advance is allowed.
    dl_pending = False
    dl_hold_id: str | None = None
    last_saved_id: str | None = None
    last_saved_mono = time.monotonic()
    last_stats_log = time.monotonic()
    retry_ms: int | None = None
    pending_flushes: set[asyncio.Task] = set()
    flush_sem = asyncio.Semaphore(FLUSH_CONCURRENCY)

    async def _flush_main(honor_stop: bool) -> None:
        """Flush the main batch with bounded in-place retry.

        EventBatcher.flush re-queues rows on failure, so we retry after each
        backoff in FLUSH_RETRY_SCHEDULE; only when the schedule is exhausted
        do we abandon the batch (drop + unmark, counted as insert_failed) so a
        reconnect replay can still refill the dedup ring. honor_stop=True for
        background flushes (a shutdown mid-retry leaves the rows for the final
        flush); False for the final flush, which must not bail on the stop flag.
        """
        nonlocal durable_id, last_stats_log, main_high_water
        attempt = 0
        while True:
            mid, flushed = await batcher.flush(client)
            if flushed == 0:
                # Nothing pending: a concurrent flush task already drained the
                # batch. Not a failure — do not retry or count it.
                break
            if mid is not None:
                counters["total"] += flushed
                # Advance only up to an unlanded DL row (mirror of the
                # DL->main clamp in _do_dl_flush): a landed main batch must not
                # skip a DL row that has not landed or was dropped.
                target = _min_id(mid, dl_hold_id) if dl_pending else mid
                if target is not None:
                    durable_id = _max_id(durable_id, target)
                main_high_water = _max_id(main_high_water, mid)
                break
            if attempt >= len(FLUSH_RETRY_SCHEDULE):
                dropped = batcher.abandon()
                counters["insert_failed"] += dropped
                logger.warning(
                    "insert_failed events=%d reason=%s", dropped, "batch dropped"
                )
                break
            delay = FLUSH_RETRY_SCHEDULE[attempt]
            attempt += 1
            logger.warning("insert_retry events=%d attempt=%d", flushed, attempt)
            if honor_stop:
                if await _sleep_or_stop(stop, delay):
                    # Shutdown mid-retry: leave rows queued for the final flush.
                    break
            else:
                await asyncio.sleep(delay)
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

    async def _flush_job() -> None:
        try:
            await _flush_main(honor_stop=True)
        except Exception as exc:
            logger.warning("flush_failed reason=%s", exc)
        finally:
            flush_sem.release()

    async def _do_dl_flush() -> None:
        """Land buffered DL rows; on success count + advance the cursor.

        A DL flush that advances the cursor persists state IMMEDIATELY (no
        debounce): if the durable cursor lagged landed DL rows, a restart
        would replay those events and re-dead-letter them — the landed ring
        only suppresses that within this process, so the replay window must
        stay at the ~1ms state-write, not the 2s debounce.
        """
        nonlocal durable_id, dl_pending, dl_hold_id
        try:
            dropped, count, max_eid = await dl_batcher.flush(client)
        except Exception as exc:
            logger.warning("dl_flush_failed reason=%s", exc)
            return
        if dropped:
            # at-most-once: cursor stays behind, replay re-runs them, and the
            # mirror hold stays frozen so a successful main flush cannot skip
            # these unlanded rows.
            return
        counters["dead_lettered"] += count
        # Rows landed: release the mirror hold once the buffer is empty; during
        # a burst (rows added while the insert awaited) re-freeze at the
        # current watermark so the new unlanded rows stay protected.
        if dl_batcher.pending_count == 0:
            dl_pending = False
            dl_hold_id = None
        if max_eid is None:
            return
        # A DL row must never drag the cursor past the main batcher's last
        # SUCCESSFUL flush: if a main batch (e.g. ids 100-150) is pending or
        # was dropped while a DL row (id 155) lands, moving the cursor to 155
        # makes a reconnect's Last-Event-ID skip 100-150 forever. DL-only
        # traffic (no main rows ever buffered) has no such gap, so max_eid is
        # safe there.
        if not main_seen:
            bound = max_eid
        else:
            bound = main_high_water
        if dl_pending:
            # ... and while any DL row is still unlanded, do not advance past
            # the frozen watermark (mirror of the main->DL guard).
            bound = _min_id(bound, dl_hold_id)
        if bound is None:
            return
        advanced = _max_id(durable_id, bound)
        if advanced != durable_id:
            durable_id = advanced
            save_state(durable_id, counters["total"])

    async def _dl_add(
        *, reason: str, wiki: str, title: str, event: str, event_id: str | None
    ) -> None:
        """Buffer a DL row; freeze the cursor watermark on the first one.

        A replay whose cursor was clamped (main pending/dropped) can re-deliver
        an event whose DL row already landed — the landed ring skips that
        re-insert. While a DL row carrying an id is unlanded, the mirror hold
        (dl_hold_id) stops a successful main flush from advancing past it.
        """
        nonlocal dl_pending, dl_hold_id
        if dl_batcher.landed(event_id):
            return  # already landed in this process — replay duplicate
        if event_id is not None and not dl_pending:
            dl_pending = True
            dl_hold_id = durable_id
        if dl_batcher.add(
            reason=reason, wiki=wiki, title=title, event=event, event_id=event_id
        ):
            await _spawn_dl_flush()

    async def _dl_flush_job() -> None:
        try:
            await _do_dl_flush()
        finally:
            flush_sem.release()

    async def _spawn_dl_flush() -> None:
        await flush_sem.acquire()
        dl_task = asyncio.create_task(_dl_flush_job())
        pending_flushes.add(dl_task)
        dl_task.add_done_callback(pending_flushes.discard)
        # Yield so the spawned flush swaps the buffer before more adds land
        # (same rhythm as the main flush spawn below — without this, the
        # next add re-triggers flush_due and batches skew > max_rows).
        await asyncio.sleep(0)

    async def _dl_timer_job() -> None:
        """Age trigger: flush buffered DL rows even when no event arrives
        (quiet stream tail). Stop-aware wait, mirroring heartbeat_loop."""
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=DL_FLUSH_INTERVAL)
            except TimeoutError:
                pass
            if stop.is_set():
                break
            if dl_batcher.pending_count:
                await _do_dl_flush()

    dl_timer = asyncio.create_task(_dl_timer_job())

    # read=120: a silent upstream stall must time out into a reconnect —
    # with read unbounded the loop blocks in aiter_bytes forever and only
    # checks stop at chunk boundaries (which never arrive without bytes).
    async with httpx2.AsyncClient(
        timeout=httpx2.Timeout(None, connect=10.0, read=120.0)
    ) as http:
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
                                # Step 1: JSON validity. DL rows are buffered
                                # (DeadLetterBatcher) and flushed as one
                                # insert — the counter/cursor advance only
                                # when a flush LANDS, so a dropped batch
                                # leaves the cursor behind and the replay
                                # re-runs these events. _dl_add skips an id
                                # whose DL row already landed (the landed
                                # ring) and freezes the main->DL cursor guard.
                                try:
                                    obj = json.loads(ev.data)
                                except json.JSONDecodeError:
                                    await _dl_add(
                                        reason="validation:invalid_json",
                                        wiki="",
                                        title="",
                                        event=ev.data,
                                        event_id=ev_id,
                                    )
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
                                    await _dl_add(
                                        reason=ts_reason,
                                        wiki=wiki,
                                        title=title,
                                        event=ev.data,
                                        event_id=ev_id,
                                    )
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
                                    await _dl_add(
                                        reason=reason,
                                        wiki=wiki,
                                        title=title,
                                        event=ev.data,
                                        event_id=ev_id,
                                    )
                                    continue
                                # Step 4: dedup.
                                if ev_id and batcher.seen(ev_id):
                                    counters["duplicates_skipped"] += 1
                                    continue
                                # Step 5: add to batch.
                                main_seen = True
                                flush_due = batcher.add(
                                    (datetime.now(UTC), ev.data), ev_id
                                )
                                # Step 6: flush when due.
                                if flush_due:
                                    # Fire-and-forget flush so SSE never
                                    # awaits CH; the semaphore caps concurrent
                                    # flush tasks (CH stalls backpressure the
                                    # loop at acquire instead of piling up
                                    # tasks).
                                    await flush_sem.acquire()
                                    flush_task = asyncio.create_task(_flush_job())
                                    pending_flushes.add(flush_task)
                                    flush_task.add_done_callback(
                                        pending_flushes.discard
                                    )
                                    await asyncio.sleep(0)
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
                                # Step 8: debounced durable save (kept sync:
                                # ~1ms atomic local write — no race, unlike
                                # threaded saves).
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
            if pending_flushes:
                await asyncio.gather(*pending_flushes, return_exceptions=True)
            # Drain the DL buffer BEFORE the next connect: buffered rows land
            # and advance the cursor here, so the reconnect's Last-Event-ID
            # never replays events whose DL rows already landed (they would
            # be re-dead-lettered — the DL table has no dedup).
            if dl_batcher.pending_count:
                await _do_dl_flush()
            logger.warning(
                "reconnect reason=%s last_event_id=%s",
                reason,
                durable_id,
            )
            if await _sleep_or_stop(stop, _wait_seconds(retry_ms, retry_after)):
                break

    # Connection loop exited (stop set): FINAL FLUSH — zero loss on shutdown.
    # honor_stop=False: the stop flag is already set, so the retry backoff must
    # not bail out of the drain. Wrapped like _flush_job so a CH outage here
    # still reaches save_state and never leaks the dl_timer task.
    if pending_flushes:
        await asyncio.gather(*pending_flushes, return_exceptions=True)
    if batcher.pending_count:
        try:
            await _flush_main(honor_stop=False)
        except Exception as exc:
            logger.warning("flush_failed reason=%s", exc)
    # Drain the dead-letter buffer BEFORE save_state: save_state advances the
    # cursor, so an unflushed DL batch would be stranded (a restart's replay is
    # skipped). Bounded: a DL insert failure drops the batch, so this is at
    # most one real attempt per pending batch.
    for _ in range(2):
        if not dl_batcher.pending_count:
            break
        await _do_dl_flush()
    save_state(durable_id, counters["total"])
    dl_timer.cancel()
    with suppress(asyncio.CancelledError):
        await dl_timer


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
