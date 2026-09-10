# Phase 6 — Kubernetes Integration (WikiStream)

**Status:** Ready — v4 ephemeral (2026-09-06) — per-project cluster, owned + torn down with WikiStream
**Owner:** Ahmed
**Goal:** Get credible K8s on the DE CV with the laziest *honest* integration. No fake scaling.
**Budget:** £70/mo hard ceiling for the K8s delta (ephemeral Autopilot zonal — owned + torn down with WikiStream, see §8).
**Decisions locked in grilling:** No KEDA (§3.2), singleton consumer with GCS state (§4), bake SQL into job images (§5), rewrite bash→Python (§6), Terraform for platform + kubectl apply -k for workloads (§7), all 4 timers → CronJobs including backup.
**Changelog:** §16 v3 slim cut per defensibility review (see §16). v2 was 736 lines reference architecture.

---

## 1. Why K8s at all for WikiStream

**Only reason is CV signal.** Interview answer: "WikiStream proved single-VM docker-compose; Phase 6 proves I can run the same pipeline on managed K8s without lying about scale." Optimizes for **credibility per line of YAML**.

What it *does* add:

- Managed lifecycle (rolling updates, health probes, `terminationGracePeriodSeconds` flush) for the singleton consumer.
- Batch jobs as declarative `CronJob`s (export/parity/gx/backup) — the actual batch surface becomes K8s-native.
- Platform breadth: Artifact Registry, Workload Identity, VPC-native Autopilot, Terraform — each a separate keyword without inventing a bottleneck.

What it does **not** claim: horizontal scaling. Each replica opens its own SSE connection to `stream.wikimedia.org` and duplicates events; dedup ring (50k) is per-pod. Honest story is `replicas: 1`.

> Glossary drift fix: `docs/planning/vision-and-adr.md` §4 trade-off table has row "Single VM | GKE Autopilot (zonal, ephemeral) — §3/§8 Phase 6".

| Decision | ADR |
|----------|-----|
| Zonal ephemeral cluster, owned + torn down with WikiStream | [ADR-0012](../adr/0012-gke-autopilot-shared-cluster.md) |
| Singleton consumer (`replicas: 1`) + GCS state fencing | [ADR-0013](../adr/0013-consumer-singleton-gcs-state.md) |
| 4 timers → CronJobs, baked SQL, bash→Python | [ADR-0014](../adr/0014-cronjobs-over-systemd.md) |

---

## 2. Architecture

### 2.1 Before (Phase 5)

```
Wikimedia SSE → consumer (docker-compose, /state on PD) → ClickHouse (VM, ch-data PD 50GB)
                                      ↘ dead_letter → CH
              systemd timers @ :00/:05/:20/:30 → export.sh / parity.sh / backup.sh / gx → CH/BQ/GCS
              Grafana (VM) → CH + BQ
              VM: e2-medium, startup.sh + boot.sh
```

### 2.2 After (Phase 6)

```
                                                           ┌──────────────────────────────────────┐
Wikimedia SSE ──→  consumer Deployment (1 replica, GCS state)│ GKE Autopilot (zonal, ephemeral)     │
                   │  ↘ /healthz :8080                      │  namespace: wikistream             │
                   ├──→ ClickHouse (VM, unchanged) ─────────┼──→ VPC (same subnet as VM)        │
                   │      via Service wikistream-clickhouse │  Workload Identity → Secret Mgr   │
                   │      ↘ dead_letter                     │  GMP PodMonitoring               │
                   CronJobs (Forbid, OnFailure)             │  NetworkPolicy default-deny       │
                    ├─ wikistream-export   (*:00, Python) ──┼──→ CH → GCS → BQ (baked SQL)     │
                    ├─ wikistream-parity   (*:05, Python) ──┼──→ CH vs BQ → pipeline_health    │
                    ├─ wikistream-gx       (*:30, Python) ──┼──→ CH (GX Core)                  │
                    └─ wikistream-backup   (*:20, Python) ──┼──→ CH BACKUP TABLE → GCS        │
                   infra/k8s/ (kustomize)                   └──────────────────────────────────────┘

ClickHouse, ch-data PD, Grafana (VM), BigQuery, GCS — unchanged. VM timers disabled after K8s parity.
Each project owns + tears down its own cluster (sequential, no cross-project reference).
```

**Ownership:** `wikistream-505003` creates/owns its ephemeral Autopilot cluster (`infra/main/modules/gke`) + tears it down with WikiStream. No cross-project reference — sequential lifecycle per project.

