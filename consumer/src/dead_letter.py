"""Dead-letter queue for validation-failed events (plan §4A).

Validation failures (invalid JSON, bad timestamps, schema violations) are
written to default.dead_letter with a machine-readable reason — durable,
immediately visible (async_insert=0), so long-tail quality gaps surface in
Grafana/BigQuery instead of vanishing into a dropped row. This function
NEVER crashes the consumer: any insert failure is logged and swallowed.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger("wikistream.dead_letter")


async def write_dead_letter(
    client, *, reason: str, wiki: str, title: str, event: str
) -> bool:
    """Insert one dead-letter row; on ANY failure log and swallow (never crash).

    Returns True only when the row was written durably (async_insert=0): the
    consumer gates its dead_lettered counter AND durable cursor advance on
    this, so a failed DL insert re-runs after reconnect (at-least-once DL)
    instead of silently skipping the event (see consumer.py step 1-3).
    """
    try:
        await client.insert(
            "default.dead_letter",
            [[datetime.now(timezone.utc), reason, wiki, title, event]],
            column_names=["inserted_at", "reason", "wiki", "title", "event"],
            settings={"async_insert": 0},
        )
    except Exception as exc:
        logger.warning("dead_letter_write_failed reason=%s", exc)
        return False
    return True
