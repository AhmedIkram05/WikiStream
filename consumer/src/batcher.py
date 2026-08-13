"""Batching + dedup for raw event inserts (plan §4A).

EventBatcher accumulates rows until size/age thresholds are met, then flushes
them in one async_insert to default.raw_events. At-most-once: a failed flush
DROPS the batch (counted by the caller) — the stream is the source of truth
and replay refills the dedup ring after a kill, so duplicates are cheap and
losses are the thing to avoid (Q1: one row per event, best-effort).

Dedup ring is memory-only (capacity 50_000 ≈ 20 min at ~44 ev/s): after a
kill the server's replay (Last-Event-ID) refills it; the BQ parity check is
the long-tail safety net.
"""

import logging
import time
from collections import deque
from typing import Callable

logger = logging.getLogger("wikistream.batcher")

TABLE = "default.raw_events"
COLUMNS = ["inserted_at", "event"]
SETTINGS = {"async_insert": 1, "wait_for_async_insert": 0}


def _max_id(a: str, b: str) -> str:
    """Larger of two ids: numeric compare when both are digit-strings, else string."""
    if a.isdigit() and b.isdigit():
        return a if int(a) >= int(b) else b
    return a if a >= b else b


class EventBatcher:
    def __init__(
        self,
        max_rows: int = 1000,
        max_age_s: float = 5.0,
        dedup_capacity: int = 50_000,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.max_rows = max_rows
        self.max_age_s = max_age_s
        # Ring: deque(maxlen) keeps the newest N ids and evicts the oldest;
        # the set mirror gives O(1) membership. On deque overflow the evicted
        # id is discarded from the set so the two stay in sync.
        self._ring = deque(maxlen=dedup_capacity)
        self._ring_set: set[str] = set()
        self._pending: list[tuple] = []
        self._first_added_at: float | None = None
        self._batch_max_id: str | None = None
        self._now = now or time.monotonic

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def seen(self, event_id: str | None) -> bool:
        """True iff event_id is in the dedup ring (None → False, never marks)."""
        return event_id is not None and event_id in self._ring_set

    def _mark(self, event_id: str) -> None:
        # deque(maxlen) evicts the oldest on overflow; evict the same id from
        # the set mirror FIRST so the two always hold identical contents.
        if len(self._ring) == self._ring.maxlen:
            self._ring_set.discard(self._ring.popleft())
        self._ring.append(event_id)
        self._ring_set.add(event_id)

    def add(self, event_row: tuple, event_id: str | None) -> bool:
        """Append a row; mark event_id (when given); True when a flush is due.

        Age ticks from the FIRST row of the batch, not page-aligned.
        """
        if not self._pending:
            self._first_added_at = self._now()
        self._pending.append(event_row)
        if event_id is not None:
            self._mark(event_id)
            self._batch_max_id = (
                event_id
                if self._batch_max_id is None
                else _max_id(self._batch_max_id, event_id)
            )
        return len(self._pending) >= self.max_rows or (
            self._first_added_at is not None
            and self._now() - self._first_added_at >= self.max_age_s
        )

    async def flush(self, client) -> tuple[str | None, int]:
        """Flush pending rows; returns (max_event_id, count).

        max_event_id = maximum event id in the batch (None if the batch had
        no ids). On ANY exception the batch is DROPPED (at-most-once) and
        (None, len) is returned — the caller logs and counts the loss.
        """
        if not self._pending:
            return (None, 0)
        pending = self._pending
        batch_max = self._batch_max_id
        self._pending = []
        self._first_added_at = None
        self._batch_max_id = None
        try:
            await client.insert(
                TABLE,
                data=pending,
                column_names=COLUMNS,
                settings=SETTINGS,
            )
        except Exception as exc:
            logger.warning("flush_failed reason=%s", exc)
            return (None, len(pending))
        return (batch_max, len(pending))
