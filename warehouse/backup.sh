#!/usr/bin/env bash
# Phase 4B — hourly ClickHouse `default` DB backup to the local `backups`
# disk, lifted to GCS (wikistream-505003-backups), local kept last 2.
# Targets the Debian bookworm VM (GNU date). The BACKUP direction runs as the
# wikistream user (GRANT BACKUP ON *.* is granted by the orchestrator); the
# one-time RESTORE spot-check (4.2.8) deliberately runs as the container's
# default user, since wikistream has ON default.* only.
#
# Depends on: docker (container $CLICKHOUSE_CONTAINER), gcloud storage.
# CLICKHOUSE_PASSWORD is required; CH_DATA_DIR is the host path of the ch-data
# bind mount (the backups disk lives at $CH_DATA_DIR/backups/). Both are taken
# from /opt/wikistream/.env when present (else the environment), matching the
# compose interpolation so the lift/prune target the real bind path.
#
# Ordering is deliberate: the GCS lift must succeed BEFORE the final local
# prune, so a failed lift aborts (set -e) and the local backup survives for
# the next hour. A separate generous guard prune runs BEFORE the snapshot is
# created so a failed BACKUP or lift can never wedge the disk (the hourly
# 'No space left on device' cascade seen 2026-08-14 05:20/06:20).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f /opt/wikistream/.env ]; then
  # Extract only what we need — sourcing a rotated .env as shell code would let
  # a value containing quotes/semicolons mangle the variables (compose parses
  # the same file safely; shell source does not).
  CLICKHOUSE_PASSWORD="$(grep -E '^CLICKHOUSE_PASSWORD=' /opt/wikistream/.env | tail -n1 | cut -d= -f2-)"
  CH_DATA_DIR="${CH_DATA_DIR:-$(grep -E '^CH_DATA_DIR=' /opt/wikistream/.env | tail -n1 | cut -d= -f2-)}"
fi

if [ -z "${CLICKHOUSE_PASSWORD:-}" ]; then
  echo "[backup] CLICKHOUSE_PASSWORD is not set (source /opt/wikistream/.env or export it)" >&2
  exit 1
fi

: "${CLICKHOUSE_CONTAINER:=wikistream-clickhouse}"
: "${CH_DATA_DIR:=/mnt/ch-data}"
: "${BACKUP_BUCKET:=gs://wikistream-505003-backups}"

NAME="backup-$(date -u +%Y%m%d-%H%M%S)"

# Guard prune BEFORE creating a new snapshot: if the BACKUP query or the lift
# fails, set -e aborts before the post-lift prune below, so without this a
# failing backup would accumulate forever and wedge the disk. Keep the last 4
# (the post-lift prune then tightens to 2 after a successful lift). `|| true`
# so a glob miss (backups dir wiped) can't fail the script.
ls -1d "$CH_DATA_DIR"/backups/backup-* 2>/dev/null | sort | head -n -4 | while read -r old; do
  rm -rf "$old"
  echo "[backup] guard-pruned $(basename "$old")"
done || true

# Password via stdin (`--password` with no value): keeps it out of the docker
# CLI argv, where any OS-login user could read it via ps. --receive_timeout
# raises the client socket timeout above the 300s default: the BACKUP query
# streams multi-GB and hit 'Timeout exceeded ... Waited for 300 seconds' under
# disk/IO pressure (2026-08-14), leaving partial snapshots.
BACKUP_OUT="$(printf '%s\n' "$CLICKHOUSE_PASSWORD" | docker exec -i "$CLICKHOUSE_CONTAINER" clickhouse-client \
  --user wikistream --password --receive_timeout 1800 --query "BACKUP DATABASE default TO Disk('backups','$NAME')")"
case "$BACKUP_OUT" in
  *BACKUP_CREATED*) ;;
  *) echo "[backup] FAILED name=$NAME output=$BACKUP_OUT" >&2; exit 1 ;;
esac
echo "[backup] created $BACKUP_OUT"

# Lift via gsutil in serial mode (parallel_composite_upload_threshold=0):
# gcloud storage cp stages temp parts under gcloud/tmp/... and its cleanup
# pass deletes them, which needs storage.objects.delete — the VM SA only has
# objectCreator (single PUTs). gsutil serial mode creates no temp objects.
gsutil -q -o GSUtil:parallel_composite_upload_threshold=0 cp -r \
  "${CH_DATA_DIR}/backups/$NAME" "$BACKUP_BUCKET/$NAME"
echo "[backup] lifted $CH_DATA_DIR/backups/$NAME -> $BACKUP_BUCKET/$NAME"

# Prune local: keep the last 2 (names are UTC-sortable, so sort = chronological).
# `|| true` so a glob miss (backups dir wiped) can't fail the script after a
# successful lift under set -o pipefail.
ls -1d "$CH_DATA_DIR"/backups/backup-* 2>/dev/null | sort | head -n -2 | while read -r old; do
  rm -rf "$old"
  echo "[backup] pruned $(basename "$old")"
done || true

echo "[backup] completed name=$NAME status=ok"
