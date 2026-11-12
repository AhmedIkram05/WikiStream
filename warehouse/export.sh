#!/usr/bin/env bash
# Phase 3C — hourly ClickHouse → BigQuery export.
# Defaults (no args) to a TRAILING 2-HOUR window ending at the current top of
# the hour ("last two completed UTC hours"); pass START END (UTC
# 'YYYY-MM-DD HH:MM:SS') to backfill a specific window. Note: this script
# targets the Ubuntu VM — window math uses GNU date (-d '2 hours ago').
#
# Depends on: docker (container $CLICKHOUSE_CONTAINER), gcloud storage,
# bq. CLICKHOUSE_PASSWORD is required (read from /opt/wikistream/.env when
# present, else the environment).
#
# Idempotent per window: bq load --replace lands the window in <table>_staging
# (so an overlapping run cannot leave duplicate source keys behind), then a
# MERGE (kpi tables) or window DELETE+INSERT (raw sample, no natural key)
# converges the final table. Run at :00 via wikistream-export.timer.
# ponytail: staging is never vacuumed; the MERGE filters the window so leftover
# rows are harmless (same-key upsert), add expiry if staging cost matters.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="${WIKISTREAM_ENV_FILE:-/opt/wikistream/.env}"
if [ -f "$ENV_FILE" ]; then
  # Read only the keys we need. Sourcing .env would execute arbitrary shell from
  # a rotated file (and values with quotes/semicolons mangle the variables).
  CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-$(grep -E '^CLICKHOUSE_PASSWORD=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)}"
  GCP_PROJECT="${GCP_PROJECT:-$(grep -E '^GCP_PROJECT=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)}"
fi

if [ -z "${CLICKHOUSE_PASSWORD:-}" ]; then
  echo "[export] CLICKHOUSE_PASSWORD is not set (source /opt/wikistream/.env or export it)" >&2
  exit 1
fi

: "${CLICKHOUSE_CONTAINER:=wikistream-clickhouse}"
: "${BQ_DATASET:=wikistream}"
: "${STAGING_BUCKET:=gs://${GCP_PROJECT:-wikistream-505003}-bq-staging}"
: "${STAGING_TMP:=/tmp/wikistream-export}"

if [ $# -eq 2 ]; then
  START="$1"
  END="$2"
elif [ $# -eq 0 ]; then
  # Trailing 2-hour overlap: MERGE upserts make the re-exported hour a no-op
  # for keys it already has, while a late-arriving event (e.g. timestamped
  # 09:59, ingested 10:15) still lands inside the next run's window — the
  # hour-9 slice is re-imported and converged. window_end is unchanged, so
  # parity.sh's freshness gate (window_end match) keeps verifying this run.
  END="$(date -u +'%Y-%m-%d %H:00:00')"
  START="$(date -u +'%Y-%m-%d %H:00:00' -d '2 hours ago')"
else
  echo "[export] usage: $0 [START END]" >&2
  echo "[export]   START END = UTC 'YYYY-MM-DD HH:MM:SS' (inclusive-exclusive window); no args = trailing 2 completed UTC hours" >&2
  exit 1
fi

WINDOW_START="$(printf '%sZ' "${START/ /T}")"
WINDOW_END="$(printf '%sZ' "${END/ /T}")"
STAMP="$(date -u +%Y%m%d%H)"
# Unique per run: staging objects are consumed by bq load immediately and the
# VM SA only has objectCreator/objectViewer on the bucket (ADR-010) — it cannot
# overwrite an existing object, so re-running the same hour needs a fresh name.
RUN_ID="$(date -u +%H%M%S)"

# Run a committed merge_*.sql for the window (same {START}/{END} substitution
# as the extract; wikistream. -> $BQ_DATASET so overrides keep working).
merge_window() {
  bq --quiet query --use_legacy_sql=false \
    "$(sed -e "s/{START}/$START/" -e "s/{END}/$END/" -e "s/wikistream\\./$BQ_DATASET./g" "sql/$1")"
}

# key | export SQL | final table | partition field | staging table | merge SQL
TABLES=(
  # key|sqlfile|bq_table|partition_field|staging_table|merge_sql
  "kpi_edits|export_edits.sql|kpi_edits_hourly|hour|kpi_edits_hourly_staging|merge_edits.sql"
  "kpi_top_pages|export_top_pages.sql|kpi_top_pages_hourly|hour|kpi_top_pages_hourly_staging|merge_top_pages.sql"
  "kpi_sizes|export_sizes.sql|kpi_edit_sizes_hourly|hour|kpi_edit_sizes_hourly_staging|merge_sizes.sql"
  "raw_sample|export_raw_sample.sql|raw_events_sample|inserted_at|raw_events_sample_staging|reload_raw_sample.sql"
)
rows_kpi_edits=0
rows_kpi_top_pages=0
rows_kpi_sizes=0
rows_raw_sample=0

# The extracted JSONL is large (raw sample is 10% of the window); remove the
# current object on any exit so a failed load cannot leave it on the boot disk.
object=""
trap 'rm -f "${object:-}"' EXIT

for entry in "${TABLES[@]}"; do
  IFS='|' read -r key sqlfile btable partfield stable mergesql <<<"$entry"
  mkdir -p "$STAGING_TMP/$key"
  object="$STAGING_TMP/$key/${STAMP}-${RUN_ID}.jsonl"

  # Password via stdin (`--password` with no value, like backup.sh): keeps it
  # out of the docker CLI argv, where any OS-login user could read it via ps.
  printf '%s\n' "$CLICKHOUSE_PASSWORD" | docker exec -i "$CLICKHOUSE_CONTAINER" clickhouse-client \
    --user wikistream --password --format JSONEachRow \
    --query "$(sed "s/{START}/$START/; s/{END}/$END/" "sql/$sqlfile")" > "$object"

  rows="$(wc -l < "$object" | tr -d ' ')"
  if [ -s "$object" ]; then
    # --replace, not append: the trailing 2-hour window overlaps the previous
    # run, so appending to persistent staging gives MERGE two source rows per
    # key and BigQuery aborts. export_runs stays append-only below.
    bq load --replace --source_format=NEWLINE_DELIMITED_JSON --time_partitioning_field="$partfield" \
      --schema="schemas/$btable.json" "$BQ_DATASET.$stable" "$object"
    merge_window "$mergesql"
    # Backup copy is non-critical; never let a GCS hiccup block the load/merge.
    gcloud storage cp "$object" "$STAGING_BUCKET/$key/$(basename "$object")" >/dev/null \
      || echo "[export] backup copy failed key=$key" >&2
  fi
  rm -f "$object"

  case "$key" in
    kpi_edits)     rows_kpi_edits=$rows ;;
    kpi_top_pages) rows_kpi_top_pages=$rows ;;
    kpi_sizes)     rows_kpi_sizes=$rows ;;
    raw_sample)    rows_raw_sample=$rows ;;
  esac
