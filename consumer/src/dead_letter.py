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
"""

import logging
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

    def __init__(self, max_rows: int = 100) -> None:
        self.max_rows = max_rows
        self._pending: list[list] = []
        self._batch_max_id: str | None = None

    @property
    def pending_count(self) -> int:
        return len(self._pending)

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
            self._batch_max_id = (
                event_id
                if self._batch_max_id is None
                else _max_id(self._batch_max_id, event_id)
            )
        return len(self._pending) >= self.max_rows

    async def flush(self, client) -> tuple[bool, int, str | None]:
        """Flush pending rows as one insert; returns (dropped, count, max_id).

        dropped=True (insert failed): the batch is gone (at-most-once) and
        max_id is None — the caller must not count the rows or advance the
        cursor, so the server replay re-runs those events.
        """
        if not self._pending:
            return (False, 0, None)
        pending = self._pending
        batch_max = self._batch_max_id
        self._pending = []
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
        return (False, len(pending), batch_max)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)
