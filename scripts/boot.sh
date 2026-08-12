#!/usr/bin/env bash
# Phase 3A boot shim — runs on the VM at boot (paths are the VM layout).
# This is the only place the VM starts containers after startup.sh: startup.sh
# is NEVER edited again after Phase 3A (metadata_startup_script is ForceNew);
# Phase 3C's hourly BigQuery-export systemd units install HERE (3.3.5).
#
# Depends on CH_PASSWORD from the environment — startup.sh section 4 fetches it
# from Secret Manager; it must be exported there for boot.sh to see it.
# No secret is hardcoded in this file.
set -euo pipefail

cd /opt/wikistream

# Non-empty guard: an unset/empty CH_PASSWORD must fail loudly, not bootstrap a
# user with an empty password that breaks the migration step below.
: "${CH_PASSWORD:?boot.sh needs CH_PASSWORD in the environment (startup.sh section 4 must export it)}"

# 1. Wait for ClickHouse to accept connections. First boot needs ~10-20s (no
#    first-boot init scripts anymore). 30 attempts x 2s.
for i in $(seq 1 30); do
  if docker compose exec -T clickhouse clickhouse-client --query "SELECT 1" >/dev/null 2>&1; then
    echo "[boot] clickhouse ready (attempt ${i}/30)"
    break
  fi
  echo "[boot] waiting for clickhouse (attempt ${i}/30)"
  sleep 2
done

# If the loop never succeeded this probe fails and set -e exits 1 — startup.sh
# calls boot.sh without || true, so a dead ClickHouse fails startup loudly.
docker compose exec -T clickhouse clickhouse-client --query "SELECT 1" >/dev/null

# 2. User bootstrap. The SQL is built in a VARIABLE first so ${CH_PASSWORD}
#    expands (a quoted heredoc would pipe the literal text). The trailing
#    ALTER USER runs on EVERY boot — the rotation-gap fix: the old first-boot
#    user setup only ever ran on empty volumes, so a rotated password was
#    never re-applied. Default user is localhost-only, hence the docker exec
#    path (host curl is for migrations, where the wikistream user hits HTTP).
BOOTSTRAP_SQL="CREATE USER IF NOT EXISTS wikistream IDENTIFIED WITH plaintext_password BY '${CH_PASSWORD}' HOST ANY;
GRANT SELECT, INSERT, CREATE, ALTER, DROP, TRUNCATE, OPTIMIZE ON default.* TO wikistream;
ALTER USER IF EXISTS wikistream IDENTIFIED WITH plaintext_password BY '${CH_PASSWORD}' HOST ANY;"

# Log the SQL with the password redacted (/var/log/wikistream-startup.log is
# world-readable); the client's stdout for CREATE/GRANT/ALTER is empty, so the
# statement text here is what AC7 greps.
echo "user bootstrap: $(echo "$BOOTSTRAP_SQL" | sed "s/${CH_PASSWORD}/<redacted>/g")"
echo "$BOOTSTRAP_SQL" | docker compose exec -T clickhouse clickhouse-client --multiquery
echo "user bootstrap ok"

# 3. Migrations over the HTTP API as the bootstrap user (per-file APPLY/SKIP
#    lines; any non-zero exit aborts boot.sh via set -e).
CH_HOST=localhost CH_PORT=8123 CH_USER=wikistream CH_PASSWORD=${CH_PASSWORD} MIGRATIONS_DIR=/opt/wikistream/migrations bash /opt/wikistream/migrations/apply.sh