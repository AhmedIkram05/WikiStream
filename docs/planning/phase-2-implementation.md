# Phase 2 Implementation Plan — GCP Deployment of the Skeleton

**Status:** LOCKED 2026-08-10. Decisions ratified in a grilling session with
Ahmed (questions Q1–Q8 below; Q9–Q10 executor-decided and flagged for review).
This document is the input for an agentic coding tool — every acceptance
criterion is self-checkable (pass/fail an agent can verify), per master plan §10.

**Reviewed 2026-08-10 by three parallel subagents (technical / logic /
impressiveness review) — 32 findings, all fixed.**

**Position in the hierarchy:** Master Plan §5 Phase 2 → this document. Nothing
here re-decides the ADR or master plan; it makes Phase 2 executable.

---

## 1. Objective

Move the proven Phase 1 skeleton onto real GCP infrastructure via Terraform,
and exercise the CI/CD approval-gate pattern for the first time. Deploy the
*same* skeleton — this phase proves the deployment path, not new functionality
(master plan Phase 2). From here on, GCP is the live environment.

## 2. Scope

| In | Deliberately out (later phase) |
| --- | --- |
| Bootstrap extension: WIF pool/provider + deploy SA (Q1) | Healthchecks, `restart` supervision, systemd unit (Phase 4) |
| Main Terraform config: network/compute/iam/storage modules per ADR-007 | Pydantic validation, dead-letter, restart-resume (Phase 4) |
| VM SA granted repo-scoped `artifactregistry.reader` (ADR-008); AR repo is bootstrap-owned (Q1) | Versioned migrations / full schema / MVs / TTL (Phase 3A) |
| Secret Manager: `clickhouse-password` + `grafana-admin-password`, auto-generated (Q3) | GX suite, backups, Cloud Monitoring / Ops Agent (Phases 4/5) |
| CI: `plan.yml` (PR) + `apply.yml` (merge, gated by `production` env, image build+push) | Full firewall lockdown *tests* — rejection probe (Phase 5) |
| VM startup script: Docker + git clone + secret fetch + compose up; reset as deploy path (Q2) | Grafana alert rules, IAM least-privilege review (Phase 5) |
| IP allowlist firewall rules for 3000/8123/22 (Q6) | Coverage gates (Phase 6) |
| OS Login (Q5); single 50GB disk (Q4); destroy-and-reapply cycle (Q8) | Burst test (Phase 7a) |

## 3. Locked decisions (from the grilling session)

