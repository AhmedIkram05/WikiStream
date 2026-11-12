#!/usr/bin/env bash
# Live BigQuery idempotency gate for AUD-02.
#
# Creates a throwaway dataset, loads the same key twice with --replace, runs the
# real merge/reload SQL, and asserts the target stays at one row. This is the
# only check that exercises the service that originally failed with
# "UPDATE/MERGE must match at most one source row for each target row".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

: "${BQ_TEST_PROJECT:?set BQ_TEST_PROJECT to a throwaway GCP project}"
DATASET="${BQ_TEST_DATASET:-wikistream_ci_$(date -u +%Y%m%d%H%M%S)_$$}"
TMP_DIR="$(mktemp -d)"
trap 'bq --project_id="$BQ_TEST_PROJECT" rm -r -f -d "$DATASET" >/dev/null 2>&1 || true; rm -rf "$TMP_DIR"' EXIT

bq --project_id="$BQ_TEST_PROJECT" mk --dataset --location=US "$DATASET" >/dev/null
bq --project_id="$BQ_TEST_PROJECT" mk --table "$DATASET.kpi_edits_hourly" schemas/kpi_edits_hourly.json >/dev/null
bq --project_id="$BQ_TEST_PROJECT" mk --table "$DATASET.kpi_edits_hourly_staging" schemas/kpi_edits_hourly.json >/dev/null
bq --project_id="$BQ_TEST_PROJECT" mk --table "$DATASET.raw_events_sample" schemas/raw_events_sample.json >/dev/null
bq --project_id="$BQ_TEST_PROJECT" mk --table "$DATASET.raw_events_sample_staging" schemas/raw_events_sample.json >/dev/null

load_kpi() {
  cat > "$TMP_DIR/kpi.json" <<EOF
{"hour":"2026-01-01T00:00:00Z","wiki":"enwiki","is_bot":false,"edits":$1,"bytes_delta":10}
EOF
  bq --project_id="$BQ_TEST_PROJECT" load --replace --source_format=NEWLINE_DELIMITED_JSON \
    "$DATASET.kpi_edits_hourly_staging" "$TMP_DIR/kpi.json" schemas/kpi_edits_hourly.json >/dev/null
}

run_merge() {
  sed -e 's/{START}/2026-01-01 00:00:00/g' \
    -e 's/{END}/2026-01-01 01:00:00/g' \
    -e "s/wikistream\\./$DATASET./g" sql/merge_edits.sql |
    bq --project_id="$BQ_TEST_PROJECT" query --use_legacy_sql=false --format=none
}

assert_kpi() {
  local actual
  actual="$(bq --project_id="$BQ_TEST_PROJECT" query --use_legacy_sql=false --format=json \
    "SELECT COUNT(*) AS c, ANY_VALUE(edits) AS edits FROM \`$DATASET.kpi_edits_hourly\` WHERE hour = TIMESTAMP('2026-01-01 00:00:00')" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)[0])')"
  python3 - "$actual" "$1" "$2" <<'PY'
import ast, sys
row = ast.literal_eval(sys.argv[1])
assert row["c"] == int(sys.argv[2]), row
assert row["edits"] == int(sys.argv[3]), row
PY
}

load_kpi 2
run_merge
assert_kpi 1 2

# Second overlapping run: --replace gives the staging table exactly one source
# row, so the MERGE upserts instead of matching one target with two sources.
load_kpi 3
run_merge
assert_kpi 1 3

cat > "$TMP_DIR/raw.json" <<'EOF'
{"inserted_at":"2026-01-01T00:00:00Z","event":"{}","wiki":"enwiki","title":"T","user":"U","is_bot":false,"event_type":"edit"}
EOF
load_raw() {
  bq --project_id="$BQ_TEST_PROJECT" load --replace --source_format=NEWLINE_DELIMITED_JSON \
    "$DATASET.raw_events_sample_staging" "$TMP_DIR/raw.json" schemas/raw_events_sample.json >/dev/null
}
run_reload() {
  sed -e 's/{START}/2026-01-01 00:00:00/g' \
    -e 's/{END}/2026-01-01 01:00:00/g' \
    -e "s/wikistream\\./$DATASET./g" sql/reload_raw_sample.sql |
    bq --project_id="$BQ_TEST_PROJECT" query --use_legacy_sql=false --format=none
}
count_raw() {
  bq --project_id="$BQ_TEST_PROJECT" query --use_legacy_sql=false --format=json \
    "SELECT COUNT(*) AS c FROM \`$DATASET.raw_events_sample\` WHERE inserted_at = TIMESTAMP('2026-01-01 00:00:00')" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["c"])'
}

load_raw
run_reload
load_raw
run_reload
[ "$(count_raw)" = "1" ] || { echo "raw sample duplicated" >&2; exit 1; }

echo "BigQuery idempotency ok: kpi upsert + raw window reload each keep one row"
