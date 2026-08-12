#!/usr/bin/env bash
# Phase 3C — warehouse parity check: ClickHouse vs BigQuery for one window.
# Defaults (no args) to the LAST COMPLETED UTC hour; pass START END (UTC
# 'YYYY-MM-DD HH:MM:SS') for a specific window. Runs at :05 via
# wikistream-parity.timer to validate the export the :00 timer just made.
# Targets the Ubuntu VM — window math uses GNU date.
#
# Depends on: docker (container $CLICKHOUSE_CONTAINER), bq. CLICKHOUSE_PASSWORD
# is required (sourced from /opt/wikistream/.env when present, else env).
# CH-side sums use the SAME rollup SQL as the export (nested in an outer
# SUM), so merge-state can never create false drift — never change the CH
# side to row counts (grain differs from the BQ hourly tables).
#
# Failures are never swallowed: any CH/bq query error or mismatch exits 1.
# One JSON line per run is appended to /var/log/wikistream-parity.log and
# echoed to stdout (journald); that log is the Phase 5 alert hook.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f /opt/wikistream/.env ]; then
  set -a
  . /opt/wikistream/.env
  set +a
fi

if [ -z "${CLICKHOUSE_PASSWORD:-}" ]; then
  echo "[parity] CLICKHOUSE_PASSWORD is not set (source /opt/wikistream/.env or export it)" >&2
  exit 1
fi

: "${CLICKHOUSE_CONTAINER:=wikistream-clickhouse}"
: "${BQ_DATASET:=wikistream}"

LOG_DIR=/var/log/wikistream
LOG_FILE="$LOG_DIR/wikistream-parity.log"
mkdir -p "$LOG_DIR"

if [ $# -eq 2 ]; then
  START="$1"
  END="$2"
elif [ $# -eq 0 ]; then
  END="$(date -u +'%Y-%m-%d %H:00:00')"
  START="$(date -u +'%Y-%m-%d %H:00:00' -d '1 hour ago')"
else
  echo "[parity] usage: $0 [START END]" >&2
  echo "[parity]   START END = UTC 'YYYY-MM-DD HH:MM:SS' (inclusive-exclusive window); no args = last completed UTC hour" >&2
  exit 1
fi

WINDOW_START="$(printf '%sZ' "${START/ /T}")"
WINDOW_END="$(printf '%sZ' "${END/ /T}")"

freshness=ok
status=ok
t_edits=error
t_top_pages=error
t_sizes=error
t_raw_sample=error

# One JSON line to the log and stdout. Phases 5 alerting parses this exact shape.
emit_log() {
  local line
  line="$(python3 - "$WINDOW_START" "$WINDOW_END" "$freshness" "$t_edits" "$t_top_pages" "$t_sizes" "$t_raw_sample" "$status" <<'PY'
import json, sys
from datetime import datetime, timezone
ws, we, freshness, t_edits, t_top, t_sizes, t_raw, status = sys.argv[1:9]
print(json.dumps({
    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "window_start": ws, "window_end": we, "freshness": freshness,
    "tables": {"edits": t_edits, "top_pages": t_top, "sizes": t_sizes, "raw_sample": t_raw},
    "status": status}, sort_keys=True))
PY
  )"
  printf '%s\n' "$line" >> "$LOG_FILE"
  echo "$line"
}

# Freshness gate: the export_runs row for THIS window (not whatever row is
# newest — a manual backfill of another window writes a newer row) must exist
# and be successful.
if ! fresh_json="$(bq query --use_legacy_sql=false --format=json \
    "SELECT exported_at, window_start, window_end, status FROM $BQ_DATASET.export_runs WHERE window_end = TIMESTAMP('${WINDOW_END}') ORDER BY exported_at DESC LIMIT 1")"; then
  echo "[parity] freshness gate: bq export_runs query failed" >&2
  freshness=stale
  status=error
  t_edits="error"
  t_top_pages="error"
  t_sizes="error"
  t_raw_sample="error"
  emit_log
  exit 1
fi

if ! gate="$(python3 - "$fresh_json" "$WINDOW_END" <<'PY'
import json, sys

def norm(ts):
    # bq --format=json renders TIMESTAMP with nanosecond fraction, e.g.
    # '2026-08-12T12:00:00.000Z'; WINDOW_END is '2026-08-12T12:00:00Z'.
    # Normalize both sides so equivalence does not flake on formatting.
    t = (ts or "").strip().replace(" ", "T")
    if "." in t:
        t = t.split(".")[0]
    return t.rstrip("Z") + "Z" if t else ""

data = json.loads(sys.argv[1])
if not data:
    print("EMPTY")
else:
    row = data[0]
    if row.get("status") == "success" and norm(str(row.get("window_end") or "")) == norm(sys.argv[2]):
        print("OK")
    else:
        print(f"MISMATCH {row.get('status')} {row.get('window_end')}")
PY
)"; then
  echo "[parity] freshness gate: could not parse bq export_runs output" >&2
  freshness=stale
  status=error
  t_edits="error"
  t_top_pages="error"
  t_sizes="error"
  t_raw_sample="error"
  emit_log
  exit 1