Labels on every object: `app.kubernetes.io/name`, `app.kubernetes.io/instance=wikistream`, `app.kubernetes.io/component`, `app.kubernetes.io/part-of=wikistream`.

---

## 3. Cluster Spec

| Field | Value | Rationale |
| ------- | ------- | ----------- |
| Type | **GKE Autopilot** | No node pools; pay per pod; fits £70. CV keyword without ops toil. |
| Location | **Zonal** `us-central1-a` | Same zone as `wikistream-vm` → pod→VM internal IP. Cheaper to explain than regional; regional is honest HA but LARP for a singleton that writes to a zonal VM. Document zonal as deliberate. |
| VPC | Existing `module.network` VPC/subnet | `network = module.network.vpc_self_link`, `subnetwork = module.network.subnetwork_self_link`, `ip_allocation_policy {}` (VPC-native). Outputs `cluster_ipv4_cidr` for firewall. |
| Namespace | `wikistream` | `ResourceQuota` + `LimitRange` per namespace (§9). |
| Auth | Workload Identity (GKE → GCP) | Each KSA → GCP SA binding. No static JSON. |
| Add-ons | **GMP required**; Cloud Logging automatic | `pipeline_health` is data health, not platform health. |

Terraform:

```hcl
# infra/main/modules/gke/main.tf (new)
resource "google_container_cluster" "wikistream" {
  name     = "wikistream-autopilot"
  location = var.zone       # zonal — deliberate, see table
  project  = var.project_id
  enable_autopilot = true
  network    = var.vpc_self_link
  subnetwork = var.subnet_self_link
  ip_allocation_policy {}
  deletion_protection = false
  labels = var.labels
}
output "cluster_ipv4_cidr" { value = google_container_cluster.wikistream.ip_allocation_policy[0].cluster_ipv4_cidr_block }
output "endpoint" { value = google_container_cluster.wikistream.endpoint }
output "ca_certificate" { value = google_container_cluster.wikistream.master_auth[0].cluster_ca_certificate }

resource "google_service_account" "consumer" { account_id = "wikistream-consumer" }
resource "google_service_account" "jobs"     { account_id = "wikistream-jobs" }
resource "google_service_account_iam_member" "consumer_wi" { service_account_id = google_service_account.consumer.name, role = "roles/iam.workloadIdentityUser", member = "serviceAccount:${var.project_id}.svc.id.goog[wikistream/wikistream-consumer]" }
resource "google_service_account_iam_member" "jobs_wi"     { service_account_id = google_service_account.jobs.name, role = "roles/iam.workloadIdentityUser", member = "serviceAccount:${var.project_id}.svc.id.goog[wikistream/wikistream-jobs]" }
```

**Manifests** (`infra/k8s/`): `namespace.yaml`, `serviceaccounts.yaml`, `deployment.yaml`, `service-clickhouse.yaml`, `networkpolicy.yaml`, `resourcequota.yaml`, `limitrange.yaml`, `cronjobs.yaml`, `podmonitoring.yaml` (+ `kustomization.yaml` §9).

```yaml
# infra/k8s/namespace.yaml
apiVersion: v1
kind: Namespace
metadata: {name: wikistream, labels: {name: wikistream, "app.kubernetes.io/part-of": wikistream}}
---
# infra/k8s/serviceaccounts.yaml
apiVersion: v1
kind: ServiceAccount
metadata: {name: wikistream-consumer, namespace: wikistream}
---
apiVersion: v1
kind: ServiceAccount
metadata: {name: wikistream-jobs, namespace: wikistream}
```

---

## 4. Consumer: Singleton Deployment + GCS State

### 4.1 Why singleton

SSE stream is a single cursor (`Last-Event-ID` = Kafka composite, `consumer.py:88-123`). Horizontal replicas duplicate the stream. KEDA/HPA rejected (ADR-013).

### 4.2 Image

Existing `consumer/Dockerfile` builds correctly. For K8s just pin SHA in deploy:

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev
COPY src ./src
CMD ["uv", "run", "python", "-m", "src.consumer"]
```

CI builds → `us-central1-docker.pkg.dev/wikistream-505003/wikistream-consumer/consumer:<sha>@sha256:<digest>` — digest-pinned in `infra/k8s/deployment.yaml` (not just tag). One Trivy scan in CI (§10) is enough.

# ponytail: pin base SHA + USER 65532 + cosign/BinaryAuth when compliance requires, not for DE CV

### 4.3 State: local file → GCS

Current `consumer.py:125-145` uses `/state/consumer_state.json` (PD-backed). On K8s each rescheduled pod gets empty `emptyDir` → cursor loss.

New: `gs://wikistream-505003-consumer-state/consumer_state.json`

