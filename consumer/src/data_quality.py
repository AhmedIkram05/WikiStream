"""Hourly SQL data-quality gate for WikiStream raw_events.

Runs one aggregate over the last completed UTC hour, prints ONE JSON verdict
line, writes the verdict to default.pipeline_health (alerting telemetry), and
exits 0 on success (or empty-window skip) / 1 on any check failure.
"""

import atexit
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import clickhouse_connect

logger = logging.getLogger(__name__)

# One window scan; every check is an aggregate. The null checks are cheap
# schema-drift guards even though the MATERIALIZED columns are non-nullable.
WINDOW_SQL = """SELECT
  count()                                                          AS row_count,
  countIf(isNull(wiki))                                            AS null_wiki,
  countIf(isNull(title))                                           AS null_title,
  countIf(isNull(event_type))                                      AS null_event_type,
  countIf(isNull(is_bot))                                          AS null_is_bot,
  countIf(isNull(length_new))                                      AS null_length_new,
  countIf(isNull(event_timestamp))                              AS null_event_timestamp,
  countIf(event_type NOT IN ('edit','new','log','categorize')
          OR isNull(event_type))                                   AS bad_event_type,
  quantileExact(0.5)(lag_seconds)                                  AS median_lag,
  max(skew_seconds)                                                AS max_skew,
  uniqExact(wiki)                                                  AS wiki_count,
  avg(is_bot)                                                      AS bot_ratio
FROM (
  SELECT wiki, title, event_type, is_bot, length_new, event_timestamp,
         dateDiff('second', event_timestamp, inserted_at)              AS lag_seconds,
         greatest(0, dateDiff('second', inserted_at, event_timestamp)) AS skew_seconds
  FROM {table}
  WHERE inserted_at >= '{start}' AND inserted_at < '{end}'
)"""

# Must match WINDOW_SQL's SELECT order.
SQL_COLUMNS = (
    "row_count",
    "null_wiki",
    "null_title",
    "null_event_type",
    "null_is_bot",
    "null_length_new",
    "null_event_timestamp",
    "bad_event_type",
    "median_lag",
    "max_skew",
    "wiki_count",
    "bot_ratio",
)

LAG_MAX_SECONDS = 300
SKEW_MAX_SECONDS = 300
WIKI_MIN = 101
BOT_MIN = 0.05
BOT_MAX = 0.95


def report_status(verdict: dict, client=None) -> tuple | None:
    """Write the gate verdict to default.pipeline_health.

    Never raises and never affects the suite's exit code: a falsy client
    (connection failure) or a failed insert is logged as a warning and
    skipped. The client parameterizes the insert, so the detail JSON is
    passed through unchanged.
    """
    if not verdict or not client:
        logger.warning("pipeline_health status write skipped: no clickhouse client")
        return None
    row = (
        datetime.now(timezone.utc),
        "data_quality",
        "result",
        1.0 if verdict.get("success") else 0.0,
        json.dumps(
            {
                "window_start": verdict.get("window_start"),
                "window_end": verdict.get("window_end"),
                "run_id": verdict.get("run_id"),
                "expectations_passed": int(verdict.get("expectations_passed", 0)),
                "expectations_failed": int(verdict.get("expectations_failed", 0)),
                "row_count": int(verdict.get("row_count", 0)),
            }
        ),
    )
    try:
        client.insert(
            "default.pipeline_health",
            data=[row],
            column_names=["ts", "source", "metric", "value", "detail"],
            settings={"async_insert": 0},
        )
    except Exception as exc:
        logger.warning("pipeline_health status write failed: %s", exc)
    return row


def _check_results(values: dict) -> list[bool]:
    """The 11 checks, in a stable order for the verdict counts."""
    return [
        values["null_wiki"] == 0,
        values["null_title"] == 0,
        values["null_event_type"] == 0,
        values["null_is_bot"] == 0,
        values["null_length_new"] == 0,
        values["null_event_timestamp"] == 0,
        values["bad_event_type"] == 0,
        values["median_lag"] <= LAG_MAX_SECONDS,
        values["max_skew"] <= SKEW_MAX_SECONDS,
        values["wiki_count"] >= WIKI_MIN,
        BOT_MIN <= values["bot_ratio"] <= BOT_MAX,
    ]


