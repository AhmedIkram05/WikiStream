"""Dead-letter queue for validation-failed events (plan §4A).

Validation failures (invalid JSON, bad timestamps, schema violations) are
written to default.dead_letter with a machine-readable reason — buffered
(async_insert=1, at-most-once like the main batch), so long-tail quality
gaps surface in Grafana/BigQuery instead of vanishing.

Rows accumulate in DeadLetterBatcher and flush as ONE insert per batch: the
SSE loop must never await a per-event round-trip (a schema-drift burst once
meant one sequential insert per bad event — thousands of round-trips stalled
the stream read until TCP backpressure forced a disconnect/replay churn).
Flushes NEVER crash the consumer: any insert failure is logged and swallowed
(the batch is dropped at-most-once; the cursor stays behind so the server
replay re-runs those events).

Idempotency (partial): the DL table is a plain MergeTree with no dedup key, so
a replay of an already-landed row would land it twice. A bounded ring of LANDED
event ids (populated only on a successful flush) lets the consumer skip
re-dead-lettering those events. This covers the replay window within one
process (the common case: a reconnect whose cursor was clamped behind a pending
main batch); cross-restart duplicates remain — the complete fix is a
ReplacingMergeTree key on the DL table, tracked as a follow-up.
"""

import logging
from collections import deque
from datetime import datetime, timezone

from src.cursors import _max_id

logger = logging.getLogger("wikistream.dead_letter")

TABLE = "default.dead_letter"
COLUMNS = ["inserted_at", "reason", "wiki", "title", "event"]
SETTINGS = {"async_insert": 1, "wait_for_async_insert": 0}


class DeadLetterBatcher:
    """Buffer dead-letter rows; flush one insert per batch (at-most-once).

    Mirrors EventBatcher: a size threshold decides when a flush is due (the
    consumer's flush timer owns the age threshold — DL rows are too rare for
    add-time age checks to fire predictably), a failed flush drops the batch
    (rows are regenerable: the cursor only advances on success, so the server
    replay re-runs the events), and the caller decides fire-and-forget vs
    awaited flush.
    """

    def __init__(self, max_rows: int = 100, dedup_capacity: int = 10_000) -> None:
        self.max_rows = max_rows
        self._pending: list[list] = []
        self._pending_ids: list[str] = []
        self._batch_max_id: str | None = None
        # Ring of event ids whose DL rows LANDED (marked only on a successful
        # flush). Mirrors EventBatcher's ring: the set mirror gives O(1)
        # membership, and the deque(maxlen) evicts the oldest on overflow.
        self._landed = deque(maxlen=dedup_capacity)
        self._landed_set: set[str] = set()

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def landed(self, event_id: str | None) -> bool:
        """True iff event_id's DL row already landed (None → False, never marks)."""
        return event_id is not None and event_id in self._landed_set

    def _mark_landed(self, ids: list[str]) -> None:
        for event_id in ids:
            if len(self._landed) == self._landed.maxlen:
                self._landed_set.discard(self._landed.popleft())
            self._landed.append(event_id)
            self._landed_set.add(event_id)

    def add(
        self,
        *,
        reason: str,
        wiki: str,
        title: str,
        event: str,
        event_id: str | None,
    ) -> bool:
        """Append a row; True when a flush is due (max_rows reached)."""
        self._pending.append([_now_utc(), reason, wiki, title, event])
        if event_id is not None:
            self._pending_ids.append(event_id)
            self._batch_max_id = (
                event_id
                if self._batch_max_id is None
                else _max_id(self._batch_max_id, event_id)
            )
        return len(self._pending) >= self.max_rows

    async def flush(self, client) -> tuple[bool, int, str | None]:
        """Flush pending rows as one insert; returns (dropped, count, max_id).

        dropped=True (insert failed): the batch is gone (at-most-once), its ids
        are NOT marked landed, and max_id is None — the caller must not count
        the rows or advance the cursor, so the server replay re-runs them.
        """
        if not self._pending:
            return (False, 0, None)
        pending = self._pending
        batch_ids = self._pending_ids
        batch_max = self._batch_max_id
        self._pending = []
        self._pending_ids = []
        self._batch_max_id = None
        try:
            await client.insert(
                TABLE,
                data=pending,
                column_names=COLUMNS,
                settings=SETTINGS,
            )
        except Exception as exc:
            logger.warning("dead_letter_flush_failed reason=%s", exc)
            return (True, len(pending), None)
        self._mark_landed(batch_ids)
        return (False, len(pending), batch_max)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)
