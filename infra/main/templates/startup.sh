#!/usr/bin/env bash
set -euo pipefail
umask 077                              # .env + 001-init.sql stay 600
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
#    reset can't take the stack down. 001-init.sql is tracked but rendered with
#    the real secret below, so restore it before pulling or ff-only wedges on
#    any commit touching it (it gets re-rendered right after anyway).
if [ -d /opt/wikistream/.git ]; then
  git -C /opt/wikistream checkout -- docker/clickhouse/initdb.d/001-init.sql 2>/dev/null || true
  git -C /opt/wikistream pull --ff-only || true
else
  git clone --depth 1 https://github.com/AhmedIkram05/WikiStream /opt/wikistream
fi
cd /opt/wikistream

# 3. Project id from the metadata server — deterministic, no gcloud config
GCP_PROJECT=$(curl -fsS -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/project/project-id)

# 4. Secrets from Secret Manager (VM SA has scoped secretAccessor)
CH_PASSWORD=$(gcloud secrets versions access latest --secret=clickhouse-password)
GF_PASSWORD=$(gcloud secrets versions access latest --secret=grafana-admin-password)

# 5. Render initdb.d (Q9): spike-verified recipe, real password
mkdir -p docker/clickhouse/initdb.d
cat > docker/clickhouse/initdb.d/001-init.sql <<EOF
CREATE USER IF NOT EXISTS wikistream IDENTIFIED WITH plaintext_password BY '${CH_PASSWORD}' HOST ANY;
GRANT SELECT, INSERT, CREATE, ALTER, DROP, TRUNCATE, OPTIMIZE ON default.* TO wikistream;
CREATE TABLE IF NOT EXISTS default.raw_events (
    inserted_at DateTime64(3, 'UTC'),
    event String
) ENGINE = MergeTree
ORDER BY inserted_at;
EOF

# 6. .env for compose (the Phase 1 ${VAR:-default} seams, now real)
cat > .env <<EOF
CLICKHOUSE_PASSWORD=${CH_PASSWORD}
GF_SECURITY_ADMIN_PASSWORD=${GF_PASSWORD}
CONSUMER_IMAGE=us-central1-docker.pkg.dev/${GCP_PROJECT}/wikistream-consumer/consumer:latest
EOF

# 7. Pull + start. --no-build: image comes from AR, never rebuilt on the VM.
gcloud auth configure-docker us-central1-docker.pkg.dev
docker compose pull
docker compose up -d --no-build
echo "[$(date -u)] startup done"
