"""Hourly Great Expectations data-quality check for WikiStream raw_events.

Runs a windowed (last completed UTC hour) validation against ClickHouse via a
runtime batch request, prints ONE JSON line to stdout, and exits 0 on success
(or empty-window skip) / 1 on any expectation failure.
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import clickhouse_connect
import great_expectations as gx
from great_expectations.core.batch import RuntimeBatchRequest

logging.getLogger("great_expectations").setLevel(logging.CRITICAL)

# Bound is a raw template: the window is interpolated before GX runs the query.
WINDOW_SQL = """SELECT wiki, title, event_type, is_bot, length_new, event_timestamp,
       dateDiff('second', event_timestamp, inserted_at) AS lag_seconds,
       greatest(0, dateDiff('second', inserted_at, event_timestamp)) AS skew_seconds
FROM {table}
WHERE inserted_at >= '{start}' AND inserted_at < '{end}'"""


def main() -> int:
    host = os.getenv("CLICKHOUSE_HOST", "clickhouse")
    port = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    user = os.getenv("CLICKHOUSE_USER", "wikistream")
    password = os.getenv("CLICKHOUSE_PASSWORD")
    if password is None:
        print("CLICKHOUSE_PASSWORD is required", file=sys.stderr)
        return 1
    table = os.getenv("GX_TABLE", "raw_events")
    window_hours = int(os.getenv("GX_WINDOW_HOURS", "1"))
    # Production defaults match the observed ~160k rows/hour; tests relax them.
    row_min = int(os.getenv("GX_ROW_MIN", "50000"))
    row_max = int(os.getenv("GX_ROW_MAX", "500000"))
    run_id = datetime.now(timezone.utc).isoformat()

    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=window_hours)
    start_s = start.strftime("%Y-%m-%d %H:%M:%S")
    end_s = end.strftime("%Y-%m-%d %H:%M:%S")

    client = clickhouse_connect.get_client(
        host=host, port=port, username=user, password=password
    )
    try:
        row_count = client.query(
            f"SELECT count() FROM {table} "
            f"WHERE inserted_at >= '{start_s}' AND inserted_at < '{end_s}'"
        ).first_row[0]
    except Exception as exc:
        missing = "Unknown table" in str(exc) or "does not exist" in str(exc)
        if not missing:
            # Keep the JSON-line contract on failure too: the hourly log is
            # parsed by Phase 5 alerts, so no raw-traceback-only exits.
            print(
                json.dumps(
                    {
                        "success": False,
                        "window_start": start.isoformat(),
                        "window_end": end.isoformat(),
                        "run_id": run_id,
                        "error": str(exc),
                    }
                )
            )
            return 1
        row_count = 0
    if row_count == 0:
        print(
            json.dumps(
                {
                    "skipped": True,
                    "success": True,
                    "window_start": start.isoformat(),
                    "window_end": end.isoformat(),
                    "run_id": run_id,
                }
            )
        )
        return 0

    ctx = gx.get_context()
    ctx.add_datasource(
        name="runtime_ds",
        class_name="Datasource",
        execution_engine={
            "class_name": "SqlAlchemyExecutionEngine",
            "connection_string": f"clickhouse://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/default",
        },
        data_connectors={
            "default_runtime_data_connector_name": {
                "class_name": "RuntimeDataConnector",
                "batch_identifiers": ["default_identifier_name"],
            }
        },
    )
    batch_request = RuntimeBatchRequest(
        datasource_name="runtime_ds",
        data_connector_name="default_runtime_data_connector_name",
        data_asset_name=table,
        runtime_parameters={
            "query": WINDOW_SQL.format(table=table, start=start_s, end=end_s)
        },
        batch_identifiers={"default_identifier_name": "default_identifier"},
    )
    validator = ctx.get_validator(batch_request=batch_request)
    validator.expect_table_row_count_to_be_between(min_value=row_min, max_value=row_max)
    for column in (
        "wiki",
        "title",
        "event_type",
        "is_bot",
        "length_new",
        "event_timestamp",
    ):
        validator.expect_column_values_to_not_be_null(column=column)
    validator.expect_column_values_to_be_in_set(
        column="event_type", value_set=["edit", "new", "log", "categorize"]
    )
    validator.expect_column_median_to_be_between(
        column="lag_seconds", min_value=None, max_value=300
    )
    validator.expect_column_max_to_be_between(
        column="skew_seconds", min_value=None, max_value=300
    )
    validator.expect_column_unique_value_count_to_be_between(
        column="wiki", min_value=101, max_value=None
    )
    validator.expect_column_mean_to_be_between(
        column="is_bot", min_value=0.05, max_value=0.40
    )

    result = validator.validate()
    results = list(result.results)
    passed = sum(1 for r in results if r.success)
    failed = len(results) - passed
    print(
        json.dumps(
            {
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "success": bool(result.success),
                "run_id": run_id,
                "expectations_passed": passed,
                "expectations_failed": failed,
                "row_count": row_count,
            }
        )
    )
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
