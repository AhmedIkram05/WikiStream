"""Warehouse parity check: ClickHouse vs BigQuery for one UTC window.

Stdlib-only port of the original parity.sh. The systemd unit execs this
file directly through flock. The logic that bash
was doing badly — window math, JSON parsing, value comparison — is now plain
Python and unit-tested in tests/warehouse/test_parity.py.

Defaults (no args) to a TRAILING 2-HOUR window mirroring export.sh's overlap
("last two completed UTC hours"); pass START END (UTC 'YYYY-MM-DD HH:MM:SS')
for a specific window. Runs at :05 via wikistream-parity.timer so it validates
the slice the :00 timer just merged — overlap hour included, so late-arrival
rehydration is checked, not assumed.

CH-side sums use the SAME rollup SQL as the export (nested in an outer SUM),
so merge-state can never create false drift — never change the CH side to row
counts (grain differs from the BQ hourly tables).

Failures are never swallowed: any CH/bq query error or mismatch exits 1. One
JSON line per run is appended to $PARITY_LOG_DIR/wikistream-parity.log and
echoed to stdout (journald); that log is the Phase 5 alert hook.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SQL_DIR = SCRIPT_DIR / "sql"
WINDOW_FMT = "%Y-%m-%d %H:00:00"

CLICKHOUSE_CONTAINER = os.environ.get("CLICKHOUSE_CONTAINER", "wikistream-clickhouse")
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "wikistream")
CLICKHOUSE_HTTP_URL = os.environ.get(
    "CLICKHOUSE_HTTP_URL",
    f"http://127.0.0.1:{os.environ.get('CLICKHOUSE_PORT', '8123')}/",
)
BQ_DATASET = os.environ.get("BQ_DATASET", "wikistream")
LOG_DIR = os.environ.get("PARITY_LOG_DIR", "/var/log/wikistream")
LOG_NAME = "wikistream-parity.log"

#: name, export SQL, CH aggregate ("" = raw-sample count), bq parity SQL, BQ cols
COMPARISONS = (
    (
        "edits",
        "export_edits.sql",
        "sum(edits) AS edits, sum(bytes_delta) AS bytes_delta",
        "parity_bq_edits.sql",
        "edits,bytes_delta",
    ),
    (
        "top_pages",
        "export_top_pages.sql",
        "sum(edits) AS edits, sum(bytes_delta) AS bytes_delta",
        "parity_bq_top_pages.sql",
        "edits,bytes_delta",
    ),
    (
        "sizes",
        "export_sizes.sql",
        "sum(edits) AS edits",
        "parity_bq_sizes.sql",
        "edits",
    ),
    (
        "raw_sample",
        "",
        "",
        "parity_bq_raw_sample.sql",
        "row_count",
    ),
)


class ParityError(RuntimeError):
    """A query, config, or health-write failure that must exit non-zero."""


class UsageError(RuntimeError):
    """Bad CLI arguments (0 or 2 are the only accepted shapes)."""


def read_password(env_file: str | None = None) -> str:
    """Read CLICKHOUSE_PASSWORD from the environment, else the .env as DATA.

    The .env file is never sourced: only the last matching key is read, so a
    rotated file cannot execute shell or mangle values with quotes/semicolons.
    """
    value = os.environ.get("CLICKHOUSE_PASSWORD")
    if value:
        return value
    path = env_file or os.environ.get("WIKISTREAM_ENV_FILE", "/opt/wikistream/.env")
    if os.path.isfile(path):
        last = None
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("CLICKHOUSE_PASSWORD="):
                    last = line.split("=", 1)[1].rstrip("\n")
        if last is not None:
            return last
    raise ParityError(
        "CLICKHOUSE_PASSWORD is not set (source /opt/wikistream/.env or export it)"
    )


def window_bounds(
    argv: list[str], now: datetime | None = None
) -> tuple[str, str, str, str]:
    """Return (start, end, window_start, window_end) for the parity run.

    Explicit args are used verbatim; no args mirror export.sh's trailing
    2-hour overlap ending at the current top of the hour.
    """
    if len(argv) == 2:
        start, end = argv
    elif len(argv) == 0:
        end_dt = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
        start_dt = end_dt - timedelta(hours=2)
        start = start_dt.strftime(WINDOW_FMT)
        end = end_dt.strftime(WINDOW_FMT)
    else:
        raise UsageError(
            "usage: parity.py [START END]\n"
            "  START END = UTC 'YYYY-MM-DD HH:MM:SS' "
            "(inclusive-exclusive window); no args = trailing 2 completed UTC hours"
        )
    return start, end, f"{start.replace(' ', 'T')}Z", f"{end.replace(' ', 'T')}Z"


def substitute(sql: str, start: str, end: str) -> str:
    return sql.replace("{START}", start).replace("{END}", end)


def ch_docker_query(
    sql: str,
    *,
    password: str,
    container: str | None = None,
    user: str | None = None,
) -> str:
    """Run a TSV query through `docker exec clickhouse-client`.

    The password goes on stdin (`--password` with no value), never in argv.
    """
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container or CLICKHOUSE_CONTAINER,
            "clickhouse-client",
            "--user",
            user or CLICKHOUSE_USER,
            "--password",
            "--format",
            "TSV",
            "--query",
            sql,
        ],
        input=password + "\n",
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ParityError(f"ClickHouse query failed: {proc.stderr.strip()}")
    return proc.stdout


def ch_http(
    sql: str,
    *,
    password: str,
    user: str | None = None,
    url: str | None = None,
) -> str:
    """POST a query to the ClickHouse HTTP API.

    The password rides in a header, not argv, and stdin stays free for the
    request body (the reason the old curl config existed).
    """
    request = urllib.request.Request(
        url or CLICKHOUSE_HTTP_URL,
        data=sql.encode(),
        method="POST",
        headers={
            "X-ClickHouse-User": user or CLICKHOUSE_USER,
            "X-ClickHouse-Key": password,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.read().decode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise ParityError(f"ClickHouse HTTP error: {body}") from exc
    except OSError as exc:
        raise ParityError(f"ClickHouse HTTP error: {exc}") from exc


def bq_query(sql: str) -> str:
    """Run a BigQuery query and return the --format=json payload."""
    proc = subprocess.run(
        ["bq", "query", "--use_legacy_sql=false", "--format=json", sql],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ParityError(f"bq query failed: {proc.stderr.strip()}")
    return proc.stdout


def norm_ts(ts: str | None) -> str:
    """Normalize a rendered TIMESTAMP for comparison.

    bq --format=json renders nanosecond fractions ('...12:00:00.000Z');
    WINDOW_END is second precision ('...12:00:00Z'). Both become the same
    string so the freshness gate does not flake on formatting.
    """
    text = (ts or "").strip().replace(" ", "T")
    if "." in text:
        text = text.split(".")[0]
    return text.rstrip("Z") + "Z" if text else ""


def freshness_gate(fresh_json: str, window_end: str) -> str:
    """Return 'OK', 'EMPTY', or 'MISMATCH <status> <window_end>'."""
    data = json.loads(fresh_json)
    if not data:
        return "EMPTY"
    row = data[0]
    if row.get("status") == "success" and norm_ts(
        str(row.get("window_end") or "")
    ) == norm_ts(window_end):
        return "OK"
    return f"MISMATCH {row.get('status')} {row.get('window_end')}"


def bq_row_values(bq_json: str, columns: str) -> str:
    """Read named BQ columns (which arrive alphabetically) in CH order."""
    data = json.loads(bq_json)
    return " ".join(str(data[0][col]) for col in columns.split(","))


def values_match(ch_out: str, bq_out: str) -> bool:
    """Compare TSV/space-separated numeric values, position by position."""
    ch_values, bq_values = ch_out.split(), bq_out.split()
    try:
        return len(ch_values) == len(bq_values) and all(
            int(a) == int(b) for a, b in zip(ch_values, bq_values, strict=False)
        )
    except ValueError:
        return False


def emit_log(
    *,
    window_start: str,
    window_end: str,
    freshness: str,
    tables: dict[str, str],
    status: str,
    log_file: Path,
    stream=None,
    now: datetime | None = None,
) -> str:
    """Append one JSON line to the log and echo it to stdout."""
    line = json.dumps(
        {
            "ts": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_start": window_start,
            "window_end": window_end,
            "freshness": freshness,
            "tables": tables,
            "status": status,
        },
        sort_keys=True,
    )
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, file=stream or sys.stdout)
    return line


def _ch_sql(export_sql: str, aggregate: str, start: str, end: str) -> str:
    if aggregate:
        body = substitute((SQL_DIR / export_sql).read_text(), start, end)
        return f"SELECT {aggregate} FROM (\n{body}\n)"
    body = substitute((SQL_DIR / "export_raw_sample.sql").read_text(), start, end)
    return f"SELECT count() AS row_count FROM (\n{body}\n)"


def compare_table(
    name: str,
    export_sql: str,
    aggregate: str,
    bq_sql: str,
    bq_cols: str,
    *,
    start: str,
    end: str,
    password: str,
) -> str:
    """Compare one table; returns 'ok', 'mismatch', or 'error'."""
    try:
        ch_out = ch_docker_query(
            _ch_sql(export_sql, aggregate, start, end), password=password
        )
        bq_out = bq_row_values(
            bq_query(substitute((SQL_DIR / bq_sql).read_text(), start, end)),
            bq_cols,
        )
    except (ParityError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        # Query failure, missing SQL file, or malformed bq JSON: a clean error
        # (log + exit 1), never a traceback.
        print(f"[parity] {name}: {exc}", file=sys.stderr)
        return "error"
    if not values_match(ch_out, bq_out):
        print(
            f"[parity] {name}: CH/BQ mismatch (ch='{ch_out}' bq='{bq_out}')",
            file=sys.stderr,
        )
        return "mismatch"
    return "ok"


def write_health(
    status: str, window_start: str, window_end: str, *, password: str
) -> int:
    """Write the verdict to pipeline_health and prove the row landed."""
    value = 1.0 if status == "ok" else 0.0
    detail = json.dumps(
        {"status": status, "window_start": window_start, "window_end": window_end},
        separators=(",", ":"),
    ).replace("'", "\\'")
    insert = (
        "INSERT INTO default.pipeline_health (ts, source, metric, value, detail) "
        f"VALUES (now(), 'parity', 'result', {value}, '{detail}')"
    )
    try:
        ch_http(insert, password=password)
        count_text = ch_http(
            "SELECT count() FROM default.pipeline_health WHERE source = 'parity' "
            "AND metric = 'result' "
            f"AND detail LIKE '%{window_end}%'",
            password=password,
        )
        count = int(count_text.strip())
    except (ParityError, ValueError) as exc:
        raise ParityError(f"pipeline_health write failed: {exc}") from exc
    if count < 1:
        raise ParityError(f"pipeline_health row not found (count={count})")
    return count


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        start, end, window_start, window_end = window_bounds(argv)
    except UsageError as exc:
        print(f"[parity] {exc}", file=sys.stderr)
        return 1

    try:
        password = read_password()
    except ParityError as exc:
        print(f"[parity] {exc}", file=sys.stderr)
        return 1

    log_file = Path(LOG_DIR) / LOG_NAME
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[parity] cannot create log dir: {exc}", file=sys.stderr)
        return 1

    freshness = "ok"
    status = "ok"
    tables = {name: "error" for name, *_ in COMPARISONS}

    def log() -> None:
        emit_log(
            window_start=window_start,
            window_end=window_end,
            freshness=freshness,
            tables=tables,
            status=status,
            log_file=log_file,
        )

    fresh_sql = (
        f"SELECT exported_at, window_start, window_end, status "
        f"FROM {BQ_DATASET}.export_runs "
        f"WHERE window_end = TIMESTAMP('{window_end}') "
        "ORDER BY exported_at DESC LIMIT 1"
    )
    try:
        fresh_json = bq_query(fresh_sql)
        gate = freshness_gate(fresh_json, window_end)
    except (ParityError, ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"[parity] freshness gate: {exc}", file=sys.stderr)
        freshness = "stale"
        status = "error"
        log()
        return 1
    if gate != "OK":
        print(
            f"[parity] freshness gate failed: {gate} (want success/'{window_end}')",
            file=sys.stderr,
        )
        freshness = "stale"
        status = "error"
        log()
        return 1

    for name, export_sql, aggregate, bq_sql, bq_cols in COMPARISONS:
        result = compare_table(
            name,
            export_sql,
            aggregate,
            bq_sql,
            bq_cols,
            start=start,
            end=end,
            password=password,
        )
        tables[name] = result
        if result == "error":
            status = "error"
            log()
            return 1
        if result == "mismatch":
            status = "drift"

    log()
    try:
        write_health(status, window_start, window_end, password=password)
    except ParityError as exc:
        print(f"[parity] {exc}", file=sys.stderr)
        return 1
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
