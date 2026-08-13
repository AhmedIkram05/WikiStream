"""Semantic freshness healthcheck for the consumer container (plan §4B, 4.2.6).

SYNC probe (consumer.py is asyncio — untouched): when default.raw_events
went stale the consumer is wedged, so send SIGTERM to PID 1 (uv, the
container's init) which forwards it to the python consumer — graceful final
flush + durable state save — then Docker's restart policy restarts the
container from the last-event-id (ADR-004 wedge detector, zero loss).

Never crashes: every failure path logs and exits 1 (Docker marks the
container unhealthy but does NOT restart — only the SIGTERM path does).
"""

import logging
import os
import signal
import sys
from datetime import UTC, datetime, timedelta

from clickhouse_connect import get_client

CLICKHOUSE_HOST = os.environ.get("CLICKHOUSE_HOST") or "localhost"
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER") or "wikistream"
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD")
# Ports/thresholds parse inside main(): a bad env value must not crash at
# import (that would leave the container unhealthy with no diagnostic).
CLICKHOUSE_PORT = os.environ.get("CLICKHOUSE_PORT", "8123")
HEALTH_STALE_SECONDS = os.environ.get("HEALTH_STALE_SECONDS", "300")
# Table override for hermetic ch tests (same pattern as gx's GX_TABLE):
# ops-set, never user input.
HEALTHCHECK_TABLE = os.environ.get("HEALTHCHECK_TABLE") or "default.raw_events"

logger = logging.getLogger("wikistream.healthcheck")


def is_fresh(max_inserted: datetime | None, now: datetime, stale_seconds: int) -> bool:
    """True when the newest raw_events row is inside the stale window.

    Fresh iff max_inserted >= now - timedelta(seconds=stale_seconds): AT the
    threshold counts as fresh — a row exactly `stale_seconds` old has not yet
    crossed the boundary. None (empty table, fresh deploy) and future values
    (clock skew) are fresh. Non-positive stale_seconds pins the one edge:
    fresh iff max_inserted == now, so any older row is stale. max_inserted is
    a naive datetime rendered in the column's explicit 'UTC' timezone
    (probe-verified: clickhouse-connect read-back == formatDateTime(...,'UTC')),
    so it is compared against the same frame — datetime.now(UTC) with its
    tzinfo stripped; both are UTC wall-clock on any host.
    """
    if max_inserted is None:
        return True
    if stale_seconds <= 0:
        return max_inserted == now
    return max_inserted >= now - timedelta(seconds=stale_seconds)


def main() -> int:
    logging.basicConfig(format="%(levelname)-5s %(message)s", level=logging.INFO)
    try:
        port = int(CLICKHOUSE_PORT)
        stale_seconds = int(HEALTH_STALE_SECONDS)
        now = datetime.now(UTC).replace(tzinfo=None)
        client = get_client(
            host=CLICKHOUSE_HOST,
            port=port,
            username=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            connect_timeout=10.0,
        )
        try:
            max_inserted = client.query(
                f"SELECT max(inserted_at) FROM {HEALTHCHECK_TABLE}"
            ).first_row[0]
        finally:
            client.close()
        if not is_fresh(max_inserted, now, stale_seconds):
            logger.info(
                "healthcheck stale max(inserted_at)=%s (>%ds)",
                max_inserted,
                stale_seconds,
            )
            os.kill(1, signal.SIGTERM)
        # Killed PID 1 in the real container (uv tears everything down) so we
        # never return; with a mocked kill (tests) this is the success path.
        return 0
    except Exception as exc:
        logger.error("healthcheck failed reason=%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())