```python
# consumer/src/state_gcs.py (new, ~50 lines)
# ponytail: single writer so no lock; if_generation_match guards the brief RollingUpdate overlap.
import json, os, time
from google.cloud import storage
from google.api_core.exceptions import PreconditionFailed
BUCKET = os.environ["STATE_BUCKET"]
KEY = "consumer_state.json"
_client = storage.Client()
_bucket = _client.bucket(BUCKET)

def load_state() -> dict | None:
    blob = _bucket.blob(KEY)
    return json.loads(blob.download_as_text()) if blob.exists() else None

def save_state(last_event_id, total, retries=3) -> None:
    payload = json.dumps({"last_event_id": last_event_id, "total": total})
    blob = _bucket.blob(KEY)
    for attempt in range(retries):
        gen = blob.generation if blob.exists() else 0  # 0 == create-only (must not exist)
        try:
            blob.upload_from_string(payload, if_generation_match=gen)  # guards RollingUpdate overlap
            return
        except PreconditionFailed:
            if attempt == retries - 1: raise
            time.sleep(0.2 * (2 ** attempt))
            blob.reload()
```

Changes to `consumer.py`: `import state_gcs` (flag `STATE_BACKEND=gcs|file`, default `gcs` on K8s), debounced save unchanged, `if_generation_match` retry added, `STATE_DIR` volume removed.

Why not PVC: GCS already in stack (export staging, TF state), Workload Identity already needed, survives reschedule without `ReadWriteOnce` pinning.

Bucket hygiene: `uniform_bucket_level_access=true`, `versioning.enabled=true`, lifecycle abort incomplete multipart after 7d. IAM: `storage.objectCreator` + `storage.objectViewer` is enough (not `objectAdmin`).

### 4.4 Deployment manifest

```yaml
# infra/k8s/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: wikistream-consumer
  namespace: wikistream
  labels: { app.kubernetes.io/name: wikistream-consumer, app.kubernetes.io/component: consumer, app.kubernetes.io/part-of: wikistream }
spec:
  replicas: 1
  revisionHistoryLimit: 5
  strategy: { type: RollingUpdate, rollingUpdate: { maxUnavailable: 0, maxSurge: 1 } }
  selector: { matchLabels: { app.kubernetes.io/name: wikistream-consumer } }
  template:
    metadata: { labels: { app.kubernetes.io/name: wikistream-consumer } }
    spec:
      serviceAccountName: wikistream-consumer # → GCP SA via WI
      terminationGracePeriodSeconds: 30        # SIGTERM flush 10s + headroom
      securityContext: { runAsNonRoot: true, runAsUser: 1000 }
      containers:
      - name: consumer
        image: us-central1-docker.pkg.dev/wikistream-505003/wikistream-consumer/consumer:<tag>@sha256:<digest>
        ports: [{ name: healthz, containerPort: 8080 }]
        env:
        - { name: CLICKHOUSE_HOST, value: "wikistream-clickhouse" } # Service (§9)
        - { name: CLICKHOUSE_PORT, value: "8123" }
        - { name: STATE_BUCKET, value: "wikistream-505003-consumer-state" }
        - { name: CLICKHOUSE_PASSWORD, valueFrom: { secretKeyRef: { name: wikistream-secrets, key: clickhouse-password } } }
        resources: { requests: { cpu: "250m", memory: "512Mi" }, limits: { cpu: "500m", memory: "1Gi" } }
        securityContext: { allowPrivilegeEscalation: false }
        livenessProbe: { httpGet: { path: /healthz, port: healthz }, periodSeconds: 30 }
        readinessProbe: { httpGet: { path: /healthz, port: healthz }, periodSeconds: 10 }
```

# ponytail: PDB/topologySpread/priorityClass/preStop sleep 5 + readOnlyRootFilesystem/drop ALL/seccomp are correct for multi-replica stateless — deferred for this singleton; add when replicas >1

Health: `httpGet /healthz:8080` via new `consumer/src/healthz.py` (~35 lines, stdlib `http.server` reusing `healthcheck.is_fresh` (`HEALTH_STALE_SECONDS=300` from healthcheck.py, reading last event time/GCS state age)). No `exec uv run`.