def _env_int(name: str, default: int) -> int:
    """Parse an integer env var with a named error instead of a raw traceback."""
    raw = os.getenv(name, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}={raw!r} is not an integer") from exc


def _window_end() -> datetime:
    """Return the window end in UTC; DATA_QUALITY_WINDOW_END is test-only."""
    raw = os.getenv("DATA_QUALITY_WINDOW_END")
    if not raw:
        return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    try:
        end = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"DATA_QUALITY_WINDOW_END={raw!r} is not ISO-8601") from exc
    if end.tzinfo is None:
        return end.replace(tzinfo=timezone.utc)
    return end.astimezone(timezone.utc)


def main() -> int:
    run_id = datetime.now(timezone.utc).isoformat()
    password = os.getenv("CLICKHOUSE_PASSWORD")
    if not password:
        print("CLICKHOUSE_PASSWORD is required", file=sys.stderr)
        return 1
    try:
        host = os.getenv("CLICKHOUSE_HOST", "clickhouse")
        port = _env_int("CLICKHOUSE_PORT", 8123)
        user = os.getenv("CLICKHOUSE_USER", "wikistream")
        table = os.getenv("DATA_QUALITY_TABLE", "default.raw_events")
        window_hours = _env_int("DATA_QUALITY_WINDOW_HOURS", 1)
        row_min = _env_int("DATA_QUALITY_ROW_MIN", 50000)
        row_max = _env_int("DATA_QUALITY_ROW_MAX", 5000000)
        end = _window_end()
    except ValueError as exc:
        # Bad configuration must still honour the one-JSON-line contract; no
        # client exists yet, so there is no pipeline_health row to write.
        print(json.dumps({"success": False, "run_id": run_id, "error": str(exc)}))
        return 1

    start = end - timedelta(hours=window_hours)
    start_s = start.strftime("%Y-%m-%d %H:%M:%S")
    end_s = end.strftime("%Y-%m-%d %H:%M:%S")

    # Every exit path writes the verdict: register once, before the connection,
    # so a future early return cannot forget it. The closure reads the live
    # locals (client may be None if the connection itself failed).
    verdict = {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "run_id": run_id,
    }
    client = None
    atexit.register(lambda: report_status(verdict, client))

    try:
        client = clickhouse_connect.get_client(
            host=host, port=port, username=user, password=password
        )
    except Exception as exc:
        # Construction-time failure: no pipeline_health row is written (client
        # is None), so Grafana's data-quality alert cannot fire — only the
        # systemd OnFailure path covers this.
        verdict = {
            "success": False,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "run_id": run_id,
            "error": str(exc),
        }
        print(json.dumps(verdict))
        return 1

    try:
        result = client.query(
            WINDOW_SQL.format(table=table, start=start_s, end=end_s)
        ).first_row
        values = dict(zip(SQL_COLUMNS, result, strict=True))
    except Exception as exc:
        missing = "Unknown table" in str(exc) or "does not exist" in str(exc)
        # A missing table is a misconfiguration, not an empty window: it must
        # fail the run (and write value 0.0) or the alert would never fire on a
        # dropped/renamed DATA_QUALITY_TABLE.
        verdict = {
            "success": False,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "run_id": run_id,
            "expectations_passed": 0,
            "expectations_failed": 1,
            "row_count": 0,
            "error": (f"table {table} missing: {exc}" if missing else str(exc)),
        }
        print(json.dumps(verdict))
        return 1

    row_count = values["row_count"]
    if row_count == 0:
        verdict = {
            "skipped": True,
            "success": True,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "run_id": run_id,
        }
        print(json.dumps(verdict))
        return 0

    # Row-count is enforced on the full window; the 11 checks are
    # scale-invariant distribution checks.
    if not (row_min <= row_count <= row_max):
        verdict = {
            "success": False,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "run_id": run_id,
            "expectations_passed": 0,
            "expectations_failed": 1,
            "row_count": row_count,
            "error": f"row_count {row_count} outside [{row_min}, {row_max}]",
        }
        print(json.dumps(verdict))
        return 1

    checks = _check_results(values)
    passed = sum(checks)
    failed = len(checks) - passed
    verdict = {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "success": failed == 0,
        "run_id": run_id,
        "expectations_passed": passed,
        "expectations_failed": failed,
        "row_count": row_count,
    }
    print(json.dumps(verdict))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
