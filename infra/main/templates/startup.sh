#!/usr/bin/env bash
set -euo pipefail
umask 077                              # .env stays 600
exec > >(tee -a /var/log/wikistream-startup.log) 2>&1  # log file AND serial console
echo "[$(date -u)] startup begin"

# 0. Cloud SDK + git — NOT on the ubuntu-2404-lts base image. google-cloud-cli
#    ships in Google's apt repo, not Ubuntu's, so add that repo first (official
#    recipe from cloud.google.com/sdk/docs/install). Verified on 24.04 at build.
if ! command -v gcloud >/dev/null; then
  apt-get update
  apt-get install -y apt-transport-https ca-certificates gnupg curl
  curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" > /etc/apt/sources.list.d/google-cloud-sdk.list
  apt-get update
  apt-get install -y google-cloud-cli
fi
command -v git >/dev/null || apt-get install -y git

# 1. Docker + compose plugin (idempotent). Ubuntu packages compose v2 as
#    docker-compose-v2 (docker-compose-plugin only exists in Docker's repo).
command -v docker >/dev/null || { apt-get update && apt-get install -y docker.io docker-compose-v2; }
systemctl enable --now docker

# 2. Repo = single source of truth; pull-or-clone so a GitHub outage during a
#    reset can't take the stack down.
if [ -d /opt/wikistream/.git ]; then
  git -C /opt/wikistream pull --ff-only || true
else
  git clone --depth 1 https://github.com/AhmedIkram05/WikiStream /opt/wikistream
fi
cd /opt/wikistream

# 2b. Containers read bind mounts as unprivileged users (grafana uid 472), but
#     umask 077 leaves the clone 700/600 root — grafana provisioning dies with
#     "permission denied". Open the tree (r+X) BEFORE rendering secrets below
#     so .env still lands 600. Found 2026-08-11 on first prod boot.
chmod -R a+rX /opt/wikistream

# 3. Project id from the metadata server — deterministic, no gcloud config
GCP_PROJECT=$(curl -fsS -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/project/project-id)

# 4. Secrets from Secret Manager (VM SA has scoped secretAccessor).
#    CH_PASSWORD must be EXPORTED: scripts/boot.sh runs as a child process
#    (bash /opt/wikistream/scripts/boot.sh) and only exported vars reach it.
export CH_PASSWORD=$(gcloud secrets versions access latest --secret=clickhouse-password)
GF_PASSWORD=$(gcloud secrets versions access latest --secret=grafana-admin-password)

# 5. Durable ch-data disk (Phase 3A): data survives every startup.sh-driven
#    instance recreate (metadata_startup_script is ForceNew). Idempotent —
#    an existing fs, fstab entry, or mount is left alone. "nofail" keeps boot
#    working if the disk is ever detached.
DEV=/dev/disk/by-id/google-ch-data
if [ -b "$DEV" ]; then
  if ! blkid "$DEV" >/dev/null 2>&1; then
    mkfs.ext4 "$DEV"
  fi
  UUID=$(blkid -s UUID -o value "$DEV")
  grep -q "$UUID" /etc/fstab || echo "UUID=$UUID /mnt/ch-data ext4 defaults,nofail 0 2" >> /etc/fstab
  mountpoint -q /mnt/ch-data || mount /mnt/ch-data
  mkdir -p /mnt/ch-data/clickhouse
  export CH_DATA_DIR=/mnt/ch-data/clickhouse
fi

# 6. .env for compose (the Phase 1 ${VAR:-default} seams, now real)
cat > .env <<EOF
CLICKHOUSE_PASSWORD=${CH_PASSWORD}
GF_SECURITY_ADMIN_PASSWORD=${GF_PASSWORD}
CONSUMER_IMAGE=us-central1-docker.pkg.dev/${GCP_PROJECT}/wikistream-consumer/consumer:latest
EOF
[ -n "${CH_DATA_DIR:-}" ] && echo "CH_DATA_DIR=${CH_DATA_DIR}" >> .env

# 7. Pull + start. --no-build: image comes from AR, never rebuilt on the VM.
#    Single container start: boot.sh below is the only other launch point.
gcloud auth configure-docker us-central1-docker.pkg.dev
docker compose pull
docker compose up -d --no-build

# 8. Boot shim: readiness, user bootstrap (rotation gap), migrations. This is
#    the VM's one-and-only container start; startup.sh must NEVER be edited
#    again after Phase 3A (3C's systemd export units land in scripts/boot.sh).
bash /opt/wikistream/scripts/boot.sh
echo "[$(date -u)] startup done"
