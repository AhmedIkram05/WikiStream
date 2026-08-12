#!/usr/bin/env bash
# migrations/apply.sh — idempotent SQL migration runner for WikiStream.
# Speaks to the ClickHouse HTTP API (port 8123) via curl — no
# clickhouse-client binary — so it runs identically on the VM host, CI
# runner, and laptop.
#
# Env:
#   CH_HOST         default localhost
#   CH_PORT         default 8123
#   CH_USER         default wikistream
#   CH_PASSWORD     REQUIRED
#   MIGRATIONS_DIR  default = this script's own directory
set -euo pipefail

CH_HOST="${CH_HOST:-localhost}"
CH_PORT="${CH_PORT:-8123}"
CH_USER="${CH_USER:-wikistream}"
MIGRATIONS_DIR="${MIGRATIONS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

if [ -z "${CH_PASSWORD:-}" ]; then
  echo "error: CH_PASSWORD is required (set it before running apply.sh)" >&2
  exit 1
fi

CH_URL="http://${CH_HOST}:${CH_PORT}/"

# Run a one-shot query through the CH HTTP API; the request body IS the
# query. --fail-with-body makes any non-2xx (incl. 516 auth errors) exit
# non-zero and print the response body; header auth keeps the password out
# of the URL and argv.
ch_query() {
  curl -sS --fail-with-body \
    -H "X-ClickHouse-User: ${CH_USER}" \
    -H "X-ClickHouse-Key: ${CH_PASSWORD}" \
    -X POST --data-binary "$1" \
    "${CH_URL}"
}

# --- Step 1: readiness wait --------------------------------------------------
# Same header auth + --fail-with-body as the apply step: an auth error is
# "not ready yet" (the wikistream user may not exist on first boot before
# bootstrap), not a hard failure. Fails non-zero if never ready.
ready=0
for _ in $(seq 1 30); do
  if ch_query "SELECT 1" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [ "${ready}" -ne 1 ]; then
  echo "error: ClickHouse not ready at ${CH_HOST}:${CH_PORT} after 30 attempts (~60s) as user ${CH_USER}" >&2
  exit 1
fi

# --- Step 2: bookkeeping table -----------------------------------------------
ch_query "CREATE TABLE IF NOT EXISTS default.schema_migrations
(
    version    String,
    status     String,
    applied_at DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY version" >/dev/null

# --- Step 3: apply loop ------------------------------------------------------
applied=0
skipped=0
shopt -s nullglob
for file in "${MIGRATIONS_DIR}"/[0-9]*.sql; do
  version="$(basename "${file}" .sql)"

  # Already recorded? A row means done regardless of status.
  recorded="$(ch_query "SELECT version FROM default.schema_migrations WHERE version = '${version}' FORMAT TSV")"
  if [ -n "${recorded}" ]; then
    echo "SKIP ${version} (recorded)"
    skipped=$((skipped + 1))
    continue
  fi

  # Optional guard: first line `-- guard: <expr>`; apply iff SELECT <expr>
  # evaluates to 1. A guard-0 file is RECORDED as 'skipped', never left out.
  status="applied"
  first_line="$(head -n 1 "${file}")"
  if [[ "${first_line}" == "-- guard: "* ]]; then
    guard_expr="${first_line#"-- guard: "}"
    guard_result="$(ch_query "SELECT ${guard_expr} FORMAT TSV")"
    guard_result="${guard_result//[[:space:]]/}"
    if [ "${guard_result}" != "1" ]; then
      status="skipped"
    fi
  fi

  if [ "${status}" = "applied" ]; then
    # Apply: the request body IS the query (no /?query=).
    if ! apply_output="$(curl -sS --fail-with-body \
        -H "X-ClickHouse-User: ${CH_USER}" \
        -H "X-ClickHouse-Key: ${CH_PASSWORD}" \
        -X POST --data-binary "@${file}" \
        "${CH_URL}" 2>&1)"; then
      echo "error: failed to apply ${version}:" >&2
      printf '%s\n' "${apply_output:-}" >&2
      exit 1
    fi
    echo "APPLY ${version}"
  else
    echo "SKIP ${version} (guard 0)"
  fi

  # Record EVERY file after evaluation. A failed apply never reaches this
  # line, so a re-run is retry-safe.
  ch_query "INSERT INTO default.schema_migrations (version, status) VALUES ('${version}', '${status}')" >/dev/null
  if [ "${status}" = "applied" ]; then
    applied=$((applied + 1))
  else
    skipped=$((skipped + 1))
  fi
done

# --- Step 4: summary ----------------------------------------------------------
echo "migrations complete: ${applied} applied, ${skipped} skipped"
exit 0