fi

if [ "$gate" != "OK" ]; then
  echo "[parity] freshness gate failed: $gate (want success/'$WINDOW_END')" >&2
  freshness=stale
  status=error
  t_edits="error"
  t_top_pages="error"
  t_sizes="error"
  t_raw_sample="error"
  emit_log
  exit 1
fi

# Compare CH vs BQ for the window. $1 = result var name, $2 = label,
# $3 = export SQL filename ("" = raw-sample fixed query), $4 = CH aggregate
# over the export SQL, $5 = bq parity SQL, $6 = comma-separated BQ column
# names in CH order. Any query error or mismatch exits 1.
compare_table() {
  local -n tvar=$1
  local name=$2 export_sql=$3 aggregate=$4 bq_sql=$5 bq_cols=$6
  local ch_out bq_out verdict

  if [ -n "$aggregate" ]; then
    if ! ch_out="$(docker exec -i "$CLICKHOUSE_CONTAINER" clickhouse-client --user wikistream \
        --password "$CLICKHOUSE_PASSWORD" --format TSV \
        <<<"SELECT $aggregate FROM (
$(sed "s/{START}/$START/; s/{END}/$END/" "sql/$export_sql")
)")"; then
      echo "[parity] $name: ClickHouse query failed" >&2
      tvar="error"
      status=error
      emit_log
      exit 1
    fi
  else
    # Single source of truth: count over the committed export SQL, so the 10%
    # deterministic-sample predicate can never drift out of sync with export.sh.
    if ! ch_out="$(docker exec -i "$CLICKHOUSE_CONTAINER" clickhouse-client --user wikistream \
        --password "$CLICKHOUSE_PASSWORD" --format TSV \
        <<<"SELECT count() AS row_count FROM (
$(sed "s/{START}/$START/; s/{END}/$END/" "sql/export_raw_sample.sql")
)")"; then
      echo "[parity] $name: ClickHouse query failed" >&2
      tvar="error"
      status=error
      emit_log
      exit 1
    fi
  fi

  # bq --format=json emits columns in ALPHABETICAL order (not SELECT order),
  # so read them by name in the CH column order passed as $6.
  if ! bq_out="$(bq query --use_legacy_sql=false --format=json \
      "$(sed "s/{START}/$START/; s/{END}/$END/" "sql/$bq_sql")" \
      | python3 -c '
import json, sys
cols = sys.argv[1].split(",")
d = json.load(sys.stdin)
print(" ".join(str(d[0][c]) for c in cols))
' "$bq_cols")"; then
    echo "[parity] $name: BigQuery query failed" >&2
    tvar="error"
    status=error
    emit_log
    exit 1
  fi

  verdict="$(python3 - "$name" "$ch_out" "$bq_out" <<'PY'
import sys
name, ch, bq = sys.argv[1], sys.argv[2], sys.argv[3]
chv, bqv = ch.split(), bq.split()
try:
    ok = len(chv) == len(bqv) and all(int(a) == int(b) for a, b in zip(chv, bqv))
except ValueError:
    ok = False
print("ok" if ok else "mismatch")
PY
  )"

  tvar="$verdict"
  if [ "$verdict" != "ok" ]; then
    echo "[parity] $name: CH/BQ mismatch (ch='$ch_out' bq='$bq_out')" >&2
    status=drift
  fi
}

compare_table t_edits      kpi_edits    export_edits.sql      "sum(edits) AS edits, sum(bytes_delta) AS bytes_delta" parity_bq_edits.sql       edits,bytes_delta
compare_table t_top_pages  top_pages    export_top_pages.sql  "sum(edits) AS edits, sum(bytes_delta) AS bytes_delta" parity_bq_top_pages.sql   edits,bytes_delta
compare_table t_sizes      sizes        export_sizes.sql      "sum(edits) AS edits"                                 parity_bq_sizes.sql       edits
compare_table t_raw_sample raw_sample   ""                    ""                                                    parity_bq_raw_sample.sql  row_count

emit_log
if [ "$status" != "ok" ]; then
  exit 1
fi