done

# One run record per export; parity.sh's freshness gate reads this table.
mkdir -p "$STAGING_TMP/export_runs"
runs_file="$STAGING_TMP/export_runs/$STAMP.jsonl"
python3 - "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$WINDOW_START" "$WINDOW_END" \
  "$rows_kpi_edits" "$rows_kpi_top_pages" "$rows_kpi_sizes" "$rows_raw_sample" <<'PY' > "$runs_file"
import json, sys
exported_at, ws, we, r_edits, r_top, r_sizes, r_raw = sys.argv[1:8]
print(json.dumps({"exported_at": exported_at, "window_start": ws, "window_end": we,
                  "status": "success", "rows_edits": int(r_edits), "rows_top_pages": int(r_top),
                  "rows_sizes": int(r_sizes), "rows_raw_sample": int(r_raw)},
                 sort_keys=True))
PY
bq load --source_format=NEWLINE_DELIMITED_JSON --time_partitioning_field=exported_at \
  --schema="schemas/export_runs.json" "$BQ_DATASET.export_runs" "$runs_file"

echo "[export] success window=$WINDOW_START..$WINDOW_END rows_edits=$rows_kpi_edits rows_top_pages=$rows_kpi_top_pages rows_sizes=$rows_kpi_sizes rows_raw_sample=$rows_raw_sample"
