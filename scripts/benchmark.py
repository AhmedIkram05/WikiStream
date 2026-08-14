#!/usr/bin/env python3
"""Phase 7b — raw-scan vs MV latency benchmark against real accumulated data.

Measures client-side wall-clock latency (p50/p99/mean) for the canonical
dashboard queries in raw-table form vs MV form, on the same 24h window of
the REAL dataset (VM ClickHouse, 50.2M rows).

Usage:
    CH_PASSWORD=... python scripts/benchmark.py [--runs N]

Rows-scanned: best-effort from system.query_log (SYSTEM FLUSH LOGS + retry);
falls back to window row counts (marked with *) — the VM's data disk is
~98% full and query_log flushes fail silently, so per-query read_rows is
often unavailable (documented Phase 7 finding).
"""
import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from burst_test import CH_HOST, CH_PORT, CH_DB, ch_query  # noqa: E402

RUNS = 5
WINDOW = "24 HOUR"

QUERIES = {
    "Q1 edit velocity (24h)": {
        "RAW": (
            f"SELECT toStartOfMinute(inserted_at) t, wiki, is_bot, count() edits, "
            f"sum(toInt64(length_new)-toInt64(length_old)) bytes_delta "
            f"FROM {CH_DB}.raw_events "
            f"WHERE event_type IN ('edit','new') AND wiki != '' "
            f"AND inserted_at >= now()-INTERVAL {WINDOW} "
            f"GROUP BY t, wiki, is_bot /* BENCH-Q1-RAW */"
        ),
        "MV": (
            f"SELECT minute t, wiki, is_bot, edits, bytes_delta "
            f"FROM {CH_DB}.mv_edits_per_minute "
            f"WHERE minute >= toStartOfMinute(now()-INTERVAL {WINDOW}) "
            f"/* BENCH-Q1-MV */"
        ),
    },
    "Q2 top pages (24h)": {
        "RAW": (
            f"SELECT title, wiki, count() edits "
            f"FROM {CH_DB}.raw_events "
            f"WHERE event_type IN ('edit','new') AND wiki != '' "
            f"AND inserted_at >= now()-INTERVAL {WINDOW} "
            f"GROUP BY title, wiki ORDER BY edits DESC LIMIT 10 /* BENCH-Q2-RAW */"
        ),
        "MV": (
            f"SELECT title, wiki, sum(edits) edits "
            f"FROM {CH_DB}.mv_top_pages_per_minute "
            f"WHERE minute >= toStartOfMinute(now()-INTERVAL {WINDOW}) "
            f"GROUP BY title, wiki ORDER BY edits DESC LIMIT 10 /* BENCH-Q2-MV */"
        ),
    },
}

# Window-count fallbacks for rows scanned (used when query_log is unreliable).
SCAN_COUNTS = {
    "RAW": f"SELECT count() FROM {CH_DB}.raw_events "
           f"WHERE event_type IN ('edit','new') AND wiki != '' "
           f"AND inserted_at >= now()-INTERVAL {WINDOW}",
    "MV": f"SELECT count() FROM {CH_DB}.mv_edits_per_minute "
          f"WHERE minute >= toStartOfMinute(now()-INTERVAL {WINDOW})",
}


def query_log_metrics(marker: str) -> dict | None:
    """Best-effort server-side read_rows/ms for the last QueryFinish of the marker.

    Returns None when system.query_log is unreliable (disk-full flush drops).
    """
    for attempt in range(4):
        try:
            ch_query("SYSTEM FLUSH LOGS")
        except Exception:
            pass  # flush may fail on a full disk; fall back below
        try:
            row = ch_query(
                f"SELECT query_duration_ms, read_rows FROM system.query_log "
                f"WHERE type='QueryFinish' AND query LIKE '%{marker}%' "
                f"AND event_time >= now()-INTERVAL 2 MINUTE "
                f"ORDER BY event_time DESC LIMIT 1"
            )
        except Exception:
            row = []
        if row:
            return {"ms": int(row[0][0]), "rows": int(row[0][1])}
        time.sleep(1.0)
    return None


def run_form(sql: str, marker: str, runs: int = RUNS) -> tuple[list[float], dict | None]:
    ch_query(sql, timeout=600)  # warmup (fills page cache for both forms equally)
    lat_ms: list[float] = []
    last_metrics: dict | None = None
    for _ in range(runs):
        t0 = time.perf_counter()
        ch_query(sql, timeout=600)
        lat_ms.append((time.perf_counter() - t0) * 1000.0)
        m = query_log_metrics(marker)
        if m:
            last_metrics = m
    return lat_ms, last_metrics


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100.0 * len(xs)))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=RUNS)
    ap.add_argument("--only", default="", help="run only queries whose label contains this")
    ap.add_argument("--form", default="", help="run only this form (RAW or MV)")
    args = ap.parse_args()

    if not os.environ.get("CH_PASSWORD"):
        print("CH_PASSWORD is required", file=sys.stderr)
        return 2

    print(f"server: {CH_HOST}:{CH_PORT}  runs/query: {args.runs}  window: {WINDOW}")
    print(f"measurement: client-side wall-clock (includes network RTT); "
          f"comparison raw-vs-MV is the point")
    print()

    for label, forms in QUERIES.items():
        if args.only and args.only not in label:
            continue
        print(f"== {label} ==")
        results = {}
        for form, sql in forms.items():
            if args.form and form != args.form.upper():
                continue
            marker = sql.split("/* ")[-1].split(" */")[0]
            lat_ms, metrics = run_form(sql, marker, runs=args.runs)
            results[form] = {"lat": lat_ms, "metrics": metrics}

            if metrics:
                rows_m = metrics["rows"] / 1e6
                src = ""
            else:
                try:
                    rows = int(ch_query(SCAN_COUNTS[form], timeout=600)[0][0])
                    rows_m = rows / 1e6
                except (TimeoutError, Exception) as exc:  # noqa: BLE001
                    rows_m = 0.0
                    src = "!"
                else:
                    src = "*"
            print(
                f"  {form:3s} p50 {pct(lat_ms, 50):>9.1f} ms  p99 {pct(lat_ms, 99):>9.1f} ms  "
                f"mean {statistics.mean(lat_ms):>9.1f} ms  scanned {rows_m:>8.2f}M rows{src}"
            )
        if "RAW" in results and "MV" in results:
            raw, mv = results["RAW"], results["MV"]
            r50, m50 = pct(raw["lat"], 50), pct(mv["lat"], 50)
            r99, m99 = pct(raw["lat"], 99), pct(mv["lat"], 99)
            print(f"  speedup p50 {r50 / m50:>6.1f}x   p99 {r99 / m99:>6.1f}x")
            print()
    print("* scanned = window row count (query_log read_rows unavailable: "
          "VM data disk ~98% full drops log flushes)")
    print("! scanned = scan-count query itself timed out under disk pressure")
    return 0


if __name__ == "__main__":
    sys.exit(main())