#!/usr/bin/env bash
# Phase 4B — hourly ClickHouse `default` DB backup to the local `backups`
# disk, lifted to GCS (wikistream-505003-backups), local kept last 2.
# Targets the Debian bookworm VM (GNU date). The BACKUP direction runs as the
# wikistream user (GRANT BACKUP ON *.* is granted by the orchestrator); the
# one-time RESTORE spot-check (4.2.8) deliberately runs as the container's
# default user, since wikistream has ON default.* only.
#
# Depends on: docker (container $CLICKHOUSE_CONTAINER), gcloud storage.
# CLICKHOUSE_PASSWORD is required (sourced from /opt/wikistream/.env when
# present, else the environment). CH_DATA_DIR is the host path of the ch-data
# bind mount (the backups disk lives at $CH_DATA_DIR/backups/).
#
# Ordering is deliberate: the GCS lift must succeed BEFORE local prune, so a
# failed lift aborts (set -e) and the local backup survives for the next hour.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f /opt/wikistream/.env ]; then
  # Extract only the secret — sourcing a rotated .env as shell code would let
  # a value containing quotes/semicolons mangle the variable (compose parses
  # the same file safely; shell source does not).
  CLICKHOUSE_PASSWORD="$(grep -E '^CLICKHOUSE_PASSWORD=' /opt/wikistream/.env | tail -n1 | cut -d= -f2-)"
fi

if [ -z "${CLICKHOUSE_PASSWORD:-}" ]; then
  echo "[backup] CLICKHOUSE_PASSWORD is not set (source /opt/wikistream/.env or export it)" >&2
  exit 1
fi

: "${CLICKHOUSE_CONTAINER:=wikistream-clickhouse}"
: "${CH_DATA_DIR:=/mnt/ch-data}"
: "${BACKUP_BUCKET:=gs://wikistream-505003-backups}"

NAME="backup-$(date -u +%Y%m%d-%H%M%S)"

# Password via stdin (`--password` with no value): keeps it out of the docker
# CLI argv, where any OS-login user could read it via ps.
BACKUP_OUT="$(printf '%s\n' "$CLICKHOUSE_PASSWORD" | docker exec -i "$CLICKHOUSE_CONTAINER" clickhouse-client \
  --user wikistream --password --query "BACKUP DATABASE default TO Disk('backups','$NAME')")"
case "$BACKUP_OUT" in
  *BACKUP_CREATED*) ;;
  *) echo "[backup] FAILED name=$NAME output=$BACKUP_OUT" >&2; exit 1 ;;
esac
echo "[backup] created $BACKUP_OUT"

gcloud storage cp -r "${CH_DATA_DIR}/backups/$NAME" "$BACKUP_BUCKET/$NAME" >/dev/null
echo "[backup] lifted $CH_DATA_DIR/backups/$NAME -> $BACKUP_BUCKET/$NAME"

# Prune local: keep the last 2 (names are UTC-sortable, so sort = chronological).
# `|| true` so a glob miss (backups dir wiped) can't fail the script after a
# successful lift under set -o pipefail.
ls -1d "$CH_DATA_DIR"/backups/backup-* 2>/dev/null | sort | head -n -2 | while read -r old; do
  rm -rf "$old"
  echo "[backup] pruned $(basename "$old")"
done || true

echo "[backup] completed name=$NAME status=ok"