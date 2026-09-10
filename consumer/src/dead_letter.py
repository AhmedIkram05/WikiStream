"""Dead-letter queue for validation-failed events (plan §4A).

Validation failures (invalid JSON, bad timestamps, schema violations) are
written to default.dead_letter with a machine-readable reason — buffered
(async_insert=1, at-most-once like the main batch), so long-tail quality
gaps surface in Grafana/BigQuery instead of vanishing. This function
NEVER crashes the consumer: any insert failure is logged and swallowed.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger("wikistream.dead_letter")


async def write_dead_letter(
    client, *, reason: str, wiki: str, title: str, event: str
) -> bool:
    """Insert one dead-letter row; on ANY failure log and swallow (never crash).

    Returns True when the row was accepted (buffered async_insert=1,
    at-most-once like the main batch): the consumer gates its
    dead_lettered counter and cursor advance on this.
    """
    try:
        # ponytail: buffered DL insert, sync stalled SSE loop -> lag.
        # Matches main batch at-most-once.
        await client.insert(
            "default.dead_letter",
            [[datetime.now(timezone.utc), reason, wiki, title, event]],
            column_names=["inserted_at", "reason", "wiki", "title", "event"],
            settings={"async_insert": 1, "wait_for_async_insert": 0},
        )
    except Exception as exc:
        logger.warning("dead_letter_write_failed reason=%s", exc)
        return False
    return True
