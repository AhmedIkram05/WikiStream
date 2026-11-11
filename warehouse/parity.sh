#!/usr/bin/env bash
# Phase 3C — warehouse parity check: ClickHouse vs BigQuery for one window.
# Defaults (no args) to a TRAILING 2-HOUR window mirroring export.sh's overlap
# ("last two completed UTC hours"); pass START END (UTC 'YYYY-MM-DD HH:MM:SS')
# for a specific window. Runs at :05 via wikistream-parity.timer so it
# validates the slice the :00 timer just merged — overlap hour included, so
# late-arrival rehydration is checked, not assumed. Targets the Ubuntu VM —
# window math uses GNU date.
#
# Depends on: docker (container $CLICKHOUSE_CONTAINER), bq, curl.
# CLICKHOUSE_PASSWORD is required (read from /opt/wikistream/.env when
# present, else env); the final telemetry INSERT uses the HTTP API with a
# 0600 curl config so the key never enters argv.
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

ENV_FILE="${WIKISTREAM_ENV_FILE:-/opt/wikistream/.env}"
if [ -f "$ENV_FILE" ]; then
  # Read only the key we need. Sourcing .env would execute arbitrary shell from
  # a rotated file (and values with quotes/semicolons mangle the variables).
  CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-$(grep -E '^CLICKHOUSE_PASSWORD=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)}"
fi

if [ -z "${CLICKHOUSE_PASSWORD:-}" ]; then
  echo "[parity] CLICKHOUSE_PASSWORD is not set (source /opt/wikistream/.env or export it)" >&2
  exit 1
fi

: "${CLICKHOUSE_CONTAINER:=wikistream-clickhouse}"
: "${CLICKHOUSE_USER:=wikistream}"
: "${CLICKHOUSE_HTTP_URL:=http://127.0.0.1:${CLICKHOUSE_PORT:-8123}/}"
: "${BQ_DATASET:=wikistream}"

LOG_DIR="${PARITY_LOG_DIR:-/var/log/wikistream}"
LOG_FILE="$LOG_DIR/wikistream-parity.log"
mkdir -p "$LOG_DIR"

# The final pipeline_health insert uses the HTTP API: clickhouse-client over
# `docker exec -i` cannot take the password on stdin and the INSERT body at the
# same time (it reads stdin as insert data). Keep the key in a 0600 curl config
# so it never appears in argv or the URL.
_curl_escape() {
  local value=$1
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  printf '%s' "$value"
}

CH_HTTP_CONFIG="$(mktemp)"
chmod 600 "$CH_HTTP_CONFIG"
trap 'rm -f "$CH_HTTP_CONFIG"' EXIT
{
  printf 'header = "X-ClickHouse-User: %s"\n' "$(_curl_escape "$CLICKHOUSE_USER")"
  printf 'header = "X-ClickHouse-Key: %s"\n' "$(_curl_escape "$CLICKHOUSE_PASSWORD")"
} > "$CH_HTTP_CONFIG"

ch_http() {
  curl --silent --show-error --fail-with-body --config "$CH_HTTP_CONFIG" \
    --data-binary "$1" "$CLICKHOUSE_HTTP_URL"
}

if [ $# -eq 2 ]; then
  START="$1"
  END="$2"
elif [ $# -eq 0 ]; then
  # Mirror export.sh's trailing 2-hour overlap (same window_end, so the
  # freshness gate still keys on the exported run); parity therefore checks
  # the full exported slice — previous hour's rehydrated rows included.
  END="$(date -u +'%Y-%m-%d %H:00:00')"
  START="$(date -u +'%Y-%m-%d %H:00:00' -d '2 hours ago')"
else
  echo "[parity] usage: $0 [START END]" >&2
  echo "[parity]   START END = UTC 'YYYY-MM-DD HH:MM:SS' (inclusive-exclusive window); no args = trailing 2 completed UTC hours" >&2
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
compare_result=""
compare_table() {
  local name=$1 export_sql=$2 aggregate=$3 bq_sql=$4 bq_cols=$5
  local ch_out bq_out verdict

  if [ -n "$aggregate" ]; then
    # Password via stdin (`--password` with no value, like backup.sh): keeps
    # it out of the docker CLI argv, where any OS-login user could read it
    # via ps.
    if ! ch_out="$(printf '%s\n' "$CLICKHOUSE_PASSWORD" | docker exec -i "$CLICKHOUSE_CONTAINER" clickhouse-client --user wikistream \
        --password --format TSV \
        --query "SELECT $aggregate FROM (
$(sed "s/{START}/$START/; s/{END}/$END/" "sql/$export_sql")
)")"; then
      echo "[parity] $name: ClickHouse query failed" >&2
      compare_result="error"
      status=error
      emit_log
      exit 1
    fi
  else
    # Single source of truth: count over the committed export SQL, so the 10%
    # deterministic-sample predicate can never drift out of sync with export.sh.
    if ! ch_out="$(printf '%s\n' "$CLICKHOUSE_PASSWORD" | docker exec -i "$CLICKHOUSE_CONTAINER" clickhouse-client --user wikistream \
        --password --format TSV \
        --query "SELECT count() AS row_count FROM (
$(sed "s/{START}/$START/; s/{END}/$END/" "sql/export_raw_sample.sql")
)")"; then
      echo "[parity] $name: ClickHouse query failed" >&2
      compare_result="error"
      status=error
      emit_log
      exit 1
    fi
  fi

  # bq --format=json emits columns in ALPHABETICAL order (not SELECT order),
  # so read them by name in the CH column order passed as $5.
  if ! bq_out="$(bq query --use_legacy_sql=false --format=json \
      "$(sed "s/{START}/$START/; s/{END}/$END/" "sql/$bq_sql")" \
      | python3 -c '
import json, sys
cols = sys.argv[1].split(",")
d = json.load(sys.stdin)
print(" ".join(str(d[0][c]) for c in cols))
' "$bq_cols")"; then
    echo "[parity] $name: BigQuery query failed" >&2
    compare_result="error"
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

  if [ "$verdict" != "ok" ]; then
    echo "[parity] $name: CH/BQ mismatch (ch='$ch_out' bq='$bq_out')" >&2
    status=drift
  fi
  compare_result="$verdict"
}

compare_table kpi_edits    export_edits.sql      "sum(edits) AS edits, sum(bytes_delta) AS bytes_delta" parity_bq_edits.sql      edits,bytes_delta
t_edits="$compare_result"
compare_table top_pages    export_top_pages.sql  "sum(edits) AS edits, sum(bytes_delta) AS bytes_delta" parity_bq_top_pages.sql  edits,bytes_delta
t_top_pages="$compare_result"
compare_table sizes        export_sizes.sql      "sum(edits) AS edits"                                 parity_bq_sizes.sql      edits
t_sizes="$compare_result"
compare_table raw_sample   ""                    ""                                                    parity_bq_raw_sample.sql row_count
t_raw_sample="$compare_result"

emit_log

# Phase 5: push the verdict into pipeline_health (alerting telemetry)
_P5_VALUE=0.0; [ "$status" = "ok" ] && _P5_VALUE=1.0
_P5_DETAIL=$(printf '{"status":"%s","window_start":"%s","window_end":"%s"}' "$status" "$WINDOW_START" "$WINDOW_END" | sed "s/'/\\\\'/g")
# HTTP keeps the password out of argv and leaves stdin free for the request
# body; clickhouse-client over `docker exec -i` cannot do both for an INSERT.
_P5_SQL="INSERT INTO default.pipeline_health (ts, source, metric, value, detail) VALUES (now(), 'parity', 'result', $_P5_VALUE, '$_P5_DETAIL')"
if ! ch_http "$_P5_SQL" >/dev/null; then
  echo "[$(date -u)] parity: pipeline_health write failed" >&2
  exit 1
fi

# Prove the row landed; a swallowed insert is what left the parity alert stuck.
_P5_CHECK="SELECT count() FROM default.pipeline_health WHERE source = 'parity' AND metric = 'result' AND detail LIKE '%${WINDOW_END}%'"
if ! _P5_COUNT="$(ch_http "$_P5_CHECK")"; then
  echo "[$(date -u)] parity: pipeline_health verification query failed" >&2
  exit 1
fi
_P5_COUNT="$(printf '%s' "$_P5_COUNT" | tr -d '[:space:]')"
if [ "${_P5_COUNT:-0}" -lt 1 ]; then
  echo "[$(date -u)] parity: pipeline_health row not found (count=${_P5_COUNT:-0})" >&2
  exit 1
fi

if [ "$status" != "ok" ]; then
  exit 1
fi
