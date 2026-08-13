"""Periodic liveness telemetry for the consumer (plan §5A).

Writes one row per tick to default.pipeline_health: a fixed source/metric
with a value of 1.0 and a JSON `detail` carrying per-counter deltas since the
previous tick plus cumulative totals (incl. resumed_from). Insert failures
are logged and swallowed — alerting must never take the consumer down.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def build_row(counters: dict, previous: dict | None, ts) -> tuple:
    prev = previous or {}
    detail = {
        "inserted_delta": (
            0 if previous is None else counters["total"] - prev.get("total", 0)
        ),
        "dead_lettered_delta": (
            0
            if previous is None
            else counters["dead_lettered"] - prev.get("dead_lettered", 0)
        ),
        "insert_failed_delta": (
            0
            if previous is None
            else counters["insert_failed"] - prev.get("insert_failed", 0)
        ),
        "duplicates_skipped_delta": (
            0
            if previous is None
            else counters["duplicates_skipped"] - prev.get("duplicates_skipped", 0)
        ),
        "total": counters["total"],
        "dead_lettered": counters["dead_lettered"],
        "insert_failed": counters["insert_failed"],
        "duplicates_skipped": counters["duplicates_skipped"],
        "resumed_from": counters["resumed_from"],
    }
    return (ts, "consumer", "heartbeat", 1.0, json.dumps(detail))


async def heartbeat_loop(client, counters, stop, interval: float = 15.0) -> None:
    previous = None
    while not stop.is_set():
        # Stop-aware wait: if shutdown fires mid-interval, wake promptly so the
        # final flush below lands inside the consumer's <=10s join budget.
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            break
        row = build_row(counters, previous, datetime.now(timezone.utc))
        try:
            await client.insert(
                "default.pipeline_health",
                data=[row],
                column_names=["ts", "source", "metric", "value", "detail"],
                settings={"async_insert": 1, "wait_for_async_insert": 0},
            )
        except Exception as exc:
            logger.warning("heartbeat_insert_failed reason=%s", exc)
        previous = dict(counters)
    # Final flush of the current tick on shutdown (same swallow semantics).
    row = build_row(counters, previous, datetime.now(timezone.utc))
    try:
        await client.insert(
            "default.pipeline_health",
            data=[row],
            column_names=["ts", "source", "metric", "value", "detail"],
            settings={"async_insert": 1, "wait_for_async_insert": 0},
        )
    except Exception as exc:
        logger.warning("heartbeat_insert_failed reason=%s", exc)