---

## 5. CronJobs: Baking SQL into the Image

SQL (`warehouse/sql/*.sql`) + schemas (`warehouse/schemas/*.json`) are `COPY`'d into the job image at build, not `ConfigMap`. Immutable artifact, no rollout on SQL change.

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.jobs.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev
COPY warehouse/sql ./warehouse/sql
COPY warehouse/schemas ./warehouse/schemas
COPY warehouse/*.py ./warehouse/
COPY gx/suite.py ./gx/suite.py
```

---

## 6. CronJobs: Bash → Python

`export.sh`/`parity.sh` do `docker exec` + `gcloud storage cp` + `bq load`. On K8s use `clickhouse-connect` over HTTP to `CLICKHOUSE_HOST`.

Scope: `warehouse/export.py`, `warehouse/parity.py`, `warehouse/backup.py` sharing `warehouse/clickhouse.py` / `gcs.py` / `bq.py`.

Behavior parity:

- Window: last completed UTC hour via `datetime.now(timezone.utc).replace(minute=0…)` (reuse GX logic, not `date -d`).
- `RUN_ID = HHMMSS` uniqueness, `export_runs` + `pipeline_health` writes, `compare_table` semantics (`SELECT sum(...) FROM (<export SQL>)` vs BQ), idempotency via parity.

### 6.1 Individual CronJobs

All share:

```yaml
timeZone: UTC
concurrencyPolicy: Forbid
startingDeadlineSeconds: 300
successfulJobsHistoryLimit: 3
failedJobsHistoryLimit: 3
backoffLimit: 3
activeDeadlineSeconds: <per-job>
ttlSecondsAfterFinished: 86400
restartPolicy: OnFailure
```

**`wikistream-export`** `0 * * * *` `activeDeadlineSeconds: 900` — CH query → JSONL to `/tmp` → GCS → BQ (4 tables + `export_runs`). Skip empty window.

**`wikistream-parity`** `5 * * * *` — serializes after export via schedule (no K8s dependency). On drift `exit 1` → `pipeline_health value 0.0`.

**`wikistream-gx`** `30 * * * *` `activeDeadlineSeconds: 1800` — `python -m gx.suite` with `CLICKHOUSE_HOST=wikistream-clickhouse`.

**`wikistream-backup`** `20 * * * *` `backoffLimit: 2` `activeDeadlineSeconds: 600` — `BACKUP TABLE default.raw_events TO S3('gs://wikistream-505003-ch-backups/{RUN_ID}/', ...)` via `clickhouse-connect`. Proves K8s-orchestrated backup of VM-hosted CH — stronger than leaving on systemd.

# ponytail: BACKUP DATABASE + weekly RESTORE verify when >1 table needs atomicity or audit requires proof

---

## 7. Platform Boundary — Terraform vs K8s Manifests

**Terraform owns platform** (cluster, VPC/firewall, IAM/SA+WI, GCS buckets, Artifact Registry, Secret Manager, `ResourceQuota`/`LimitRange`/`NetworkPolicy`). **Workloads** (Deployment + 4 CronJobs + Service/Endpoints + PodMonitoring) are plain YAML in `infra/k8s/` applied via `kubectl apply -k` / `kustomize`.

Provider:

```hcl
provider "kubernetes" {
  host                   = "https://${module.gke.endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(module.gke.ca_certificate)
}
```

Deploy: `kubectl apply -k infra/k8s/` (or `kustomize build | kubectl apply -f -`). Image digest updated via `kustomize edit set image consumer=<digest>` — no `terraform apply` for rollouts, so `plan` stays platform-only.

# ponytail: ArgoCD Application / Helm chart + ESO ExternalSecret when team >3 or you have >1 cluster; until then kubernetes_manifest for workloads conflates platform + app lifecycle and stores secrets in state

# ponytail: External Secrets Operator (ESO) replaces kubernetes_secret(sensitive=true) when you need secret rotation without TF state — deferred for single-cluster CV

Workloads as `kubernetes_manifest` per resource is the anti-pattern v2 fixed with ArgoCD — v3 keeps the fix but via the lazier `kubectl apply -k` instead of a full GitOps install.

---

## 8. Cost — £70/mo Ceiling

| Component | Request | Hours/mo | Cost (Autopilot) |
| ----------- | --------- | ---------- | ------------------ |
| Consumer Deployment | 250m / 512Mi | 730 (always) | ~$18-22 |
| Export CronJob | 500m / 1Gi | ~60h | ~$2 |
| Parity CronJob | 500m / 512Mi | ~60h | ~$1.50 |
| GX CronJob | 500m / 1Gi | ~60h | ~$2-3 |
| Backup CronJob | 500m / 512Mi | ~60h | ~$1.50 |
| GMP ingestion | | | ~$2-3 |
| **Total K8s delta** | | | **~$27-33 (~£21-26)** |
| VM `e2-medium` + PDs (existing) | | | ~$25-30 |
| **Combined** | | | **~£42-50/mo → under £70** |

Zonal Autopilot control plane is $0.10/hr (~$74/mo); each bounded build window is plausibly absorbed by that month's $74.40 Autopilot credit (renews monthly) — sequential per-project ephemeral likely cheaper than shared (no idle-gap billing between projects). After credit, combined ~£92-100/mo — still defensible as one-time CV tax; ceiling holds while credit lasts. No Memorystore/Cloud SQL.

Billing guard: `google_billing_budget` with `threshold_rules 0.5/0.8/1.0` → email.

---

## 9. Networking, IAM, Secrets

### Networking

- No Ingress — SSE consumer is egress-only; Grafana stays on VM:3000.
- Pod → `wikistream-vm:8123` via **Service + Endpoints** (not bare hostname):

```yaml
# infra/k8s/service-clickhouse.yaml
apiVersion: v1
kind: Service
metadata: { name: wikistream-clickhouse, namespace: wikistream }
spec: { ports: [{ name: http, port: 8123, protocol: TCP }] }
---
apiVersion: v1
kind: Endpoints
metadata: { name: wikistream-clickhouse, namespace: wikistream }
subsets:
- addresses: [{ ip: "10.128.0.XX" }] # module.compute.internal_ip via kustomize
  ports: [{ name: http, port: 8123 }]
```

- Firewall: `sourceRanges = [module.gke.cluster_ipv4_cidr]` → `targetTags=["wikistream-vm"]` port 8123 (verify pod CIDR covers pod→VM).
- NetworkPolicy (Autopilot enforces via Calico):

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: { name: default-deny, namespace: wikistream }
spec: { podSelector: {}, policyTypes: [Ingress, Egress] }
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: { name: allow-egress, namespace: wikistream }
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress:
  - to: [{ ipBlock: { cidr: "10.128.0.XX/32" } }] # CH VM
    ports: [{ protocol: TCP, port: 8123 }]
  - to: [{ ipBlock: { cidr: "0.0.0.0/0" } }]
    ports: [{ protocol: TCP, port: 443 }] # Wikimedia SSE + GCS/BQ/AR
  - to: [{ namespaceSelector: { matchLabels: { name: kube-system } } }]
    ports: [{ protocol: UDP, port: 53 }, { protocol: TCP, port: 53 }]
```

### Namespace hardening

```yaml
apiVersion: v1
kind: ResourceQuota
metadata: { name: wikistream-quota, namespace: wikistream }
spec: { hard: { requests.cpu: "2", # max concurrent requests 1.25 (consumer 0.25 + 2 jobs 0.5) → quota 2 leaves 0.75 headroom
    requests.memory: "4Gi", limits.cpu: "4", limits.memory: "8Gi", pods: "10" } }
---
apiVersion: v1
kind: LimitRange
metadata: { name: wikistream-limits, namespace: wikistream }
spec: { limits: [{ type: Container, default: { cpu: "500m", memory: "512Mi" }, defaultRequest: { cpu: "100m", memory: "128Mi" } }] }
```

### IAM

- `wikistream-consumer` SA: `secretmanager.secretAccessor` on `clickhouse-password`, `storage.objectCreator+objectViewer` on consumer-state bucket, `artifactregistry.reader` is on node SA.
- `wikistream-jobs` SA: `secretmanager.secretAccessor`, `storage.objectCreator` on `bq-staging` + backup bucket, `bigquery.dataEditor` + `bigquery.jobUser`.
- WI bindings: `iam.workloadIdentityUser` for `system:serviceaccount:wikistream:<ksa>`.

### Secrets

TF creates `google_secret_manager_secret` + `google_secret_manager_secret_version` (value from var, never output). K8s Secret via:

```hcl
resource "kubernetes_secret" "wikistream" {
  metadata: { name = "wikistream-secrets", namespace = "wikistream" }
  data = { clickhouse-password = sensitive(data.google_secret_manager_secret_version.password.secret_data) }
  # ponytail: ESO ExternalSecret when >1 cluster or rotation needed — this keeps value out of plan output but still in TF state (acceptable for single-cluster CV)
}
```

Interview answer: sensitive + WI; ESO is the upgrade, not the baseline for a DE demo.

### GCS race

Singleton is safe: `RollingUpdate maxSurge 1 + maxUnavailable 0` means at most 2 pods briefly; `save_state()` uses `if_generation_match` retry (§4.3). No lock needed.

### Endpoints injection

```yaml
# infra/k8s/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources: [namespace.yaml, serviceaccounts.yaml, deployment.yaml, service-clickhouse.yaml, networkpolicy.yaml, resourcequota.yaml, limitrange.yaml, cronjobs.yaml, podmonitoring.yaml]
patches:
  - target: {kind: Endpoints, name: wikistream-clickhouse}
    patch: |-
      - op: replace
        path: /subsets/0/addresses/0/ip
        value: "${VM_INTERNAL_IP}" # from terraform output module.compute.internal_ip
```

Verify `cluster_ipv4_cidr` is `google_container_cluster.wikistream.ip_allocation_policy[0].cluster_ipv4_cidr_block`.

---

## 10. CI/CD (GHA → AR → kubectl)

Actual repo has three workflows — surgical fix extends `apply.yml`, not a new file:

- `apply.yml` (**extend, not new file**) on `push: main` already does WIF + `docker/build-push-action` consumer+gx + `terraform apply`:
  - **add:** build-push `warehouse/Dockerfile.jobs` → `jobs:<sha>@sha256:digest`
  - **add:** `aquasecurity/trivy-action` HIGH/CRITICAL on both images
  - **add:** `kustomize edit set image` + `kubectl apply -k infra/k8s/` (digest-pinned)
  - **fix:** `zone: us-east1-b` → `us-central1-a` (matches VM)
  - keep `terraform plan -out=plan.out` → artifact → apply on approval already in file.
- `plan.yml`: no change (PR `terraform plan` only).
- `ci.yml`: append `src.state_gcs` + `src.healthz` to `BUSINESS_CRITICAL_MODULES` env; keep compose-smoke.

# ponytail: split apply.yml → k8s.yml when team>3 or plan matrix gets unwieldy

```yaml
# .github/workflows/apply.yml additions (sketch)
# - uses: docker/build-push-action@v6, with: push: true  # + warehouse/Dockerfile.jobs → jobs:<sha>@sha256:digest
# - uses: aquasecurity/trivy-action@0.24.0  # HIGH/CRITICAL fail on consumer + jobs
# - run: terraform plan -out=plan.out  # artifact → apply on approval (already in file)
# - run: kustomize edit set image consumer=$CONSUMER_DIGEST jobs=$JOBS_DIGEST && kubectl apply -k infra/k8s/
```

# ponytail: cosign + Binary Authorization + SBOM + checkov/tfsec when compliance requires

# ponytail: checkov/tfsec/tflint when repo goes public — not needed for private CV demo

---

## 11. Observability (GMP required)

- **Logs:** Autopilot ships stdout → Cloud Logging. No sidecar.
- **Metrics:** GMP `PodMonitoring` scrapes `consumer:8080/metrics` (one Gauge `wikistream_consumer_state_age_seconds` set on each `save_state()`).

```yaml
apiVersion: monitoring.googleapis.com/v1
kind: PodMonitoring
metadata: { name: wikistream-consumer, namespace: wikistream }
spec:
  selector: { matchLabels: { app.kubernetes.io/name: wikistream-consumer } }
  endpoints: [{ port: healthz, path: /metrics, interval: 30s }]
```

- **Alerts decoupled from `pipeline_health`:** `pipeline_health` is written by jobs — if wedged it can't write `0`. So alerts are log-based + GMP, not `pipeline_health`-only:

```hcl
resource "google_logging_metric" "cronjob_failed" {
  name   = "wikistream-cronjob-failed"
  filter = "resource.type=\"k8s_container\" AND jsonPayload.reason=\"BackoffLimitExceeded\""
  metric_descriptor { metric_kind = DELTA, value_type = INT64 }
}
resource "google_monitoring_alert_policy" "cronjob_failed" {
  display_name = "WikiStream CronJob failed"
  combiner = "OR"
  conditions { display_name = "CronJobFailed log-based"
    condition_threshold {
      filter = "metric.type=\"logging.googleapis.com/user/wikistream-cronjob-failed\""
      comparison = "COMPARISON_GT"; threshold_value = 0; duration = "300s"
    }
  }
  notification_channels = [google_monitoring_notification_channel.email.id]
}
```

Grafana stays on VM for v1 (no Ingress). Health is `httpGet /healthz:8080`.

---

## 12. Implementation Order

1. **Bootstrap** — `google_storage_bucket.consumer_state` + backup bucket + `google_billing_budget`.
2. **Cluster** — `module.gke` (zonal) + namespace + `ResourceQuota`/`LimitRange` + `NetworkPolicy` + WI bindings + `kubernetes_secret`. `terraform apply`.
3. **Manifests** — write `infra/k8s/` (`namespace.yaml`, `serviceaccounts.yaml`, `deployment.yaml`, `service-clickhouse.yaml`, `networkpolicy.yaml`, `resourcequota.yaml`, `limitrange.yaml`, `cronjobs.yaml`, `podmonitoring.yaml` + `kustomization.yaml` §9).
4. **State + healthz code** — `state_gcs.py` + `healthz.py`, flip `consumer.py` to `STATE_BACKEND=gcs`. Test locally with ADC.
5. **CI** — extend `apply.yml` (add jobs image + Trivy + `kubectl apply -k`), fix zone to `us-central1-a`, extend `ci.yml` `BUSINESS_CRITICAL_MODULES`.
6. **Consumer** — `kubectl apply -k infra/k8s/`; verify resume across `kubectl rollout restart`.
7. **CronJobs** — export → parity → gx → backup one at a time; verify logs + BQ `export_runs` + `pipeline_health` + `timeZone`/`backoffLimit`. Cutover verification before disabling timers: confirm all layers agree (CronJob logs + BQ `export_runs` + `pipeline_health` + GCS backup object) via plain Python script reusing `clickhouse-connect`/GCS libs already in repo — explicitly NOT Go (forcing Go = manufactured signal). Then `systemctl disable --now wikistream-export.timer wikistream-parity.timer wikistream-gx.timer wikistream-backup.timer` and set `enable_timers=false` in `infra/main/templates/startup.sh` so rebuild doesn't re-enable — don't delete timers.
8. **Observability** — GMP `PodMonitoring` + `logBasedMetric` + alert, force failure to verify.
9. **Evidence** — `kubectl get cronjobs,pods,networkpolicy`, BQ `SELECT * FROM export_runs LIMIT 5`, `trivy image` output, Grafana. File it: terminal outputs + GCP Console screenshots → plan appendix; one-line descriptions → CV bullets.

---

## 13. Rejected Alternatives

| Rejected | Why |
| ---------- | ----- |
| KEDA `ScaledObject` | Duplicates SSE ingress; dedup per-pod; needs shared queue (contradicts no-broker). |
| HPA on consumer | Singleton; scaling meaningless. |
| Pub/Sub before CH | Contradicts no-broker; cost + latency for keyword alone. |
| ClickHouse on K8s StatefulSet | Violates don't-K8s the stateful tier; PD durable stays VM-native. |
| ConfigMap for SQL | Extra object + rollout; baking into image is more immutable. |
| `kubernetes_manifest` per workload | Conflates platform + app; replaced by `kubectl apply -k` (ArgoCD when team>3). |
| ESO / ArgoCD / Helm chart for v1 | Correct for platform teams, overkill for single-cluster DE demo — `kubernetes_secret` + `kubectl -k` is defensible; note upgrade path. |
| cosign / Binary Authorization / SBOM | Compliance theatre for 1-person repo; SHA pin + Trivy proves you considered supply chain. |
| PDB / topologySpread / priorityClass / preStop | Correct for multi-replica; cargo-culting on a singleton. |
| `BACKUP DATABASE` + weekly restore-verify | Good idea, bad CV ROI; `BACKUP TABLE` proves K8s→VM backup without audit overhead. |
| Regional cluster | HA control plane is free on Autopilot but implies multi-zone app — this app is zonal (VM is zonal). |
| Bare hostname `wikistream-vm` | DNS coupling; replaced by Service + Endpoints. |

---

## 14. Interview Narrative (one-liners)

- "Ephemeral zonal Autopilot in same zone as the VM (us-central1-a); owned + torn down with WikiStream — sequential lifecycle, no cross-project reference."
- "Consumer is `replicas: 1` — singleton SSE cursor; scaling duplicates the stream. Zero-loss resume via GCS `if_generation_match`, not a PV."
- "Four systemd timers became four `CronJob`s (export/parity/gx/backup); bash `docker exec` became Python `clickhouse-connect` with SQL baked into the image. Every CronJob has `timeZone: UTC` + `startingDeadlineSeconds` + `backoffLimit`."
- "Platform via Terraform, workloads via `kubectl apply -k` — `plan.out` shows platform drift. Images SHA-pinned, Trivy-scanned."
- "Secrets via `kubernetes_secret` sensitive + WI; ESO is the noted upgrade when you have >1 cluster. NetworkPolicy default-deny, GMP required, alerts decoupled from `pipeline_health`."
- "ClickHouse stays on its VM with durable PD — K8s orchestrates `BACKUP TABLE` over Service `wikistream-clickhouse`."
- "Health is `httpGet /healthz:8080` reusing heartbeat, not `exec uv run`. One `securityContext` block, not a checklist."

---

## 15. Open Items

- [x] Secret mount — **decided v3: `kubernetes_secret` sensitive + WI** (ponytail ESO when >1 cluster).
- [x] GitOps — **decided v3: `kubectl apply -k infra/k8s/`** (ponytail ArgoCD/Helm when team>3).
- [x] Backup target — **decided: `BACKUP TABLE default.raw_events` to `wikistream-505003-ch-backups`** (ponytail `DATABASE` + verify when audit requires).
- [x] GH workflows — decided: extend apply.yml (not new k8s.yml), add jobs image + Trivy + kubectl, fix zone us-east1-b→us-central1-a; ci.yml add state_gcs+healthz (ponytail split when team>3)
- [ ] Exact VM internal IP for Endpoints — injected via kustomize from `module.compute.internal_ip`; verify `cluster_ipv4_cidr` covers pod→VM.
- [ ] `boot.sh` timer disable — `systemctl disable` timers after K8s parity (don't delete).
- [ ] Grafana on K8s — phased later, tracked in §11.

---

## 16. Changelog

| Version | Date | Change |
| --------- | ------ | -------- |
| v1 | 2026-09-02 | Initial grilled plan: zonal Autopilot, singleton+GCS, 4 CronJobs, bake SQL, bash→Python, TF kubernetes_manifest |
| v2 | 2026-09-02 | Tier 1+2 closures (736 lines): regional, ESO, ArgoCD/Helm, PDB/topologySpread/priorityClass/preStop, hardened securityContext, httpGet healthz, Service+Endpoints, NetworkPolicy, CronJob timeZone/backoff, GMP logBasedMetric decoupled, digest+Trivy+cosign/BinaryAuth/SBOM, billing budget |
| **v3 slim** | **2026-09-02** | **Defensibility cut per review — removed Platform Engineer signals that hurt DE signal:** |
| | | `cosign`+Binary Authorization+SBOM → SHA pin + one Trivy line (ponytail when compliance requires) |
| | | `ArgoCD Application`+`Chart.yaml` → `kubectl apply -k infra/k8s/` (ponytail ArgoCD when team>3) |
| | | `PDB`+`topologySpreadConstraints`+`priorityClassName`+`preStop sleep 5` → deleted (cargo-culting on singleton) |
| | | `runAsUser 65532`+`readOnlyRootFilesystem`+`drop ALL`+`seccomp`+`USER 65532` → one `securityContext` block |
| | | `wikistream-restore-verify` weekly + `BACKUP DATABASE` → `BACKUP TABLE` + ponytail comment |
| | | `Regional` → `Zonal us-central1-a` (matches VM, cheaper to defend) |
| | | Collapsed §§7/9/11 Helm/TF/ArgoCD into one **Platform boundary** paragraph; ESO → `kubernetes_secret` sensitive + ponytail note |
| | | Kept defensible core: `GCS if_generation_match` + `httpGet /healthz`+`terminationGracePeriod:30` + `timeZone`/`startingDeadline`/`backoffLimit` + `Service/Endpoints`+`NetworkPolicy`+`ResourceQuota`+`LimitRange`+`PodMonitoring`+log-based alerts |
| v4 | 2026-09-06 | Shared → ephemeral: teardown per project, sequential likely cheaper (no idle-gap billing; each bounded window plausibly absorbed by that month's $74.40 credit), no cross-project cluster reference. |

*Reference: v2 remains as architecture reference in git history; ADRs 0012-0014 reflect v2 intent — v3 is the interview-defensible execution.*
