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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Dynamic path; the helper is also checked as its own file.
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/secret_helpers.sh"

# Non-empty guards: an unset/empty password must fail loudly, not bootstrap a
# user with an empty password that breaks the migration step below.
: "${CH_PASSWORD:?boot.sh needs CH_PASSWORD in the environment (startup.sh section 4 must export it)}"
: "${CH_RO_PASSWORD:?boot.sh needs CH_RO_PASSWORD in the environment (startup.sh section 4 must export it)}"

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

# 2. User bootstrap. The SQL is built in a VARIABLE first so the passwords
#    expand (a quoted heredoc would pipe the literal text). The trailing ALTER
#    USER runs on EVERY boot — the rotation-gap fix: the old first-boot user
#    setup only ever ran on empty volumes, so a rotated password was never
#    re-applied. Default user is localhost-only, hence the docker exec path
#    (host curl is for migrations, where the wikistream user hits HTTP).
#
#    The write user is scoped to default.*, and the REVOKE removes the old
#    global grant from existing installs (GRANT alone does not shrink it).
#    Grafana gets a separate read-only user/password so a panel compromise
#    cannot DDL/DROP warehouse tables.
CH_PASSWORD_SQL="$(sql_escape "$CH_PASSWORD")"
CH_RO_PASSWORD_SQL="$(sql_escape "$CH_RO_PASSWORD")"
BOOTSTRAP_SQL="CREATE USER IF NOT EXISTS wikistream IDENTIFIED WITH plaintext_password BY '${CH_PASSWORD_SQL}' HOST ANY;
ALTER USER IF EXISTS wikistream IDENTIFIED WITH plaintext_password BY '${CH_PASSWORD_SQL}' HOST ANY;
REVOKE ALL PRIVILEGES ON *.* FROM wikistream;
GRANT SELECT, INSERT, CREATE, ALTER, DROP, TRUNCATE, OPTIMIZE, BACKUP ON default.* TO wikistream;
-- Read-only metadata for migration checks; still no access to other databases.
GRANT SELECT ON system.tables TO wikistream;
GRANT SELECT ON system.columns TO wikistream;
GRANT SELECT ON system.databases TO wikistream;
CREATE USER IF NOT EXISTS wikistream_ro IDENTIFIED WITH plaintext_password BY '${CH_RO_PASSWORD_SQL}' HOST ANY;
ALTER USER IF EXISTS wikistream_ro IDENTIFIED WITH plaintext_password BY '${CH_RO_PASSWORD_SQL}' HOST ANY;
REVOKE ALL PRIVILEGES ON *.* FROM wikistream_ro;
GRANT SELECT ON default.* TO wikistream_ro;
GRANT SELECT ON system.tables TO wikistream_ro;
GRANT SELECT ON system.columns TO wikistream_ro;
GRANT SELECT ON system.databases TO wikistream_ro;"

# Log the SQL with both passwords redacted; the client's stdout for
# CREATE/GRANT/ALTER is empty, so the statement text here is what AC7 greps.
# Redact raw AND SQL-escaped forms: the statement carries the escaped values, so
# a password containing ' or \ would otherwise slip past a raw-only match.
echo "user bootstrap: $(redact_all "$BOOTSTRAP_SQL" "$CH_PASSWORD" "$CH_RO_PASSWORD" "$CH_PASSWORD_SQL" "$CH_RO_PASSWORD_SQL")"
echo "$BOOTSTRAP_SQL" | docker compose exec -T clickhouse clickhouse-client --multiquery
echo "user bootstrap ok"

# 3. Migrations over the HTTP API as the bootstrap user (per-file APPLY/SKIP
#    lines; any non-zero exit aborts boot.sh via set -e).
CH_HOST=localhost CH_PORT=8123 CH_USER=wikistream CH_PASSWORD=${CH_PASSWORD} MIGRATIONS_DIR=/opt/wikistream/migrations bash /opt/wikistream/migrations/apply.sh

# Seed the first data-quality verdict after migrations. The boot-time `up -d`
# run starts before the CH user exists; without this a fresh deploy has no
# `data_quality` row until the :30 timer and noDataState: Alerting can page.
# A failed seed is non-fatal; the timer and OnFailure cover it.
docker compose run --rm -T data_quality || true

# Install BigQuery export + parity + backup + data-quality systemd units/timers,
# plus the OnFailure page-template (batch failures Slack instantly, incl.
# data-quality crashes). The gx unit cleanup is idempotent and keeps an
# in-place VM from running the retired gate alongside its replacement.
systemctl disable --now wikistream-gx.timer 2>/dev/null || true
rm -f /etc/systemd/system/wikistream-gx.service /etc/systemd/system/wikistream-gx.timer
cp /opt/wikistream/warehouse/wikistream-export.service /opt/wikistream/warehouse/wikistream-export.timer \
  /opt/wikistream/warehouse/wikistream-parity.service /opt/wikistream/warehouse/wikistream-parity.timer \
  /opt/wikistream/warehouse/wikistream-backup.service /opt/wikistream/warehouse/wikistream-backup.timer \
  /opt/wikistream/consumer/wikistream-data-quality.service /opt/wikistream/consumer/wikistream-data-quality.timer \
  /opt/wikistream/warehouse/wikistream-fail-notify@.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wikistream-export.timer wikistream-parity.timer wikistream-backup.timer wikistream-data-quality.timer

# Phase 5 (5B.1): Ops Agent — disk/memory metrics (agentless CM cannot see them).
# Non-fatal by design (boot.sh is set -e): a transient network failure must not
# abort the boot — the pipeline keeps running, disk alerting is delayed.
# Guard keyed on the unit, not a binary: the 2.x agent installs no
# google-ops-agent command (review-corrected 2026-08-14).
if ! systemctl is-active --quiet google-cloud-ops-agent.service; then
  if curl -fsSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh &&
    sudo bash add-google-cloud-ops-agent-repo.sh --also-install; then
    rm -f add-google-cloud-ops-agent-repo.sh
  else
    rm -f add-google-cloud-ops-agent-repo.sh
    echo "[boot] Ops Agent install failed — boot continues, no disk/VM-metrics alerting" >&2
  fi
fi

# Phase 5: Slack webhook for Grafana alerting (non-fatal if unavailable)
grep -q '^SLACK_WEBHOOK_URL=' /opt/wikistream/.env || {
  SLACK_WEBHOOK_URL=$(gcloud secrets versions access latest --secret=slack-webhook-url 2>/dev/null) &&
    echo "SLACK_WEBHOOK_URL=${SLACK_WEBHOOK_URL}" >>/opt/wikistream/.env ||
    echo "[boot] slack-webhook-url unavailable — alerting works, Slack delivery missing" >&2
}

# Phase 5: recreate grafana to pick up SLACK_WEBHOOK_URL + alerting provisioning
docker compose up -d grafana ||
  echo "[boot] grafana recreate failed — alerting provisioning may be stale" >&2