| # | Decision |
| --- | --- |
| Q1 | **Bootstrap owns identity + the artifact repo.** `infra/bootstrap` (local state, one manual apply) is extended beyond the state bucket to also create: project API enablement, the WIF pool + GitHub provider, the `wikistream-deploy` SA, the SA↔pool binding, and the `wikistream-consumer` Artifact Registry repo. **Recorded deviation from ADR-007's "bootstrap = bucket only" wording** — identity has the same "can't bootstrap itself" property as the bucket, and the AR repo does too (review finding: `build-push` runs BEFORE the gated apply, so the repo must exist from the very first merge and survive destroy — same fix class as the bucket); CI works from the very first PR with zero further manual steps. Logged in implementation-log as a deviation. |
| Q2 | **VM provisioning = Terraform startup script; deploy = `gcloud compute instances reset`.** The script (in-repo template, `infra/main/templates/startup.sh`) installs Docker + Cloud SDK (`gcloud`) + git (none ship on `ubuntu-2404-lts` — review finding), clones the public repo (single source of truth for compose + grafana provisioning — no duplicated files), renders `001-init.sql` from the spike-verified recipe with the Secret Manager password (see Q9), writes `.env` from secrets, `gcloud auth configure-docker`, `docker compose pull && up -d --no-build`. Runs on every boot; project id resolved from the metadata server (deterministic, no gcloud-config dependency). The apply workflow's final step resets the VM → startup re-runs → new image live. No SSH from CI — the deploy SA has zero SSH rights. |
| Q3 | **Two secrets now, auto-generated:** `clickhouse-password` + `grafana-admin-password`, created by Terraform via `random_password` (`special = false` — alphanumeric avoids shell-escaping in the startup script; length 24). Zero plaintext in the repo or Terraform state; plaintext only on the VM filesystem (`.env` + rendered `001-init.sql`, written with `umask 077` — task 2.2). `STREAM_URL`/`USER_AGENT` stay non-secret env. |
| Q4 | **Single 50GB pd-standard boot disk.** e2-medium's 10GB default is tight (images ~2–3GB + ~5–10GB of window data at ~40 events/s × ~1.5KB). No second disk/mount — the 24–48h window fits; Phase 4 backups go to GCS, not local. Resizeable later if Phase 7's burst test shows pressure. |
| Q5 | **OS Login** (instance metadata `enable-oslogin: TRUE`). `gcloud compute ssh` just works; no key material in metadata or state; CI SA has no SSH path — clean least-privilege story. |
| Q6 | **IP allowlist arrives now, lockdown tested later.** Default posture: own VPC (no default open rules), allow-internal + allow-ssh/3000/8123 from `allowed_ips` variable (Ahmed's current IP, in committed `terraform.tfvars`). Phase 5 tightens and deliberately tests rejection. IP-change procedure documented in §9. |
| Q7 | **Image build+push lives in `apply.yml`, before the gate.** Job 1: build+push (un-gated, tags `sha-<commit>` + `latest`). Job 2: terraform apply, `environment: production` (required reviewer = Ahmed, log 0.3 confirmed). One approval gates the whole deploy. `plan.yml` (PR-triggered) is terraform-only. |
| Q8 | **Local destroy, CI reapply.** Ahmed runs `terraform destroy` locally (ADC owner creds — exercises the real teardown path once). Reapply happens entirely through the CI workflow from cold state (`workflow_dispatch` re-trigger on `apply.yml` — same gated path, no dummy commits). Proves the gate rebuilds the world from zero — the exact Gate 1 question. Destroy stays out of CI: a merge-triggered destroy is a mis-click away from killing the VM. |
| Q9 | *(executor-decided, review)* **Startup script renders `docker/clickhouse/initdb.d/001-init.sql`** from a heredoc template mirroring the Phase 0.4 spike-verified recipe (CREATE USER plaintext HOST ANY + scoped grants), password interpolated from the Secret Manager value. Rejected alternative: the CH image's `CLICKHOUSE_USER/CLICKHOUSE_PASSWORD` entrypoint env — its grant semantics (`GRANT ALL ON *.*`) differ from the verified recipe. Phase 3A retires initdb.d entirely; the heredoc dies with it. |
| Q10 | *(executor-decided, review)* **VM SA scopes = `cloud-platform`** (legacy OAuth scopes; the metadata token must reach Secret Manager + AR from the startup script). Least privilege is enforced at the IAM layer (SA holds only `artifactregistry.reader` scoped to one repo + `secretAccessor` scoped to two secrets); scopes are coarse in GCP's legacy model — and there is NO narrower scope for Secret Manager's API (no `secretmanager.read` scope exists), so `cloud-platform` is as narrow as the scope model permits; IAM is the real authz layer (log this defense verbatim — it's the interview answer). Note for Phase 5's IAM review: the default docker bridge can reach the metadata server, so containers could mint the VM SA token. |

Plus Phase 0/1 handoffs that are **constraints, not choices**:

- GitHub Environments `production` + required reviewer EXISTS (log 0.3) — `workflow_dispatch` fallback NOT needed; `environment: production` on the apply job.
- The Phase 1 compose file runs on the VM **verbatim** with `--no-build` (build key present but unused); `CONSUMER_IMAGE` env points at the AR tag; `.env` written by the startup script supplies the secrets the `${VAR:-default}` seams already expect (Phase 1 §10 handoff).
- Raw `raw_events (inserted_at DateTime64(3,'UTC'), event String)` shape is untouched; Phase 3A replaces it.
- The parser tests, log contract (`connected url=`, `reconnect reason=`, `insert_failed`, `inserted events=`), and count()-sample verification protocol carry forward unchanged.
- State bucket `gs://wikistream-505003-terraform-state` (log 0.2) is `infra/main`'s backend — `prefix = "main"`.

## 4. Prerequisites

- Phase 0 exit criteria met (all DONE) + Phase 1 exit criteria met (AC1–AC10, log).
- `infra/bootstrap` applied once (bucket exists, log 0.2); Ahmed's ADC credentials work locally.
- Ahmed's current public IP in hand for `allowed_ips` (discover at build time: `curl -s ifconfig.me`).
- **Build-time re-verification checklist** (Vision §9; verify at build time):

| Item | Status |
| --- | --- |
| Terraform google provider ≥ 7.43.0 (bootstrap lockfile already resolved 7.43.0) | ✅ known — pin in `infra/main` lockfile at build |
| WIF resources: `google_iam_workload_identity_pool` / `_provider` (oidc block, `attribute_mapping` with `google.subject` = `assertion.sub`, `attribute_condition` on `assertion.repository`) — shape confirmed from provider docs 2026-08-10 | ✅ shape confirmed; exact attribute names re-checked at build |
| `google_service_account_iam_member` binding: member `principalSet://iam.googleapis.com/projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/<POOL>/attribute.repository/AhmedIkram05/WikiStream`, role `roles/iam.workloadIdentityUser`; `PROJECT_NUMBER` via `data "google_project"` | ✅ standard pattern; re-check at build |
| `google-github-actions/auth` (WIF: `workload_identity_provider` + `service_account`), `hashicorp/setup-terraform`, `docker/build-push-action`, `actions/checkout`, `actions/github-script` | versions pinned at build; auth action needs `permissions: id-token: write` |
| `ubuntu-os-cloud/ubuntu-2404-lts` image; `docker.io` + `docker-compose-plugin` + `google-cloud-cli` + `git` installed BY the startup script (none ship on the base image — review finding) | ✅ known; verify at build |
| `gcloud auth configure-docker <region>-docker.pkg.dev` (regional form) for AR pulls on the VM | ✅ standard; verify at build |
| OS Login: instance metadata `enable-oslogin = "TRUE"`; requires `oslogin.googleapis.com` API; **Ahmed's account needs a role carrying `compute.osLogin`** (owner/editor covers it; module `iam` binds it explicitly — review finding) | ✅ known; verify at build |
| `sts.googleapis.com` enabled (WIF token exchange runs through the Security Token Service — review finding) | ✅ add to bootstrap API list |
| Secret Manager: `google_secret_manager_secret` with `replication { auto {} }` + `google_secret_manager_secret_version`; `random_password` (hashicorp/random provider) | ✅ known; pin provider at build |
| `gcloud compute instances reset` semantics (hard reboot; metadata/startup script preserved; disks intact) | ✅ known; verify at build |

## 5. Target file structure

```
infra/bootstrap/
  main.tf            # EXTENDED: project services + WIF pool/provider + deploy SA + binding
  variables.tf       # project_id (existing)
  outputs.tf         # NEW: wif_provider_name (for CI), deploy_sa_email
  README.md          # UPDATED: bootstrap now = state + identity (Q1 deviation noted)
infra/main/
  main.tf            # backend gcs (wikistream-505003-terraform-state, prefix "main"), provider, locals
  variables.tf       # project_id, region, zone, allowed_ips
  terraform.tfvars   # committed: project_id=wikistream-505003, region=us-central1,
                     #   zone=us-central1-a, allowed_ips=["<Ahmed's current IP>"]
  modules/
    network/         # VPC + subnet + firewall rules (internal, ssh, grafana 3000, clickhouse 8123)
    storage/         # repo-scoped artifactregistry.reader for VM SA (repo itself is bootstrap-owned, Q1)
    iam/             # VM SA + scoped secretmanager.secretAccessor bindings
    compute/         # static IP + e2-medium instance (50GB, startup script, OS Login, SA)
  templates/
    startup.sh       # the Q2 deploy mechanism (see task 2.2)
.github/workflows/
  plan.yml           # PR: fmt/validate/init/plan + plan as PR comment
  apply.yml          # merge + workflow_dispatch: build-push → gated apply → VM reset
```

Deliberate structure choice: modules stay thin (each is one small concern) —
the 4-module split is ADR-007's locked shape (matches the AWS/Azure portfolio
projects); don't flatten or expand it.

## 6. Tasks

### 2.1 — Bootstrap extension: identity (Q1)

**`infra/bootstrap/main.tf` additions:**

- `google_project_service` for: `compute.googleapis.com`, `artifactregistry.googleapis.com`, `secretmanager.googleapis.com`, `iamcredentials.googleapis.com`, `sts.googleapis.com` (WIF token exchange — review finding), `oslogin.googleapis.com`, `cloudresourcemanager.googleapis.com` (project readiness in one manual step so CI's first run needs nothing).
- WIF (shape confirmed from provider docs; HCL sketch):

```hcl
resource "google_iam_workload_identity_pool" "ci" {
  workload_identity_pool_id = "wikistream-ci"
  display_name              = "WikiStream CI"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.ci.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_condition = "assertion.repository == \"AhmedIkram05/WikiStream\""
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  oidc { issuer_uri = "https://token.actions.githubusercontent.com" }
}
```

Note: the `attribute_condition` restricts to THIS repo only, deliberately NOT
to a branch — `plan.yml` must run on feature-branch PRs. `assertion.ref`
branch pinning is the kind of hardening Phase 5 could add; not now.

- Deploy SA `wikistream-deploy` + project-level roles: `roles/compute.admin`,
  `roles/artifactregistry.admin`, `roles/secretmanager.admin`,
  `roles/iam.serviceAccountAdmin`, `roles/iam.serviceAccountUser`, and
  `roles/storage.objectAdmin` **scoped to the tfstate bucket only**
  (`google_storage_bucket_iam_member` on `wikistream-505003-terraform-state`).
  (Project-level admin on the deploy SA is the honest, standard CI trade —
  the least-privilege interview story is the VM SA, scoped to exactly two
  secrets and one repo. State this in the log so Phase 5's IAM review knows
  it's a decision, not an oversight.)
- Binding: `google_service_account_iam_member` (role `roles/iam.workloadIdentityUser`,
  member `principalSet://iam.googleapis.com/projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/wikistream-ci/attribute.repository/AhmedIkram05/WikiStream`,
  `PROJECT_NUMBER` via `data "google_project"`).
- Artifact Registry repo (review finding: `build-push` runs before the gated
  apply, so the repo must exist from the very first merge and survive destroy —
  the same "can't bootstrap itself" class as the bucket and WIF):
  `google_artifact_registry_repository` `wikistream-consumer` (location
  `us-central1`, format `DOCKER`). It lives HERE, never in `infra/main` —
  `infra/main`'s storage module only binds the VM SA's reader role on it
  (plain string reference; no data source needed).
- **`outputs.tf`:** `wif_provider_name` (the full `projects/<num>/.../providers/github` string) + `deploy_sa_email` — the workflow files reference these values; record the resolved values in the implementation log too (CI can't read bootstrap state).

**Apply (one-time, by hand, local state — same ceremony as log 0.2):**
`gcloud config set project wikistream-505003 && terraform init && terraform apply -var project_id=wikistream-505003`.
Add `random` provider to bootstrap's lockfile? No — `random_password` lives in
`infra/main` (secrets are main-config resources, destroyed on teardown). Add
`random` to `infra/main`'s required_providers.

**Verify:** `terraform apply` clean; `gcloud iam service-accounts list` shows
`wikistream-deploy`; pool visible via
`gcloud iam workload-identity-pools describe wikistream-ci`; `gcloud artifacts
repositories describe wikistream-consumer --location us-central1` exists.
Record outputs + deviation in log (task 2.8).

### 2.2 — Main config + modules + startup script (Q2/Q4/Q5/Q6/Q9/Q10)

**`infra/main/main.tf`:** backend `gcs` (bucket `wikistream-505003-terraform-state`,
prefix `main`); provider `google` (project from vars, `region = us-central1`);
`required_providers`: `google >= 7.43`, `random`.

**`modules/network`:** `google_compute_network` `wikistream-vpc`
(`auto_create_subnetworks = false`; new VPC = no default open rules) +
`google_compute_subnetwork` `wikistream-subnet` (`10.0.0.0/24`, region var).
Firewall rules:
- `allow-internal` — source `10.0.0.0/24`, all ports (compose inter-service traffic + SSH-local work).
- `allow-ssh` — port 22, source `allowed_ips` (Q6: your IP now; lockdown testing is Phase 5).
- `allow-grafana` — port 3000, source `allowed_ips`.
- `allow-clickhouse` — port 8123, source `allowed_ips` (ad-hoc queries from laptop; consumer talks localhost).

**`modules/storage`:** `google_artifact_registry_repository_iam_member`
`roles/artifactregistry.reader` for the VM SA on the **bootstrap-owned** repo
`wikistream-consumer` — **scoped to that one repository** (ADR-008's named
deliverable; nothing broader). The repo itself is created in bootstrap (Q1
review fix): destroying/reapplying `infra/main` removes only the binding; the
repo survives, so the CI build-push job never deadlocks.

**`modules/iam`:** VM SA `wikistream-vm`; `google_secret_manager_secret_iam_member`
`roles/secretmanager.secretAccessor` on BOTH secrets, scoped to each secret only.

**`modules/compute`:** `google_compute_address` `wikistream-ip` (static,
region var — attached to a running VM = free; keeps URLs stable across
re-provision); `google_compute_instance` `wikistream-vm`:
- `machine_type = "e2-medium"`, zone var.
- Boot disk: `ubuntu-os-cloud/ubuntu-2404-lts`, **50GB `pd-standard`** (Q4).
- `metadata_startup_script` = **variable** — main.tf passes
  `file("${path.module}/templates/startup.sh")` (`file()` resolves relative to
  the declaring module, so the script must NOT be referenced from inside
  `modules/compute`; review finding); `metadata = { enable-oslogin = "TRUE" }` (Q5).
- `service_account`: `wikistream-vm` email, `scopes = ["https://www.googleapis.com/auth/cloud-platform"]` (Q10).
- `allow_stopping_for_update = true` (so later phase applies can resize/alter without destroy).
- **Labels on every resource in all four modules + main-scope secrets:**
  `labels = { project = "wikistream", managed-by = "terraform", phase = "2" }`
  — makes §9's cost sanity check a real billing-attribution story and gives
  post-destroy orphan checks a filter (review finding).

**`infra/main/templates/startup.sh`** — the Q2 deploy mechanism. Runs as root
on every boot; idempotent; every stage logs to `/var/log/wikistream-startup.log`
(and the serial console). Sketch (exact commands verified at build time):

```bash
#!/usr/bin/env bash
set -euo pipefail
umask 077                              # .env + 001-init.sql stay 600 (review finding)
exec > >(tee -a /var/log/wikistream-startup.log) 2>&1  # log file AND serial console, so get-serial-port-output works without SSH
echo "[$(date -u)] startup begin"

# 0. Cloud SDK + git — NOT on the ubuntu-2404-lts base image (review finding)
command -v gcloud >/dev/null || { apt-get update && apt-get install -y google-cloud-cli; }
command -v git >/dev/null || apt-get install -y git

# 1. Docker + compose plugin (idempotent)
command -v docker >/dev/null || { apt-get update && apt-get install -y docker.io docker-compose-plugin; }
systemctl enable --now docker

# 2. Repo = single source of truth; pull-or-clone so a GitHub outage during a
#    reset can't take the stack down (review finding)
if [ -d /opt/wikistream/.git ]; then git -C /opt/wikistream pull --ff-only || true; else git clone --depth 1 https://github.com/AhmedIkram05/WikiStream /opt/wikistream; fi
cd /opt/wikistream

# 3. Project id from the metadata server — deterministic, no gcloud config (review finding)
GCP_PROJECT=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/project/project-id)

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
```

Notes:
- `GCP_PROJECT` comes from the metadata server (step 3) — never from `gcloud config`, which is unreliable on a fresh boot; the project id lives in `terraform.tfvars`, not on the instance.
- The pull-or-clone keeps "every boot re-syncs repo + image, so a reset IS a deploy of current `main` + current `latest`" while surviving GitHub outages (last-good repo keeps the stack alive). The startup script itself is static — it's Terraform-owned config; deploys don't need Terraform state churn.
- `001-init.sql` runs on first boot only (empty `ch-data` volume, per Phase 1 Q3 semantics). On resets, the volume persists — table/user already exist; `IF NOT EXISTS` makes it safe regardless. The rendered heredoc is the **authoritative VM copy** — it overwrites the repo's dev-default file on the VM; if the Phase 1 recipe changes before 3A retires initdb.d, the heredoc is what actually runs (drift noted, intentional).
- `.env` must be written BEFORE `compose pull` (pull doesn't need it, but keep the ordering simple).
- OS Login SSH users are NOT in the docker group — all verification commands over SSH need `sudo` (AC6/troubleshooting; review finding).

### 2.3 — Secrets (Q3)

**`infra/main`** (could sit in main.tf or `modules/storage`; keep in main scope):
`random_password` `ch_password` / `gf_password` (`length = 24`, `special = false`),
`google_secret_manager_secret` `clickhouse-password` / `grafana-admin-password`
(`replication { auto {} }`), `google_secret_manager_secret_version` each.
Outputs (or `data "google_secret_manager_secret_version"` when needed) so
verification can read them back. Secrets carry the standard labels (task 2.2
note). Rotation story (interview follow-up, already true by construction):
re-apply regenerates `random_password` → new secret version → next VM reset
re-renders `.env` from the new version.

### 2.4 — CI workflows (Q7, ADR-008)

**`.github/workflows/plan.yml`** — trigger: `pull_request`. Permissions:
`id-token: write`, `contents: read`, `pull-requests: write`. Steps: checkout →
`google-github-actions/auth` (WIF provider name from bootstrap outputs; SA
`wikistream-deploy`) → `hashicorp/setup-terraform` (pin version; provider ≥7.43
via lockfile) → `terraform fmt -check` → `terraform init` (backend config via
env/`-backend-config` or committed backend — bucket exists, so init works) →
`terraform validate` → `terraform plan -out=tfplan` → post the plan to the PR
via `actions/github-script` (`github.rest.issues.createComment`, render plan
inside a fenced block; the comment is the reviewable artifact).

**`.github/workflows/apply.yml`** — triggers: `push` (branches: `main`) +
`workflow_dispatch` (Q8 re-trigger for the reapply-after-destroy).
`permissions: { contents: read, id-token: write }` at workflow level (both
jobs use the auth action — review finding). Two jobs:

1. `build-push` (no env): checkout → `google-github-actions/auth` → `gcloud
   auth configure-docker us-central1-docker.pkg.dev` → `docker/build-push-action`
   with `context: ./consumer`, `push: true`, `tags:
   us-central1-docker.pkg.dev/<project>/wikistream-consumer/consumer:sha-<git
   sha>` + `:latest` (Q7). The AR repo is bootstrap-owned (Q1), so this job
   works from the very first merge and after a destroy/reapply — no ordering
   deadlock.
2. `apply` (`needs: build-push`, `environment: production` — the log 0.3
   reviewer gate pauses the job here): **checkout** (the job runs on a fresh
   runner with no source tree — review finding) → auth → setup-terraform →
   `terraform init` → `terraform plan -out=tfplan` → `terraform apply tfplan`
   → **`gcloud compute instances reset wikistream-vm --zone us-central1-a`**
   (explicit `--zone` — no gcloud config on the runner; review finding) (Q2
   deploy; deploy SA has `compute.admin`).

Notes:
- The apply job's plan-then-apply inside the gated job keeps the "human
  approves the change" moment right before the change — don't move plan to an
  ungated pre-job; the gate must cover the apply decision.
- The reset step makes every apply a full re-deploy (VM reboot ~1–2 min).
  Acceptable for this project's deploy cadence; the startup log + serial
  output are the recovery evidence. A wedged VM that fails to recover would
  be caught by Gate 1's "survives unattended" check — that's exactly what it's
  for.
- Do NOT add a destroy job (Q8).
- Workflow files land on `main` via the Phase 2 PR itself; `plan.yml` runs
  from the PR head, `apply.yml` first fires on the merge that ships it.
- Fork PRs get no OIDC token — `plan.yml` silently won't run for them
  (public repo; solo-dev same-repo PRs are the only path today — note, not a gap).

### 2.5 — First deploy through the gate

1. PR `feature/GCP-Deployment-of-Skeleton` → `main` with everything above.
2. Verify `plan.yml` ran: green fmt/validate/plan + the plan comment posted.
3. Merge. Watch `apply.yml`: `build-push` green; `apply` job visibly **Waiting**
   for review (this is AC4's evidence — screenshot/URL in the log).
4. Approve in the Actions UI. Apply runs; final step resets the VM.
5. Run the verification battery (task 2.6/AC5–AC9).

### 2.6 — Deploy-path proof (Q2): reset → recover

With the stack green from 2.5: `gcloud compute instances reset wikistream-vm
--zone us-central1-a` (you, via gcloud — the same operation the workflow
runs). Confirm, with zero manual steps: VM returns RUNNING; startup log ends
with `startup done`; the three containers come back (volume persists — data
survives); consumer logs a fresh `connected url=`, 0 Traceback; `count()`
strictly increases between two samples. SSH verification commands run with
`sudo` (OS Login user is not in the docker group — review finding). This is
the "VM pulls the image and reproduces Phase 1's result" exit criterion
exercised twice (initial provision + reset).

### 2.7 — Destroy-and-reapply cycle (Q8)

1. **Local destroy** (Ahmed): `gcloud config set project wikistream-505003`,
   `cd infra/main`, `terraform init`, `terraform destroy`. Confirm clean:
   no VM/secrets/firewall/bindings remain (`gcloud compute instances list`,
   `gcloud secrets list` empty), and the bootstrap layer is UNTOUCHED: bucket,
   WIF pool, deploy SA, **and the AR repo** all still present — that's why CI
   still works AND why the reapply's build-push won't 404 (Q1's payoff).
2. **CI reapply from cold state:** `workflow_dispatch` on `apply.yml` →
   approve → apply recreates the world; the VM comes up fresh (empty
   `ch-data` volume → initdb.d renders the user+table again), data flows.
   **Measure it:** record workflow_dispatch start → first `connected url=` →
   first `count()` increase; the log line "cold state to flowing data: N
   minutes, zero manual steps" is the phase's interview metric (review finding).
3. Re-run the core battery (AC5/AC6/AC7 abbreviated: VM RUNNING, stack up,
   consumer connected, count() increasing, Grafana 200). **Re-query the static
   IP first** — destroy released the address and the reapply got a new one
   (`gcloud compute addresses describe wikistream-ip --region us-central1
   --format='value(address)'`); AC7/AC9 use the new IP, not the pre-destroy
   one (review finding).

### 2.8 — Wrap-up

1. Populate `docs/implementation-log.md` Phase 2 entries: 2.1 bootstrap
   extension (deviation: ADR-007 wording — now also owns the AR repo), 2.2–2.3
   resource decisions (Q9/Q10 notes for Phase 5's IAM review — incl. the
   "no narrower scope exists" defense and the metadata-server-from-containers
   note), 2.5 gate evidence (CI run URLs, Waiting-state proof + **the approval
   identity and timestamp** — proves a human, not a bot, passed the gate),
   2.6 reset evidence, 2.7 destroy/reapply evidence + the cold-to-flowing
   timing metric, resolved WIF provider name + SA emails, IP used in
   `allowed_ips` (and the change procedure pointer), the one-paragraph *why*
   of the reset-based deploy (stateless-by-design: data on the surviving
   volume, code from git+AR, reset = cheapest convergence primitive, every
   deploy doubles as a boot-recovery rehearsal), the deploy SA's
   `serviceAccountAdmin`/`serviceAccountUser` rationale (GCP requires them to
   create a VM that runs as an SA — the answer to "why admin?"), secret
   rotation story (re-apply → new version → reset re-renders), `:latest`
   rollback path (revert main + re-run apply), cost sanity line (e2-medium
   ~$23/mo + 50GB ~$2.5/mo + static IP $0 attached + AR/SM ~$0 → ~$25.5/mo ≈
   8% of the $300 trial per month, ~25% over a 3-month window; formal cost
   note is Phase 7b).
2. `infra/bootstrap/README.md` updated for the extended bootstrap.
3. Coverage-boundary doc: no new business-critical modules this phase (no
   Python code shipped); no change.
4. Commit on `feature/GCP-Deployment-of-Skeleton`, one PR to `main`, messages
   matching repo style.

## 7. Acceptance criteria (self-checkable)

| # | Criterion | How an agent verifies it |
| --- | --- | --- |
| AC1 | Bootstrap extended and applied | `terraform apply` in `infra/bootstrap` clean; `gcloud iam workload-identity-pools describe wikistream-ci` exists; `wikistream-deploy` SA exists; `gcloud artifacts repositories describe wikistream-consumer --location us-central1` exists; outputs recorded |
| AC2 | `plan.yml` works on PR | PR shows green fmt/validate/plan + a comment containing the plan (check via `gh pr view <n> --comments` or API) |
| AC3 | Image built and pushed | `gcloud artifacts docker images list us-central1-docker.pkg.dev/wikistream-505003/wikistream-consumer` shows `latest` + `sha-<commit>` tags |
| AC4 | Gate actually gates | The apply job sits in "Waiting for review" (Actions UI/API shows `waiting` status) before approval — not auto-running |
| AC5 | VM provisioned correctly | `gcloud compute instances describe wikistream-vm` → status RUNNING, machine e2-medium, 50GB disk; `gcloud compute ssh wikistream-vm --command "echo ok"` works (OS Login) |
| AC6 | Stack live on VM | Over SSH (with `sudo` — OS Login user is not in the docker group): `docker compose ps` → 3 services Up; `docker inspect -f '{{.Image}}' <consumer container>` matches the AR URI (`.../wikistream-consumer/consumer:latest` — proves the deploy seam, not a stale image); startup log ends `startup done`; consumer log has ≥1 `connected url=`, 0 Traceback; two `SELECT count()` samples ≥1 min apart, strictly increasing |
| AC7 | Grafana reachable per firewall rule | From Ahmed's machine: `curl -s -o /dev/null -w "%{http_code}" http://<static-ip>:3000` → 200; login with the value read from Secret Manager succeeds; dashboard "Phase 1 — Walking Skeleton" + panel query return ≥1 row (Phase 1 AC5/AC6 shape against the VM) |
| AC8 | Secrets are real, not defaults — and NOT in plaintext artifacts | `gcloud secrets versions access latest --secret=grafana-admin-password` yields a 24-char value; that value logs into Grafana (proves env-only swap end-to-end); same for `clickhouse-password` — over HTTP 8123 from the laptop: `curl -u wikistream:'<pw>' 'http://<ip>:8123/?query=SELECT%201'` (a native `clickhouse-client` would use port 9000, which the firewall does NOT open). Least-privilege evidence: `gcloud compute instances describe wikistream-vm --format='value(metadata.startup-script)'` and `terraform show tfplan` each contain **0 occurrences** of either secret value (review finding — where the secret does NOT live) |
| AC9 | Firewall allows exactly the allowlist | From Ahmed's IP: 22/3000/8123 reachable. One-sided rejection proof now (review finding): from the VM, `curl -s -o /dev/null -w "%{http_code}" http://<static-ip>:3000` and `:8123` → non-200 — the VM's source IP is NOT in `allowed_ips`, so the allowlist demonstrably filters. Full rejection-probe testing is Phase 5 — record this boundary in the log |
| AC10 | Deploy path proven (Q2) | Task 2.6: reset → unattended recovery, fresh `connected`, count() increasing, 0 Traceback |
| AC11 | Destroy-and-reapply (Q8) | Task 2.7: local destroy clean; bootstrap layer intact after; `workflow_dispatch` reapply green from cold state; stack flowing again |
| AC12 | Log + docs consistent | Implementation-log Phase 2 populated with numbers + CI URLs + deviations; bootstrap README updated |

## 8. Verification gate (master plan wording)

> Skeleton runs unattended on GCP, reachable per ADR-010's firewall rule,
> CI/CD gate proven functional.

Phase exit criteria = AC1–AC12. Then **Go/No-Go Gate 1 applies (master plan
§4) — an explicit final step of this phase, not a separate doc:**

- *Stability across cycles:* AC11 proved a full destroy/reapply; AC10 proved
  reset-survival. Both must have passed on the FIRST attempt's evidence (or a
  recorded, understood fix — not a silent retry).
- *Gate works end-to-end:* AC2 + AC4 — plan comment and Waiting-state
  observed on real runs.
- *Survives unattended:* the stack is left running post-2.7 with no manual
  babysitting while Phase 3 planning starts; Gate 1's verdict is recorded in
  the implementation log, not assumed.

## 9. Troubleshooting notes

- **WIF auth fails in CI ("permission denied" / "invalid grant"):** check the
  provider's `attribute_condition` string matches the repo exactly
  (`AhmedIkram05/WikiStream`, case-sensitive); check the principalSet member
  uses the PROJECT **NUMBER** (not id); check the workflow has
  `permissions: id-token: write`; check the auth action's provider string
  against bootstrap outputs.
- **`terraform init` fails from CI:** the deploy SA needs
  `storage.objectAdmin` on the bucket (scoped binding — verify it's on the
  right bucket); the backend `prefix` must not collide with bootstrap's local
  state.
- **`plan.yml` comment missing:** `pull-requests: write` permission; the
  github-script needs the PR number from `github.event.pull_request.number`.
- **`plan.yml` never runs at all on a PR:** fork PRs get no OIDC token (public
  repo) — expected; same-repo PRs are the only path today (review finding).
- **Image push fails:** `docker/build-push-action` needs the `google-github-actions/auth`
  step BEFORE it (same job) and `gcloud auth configure-docker` for the
  regional endpoint; check the repo id `wikistream-consumer` matches the URI.
- **VM up but containers absent:** read the startup log — `gcloud compute ssh
  --command "sudo cat /var/log/wikistream-startup.log"`; if SSH itself fails,
  `gcloud compute instances get-serial-port-output wikistream-vm` works
  without SSH (the script tees output to both the log file and the serial
  console — review finding). Common: `google-cloud-cli` install failed
  (missing apt repo), git clone fails (repo visibility / depth flag),
  `configure-docker` region mismatch, `.env` written after first
  `compose up` (ordering), docker-group permission (use `sudo`).
- **Consumer connects but `count()` never grows:** verify `.env` values match
  the secrets (a wrong CH password surfaces as `insert_failed` warnings, not
  a crash — the Phase 1 log contract still applies); check the datasource yaml
  env interpolation (Phase 1 C2 fix: compose must supply `CLICKHOUSE_PASSWORD`
  into the grafana container).
- **Grafana 200 but login fails:** AC8 — read the secret and use THAT value;
  `GF_SECURITY_ADMIN_PASSWORD` only applies on first grafana data-volume
  creation — if a previous boot created the volume with a different value,
  `docker compose down && up -d` (or wipe the grafana volume) — volume
  persistence semantics are the same as Phase 1.
- **IP change (dynamic IP — the cross-phase risk):** edit `allowed_ips` in
  `infra/main/terraform.tfvars` → PR → approve → apply. If you're already
  locked out of SSH, OS Login via the console's serial port or the metadata
  edit + reset path still works. Phase 5 decides whether to harden further.
- **Reset during active inserts:** safe by design — ClickHouse flushes on
  shutdown, and the consumer's reconnect loop (Phase 1-proven) absorbs the
  outage; expect `reconnect`/`clickhouse_unavailable` WARNINGs on recovery,
  not failures.
- **Quota errors on VM creation:** e2-medium quota is account-wide; check
  `gcloud compute regions describe us-central1` quotas; request increase via
  console if needed.
- **Cost sanity:** e2-medium ~$23/mo, 50GB pd-standard ~$2.5/mo, static IP $0
  while attached, AR/SM negligible → ~$25.5/mo ≈ 8% of the $300 trial per
  month (tens of cents/day). All resources carry `labels`
  (`project=wikistream`) so `gcloud billing` reports attribute costs
  correctly. (Formal FinOps note is Phase 7b.)

## 10. Handoff to Phase 3 (what Phase 3 inherits)

- **A proven deploy path:** merge → build+push → gated apply → VM reset. Phase
  3A/3B's schema/MV/dashboard changes ship through the same gate; the VM's
  startup script is static, so NEW files (migrations, dashboards) reach the VM
  via the git clone on reset — **Phase 3 must add its new artifacts to the
  repo, not to the startup script** (script changes only when the deploy
  mechanism itself changes). Rollback = revert `main` + re-run apply: the
  previous commit's image re-tags as `:latest` and the reset re-deploys it
  (review finding).
- **Live infrastructure to build against:** VM (static IP, OS Login),
  ClickHouse 26.3.17 + Grafana 13.1.1 + consumer image on AR (bootstrap-owned
  repo — survives `infra/main` destroy), two SM secrets, WIF CI identity, IP
  allowlist firewall.
- **`raw_events` is still the Phase 1 shape** — Phase 3A's versioned migrations
  replace it AND retire `docker/clickhouse/initdb.d/001-init.sql` (both the
  repo copy and the startup script's rendered heredoc — update `startup.sh`
  accordingly in the 3A PR).
- **The `:latest` image tag is the deploy seam** — Phase 3+ image changes flow
  automatically; the `sha-<commit>` tag exists for pinning if reproducibility
  ever demands it (noted ceiling, not a gap).
- **Gate 1 verdict** must be recorded (implementation log) before Phase 3
  planning starts; if it's No-Go, Phase 2's foundation is fixed first per
  master plan §4.
- **Carry-forward contracts:** the consumer log contract, count()-sample
  protocol, parser tests (19/19, 100% on sse.py), and the Phase 1 AC5/AC6
  Grafana API verification shape — all reusable as-is against the VM.
