# 5. Kubernetes extensions to all 3 CVs — REVISED 2026-09-02 (post-WikiStream v3 slim grill)

> Superseded 2026-09-06: shared → ephemeral. Retained for per-project details only.

## Kubernetes platform — per-project ephemeral GKE Autopilot, three project-native integrations (not LAAD-anchored — superseded; shared superseded 2026-09-06)

**Per-project ephemeral zonal Autopilot — each project owns + tears down its own cluster in its own GCP project. No cross-project reference (shared superseded 2026-09-06: sequential build→evidence→teardown breaks shared reference by design).** Autopilot is **not** on the Always-Free tier (that's Standard zonal only); Autopilot has a flat $0.10/hr management fee (~$74/mo) — each bounded build window plausibly absorbed by that month's $74.40 Autopilot free credit (renews monthly); sequential ephemeral likely cheaper, no idle-gap billing between projects. Past credit, the K8s delta is ~$27-33/mo (§8 v3). Zonal is deliberate: matches `wikistream-vm` zone `us-central1-a` → pod→VM internal IP without cross-VPC; regional would be HA theatre for a singleton that writes to a zonal VM.

**Order: cluster bootstrap → WikiStream singleton+CronJobs → DevSync deep → SWE-Qwen Jobs → GPU demo, last (each with own cluster lifecycle — sequential build→evidence→teardown).**

### WikiStream (DE CV) — v3 slim defensible

* **Containerize the existing consumer as-is — no split, no broker.** The "no broker" finding stays intact; Pub/Sub would contradict it. Image is digest-pinned `consumer:<sha>@sha256:<digest>` + one Trivy scan (ponytail: cosign/BinaryAuth/SBOM when compliance requires).
* **`replicas: 1` singleton — KEDA/HPA explicitly rejected.** SSE stream is a single `Last-Event-ID` cursor; each replica duplicates ingress, dedup ring (50k) is per-pod. KEDA `ScaledObject` on custom Prometheus metric (batch queue depth/events/sec) was grilled and rejected — it would need a queue/broker to be honest. Interview answer: singleton + `terminationGracePeriodSeconds: 30` + `httpGet /healthz:8080`.
* **State: GCS not PD.** `gs://wikistream-505003-consumer-state/consumer_state.json` with `if_generation_match` + retry (`consumer/src/state_gcs.py`). Replaces `/state` PD which would empty on reschedule. Bucket: `uniform_bucket_level_access` + `versioning` + `lifecycle`, least privilege `objectCreator+objectViewer`.
* **ClickHouse stays on its VM** — consistent "don't K8s the stateful tier" (applied here and to DevSync below). Accessed via `Service wikistream-clickhouse` + `Endpoints` (static internal IP), firewall `cluster_ipv4_cidr` → `:8123`, `NetworkPolicy` default-deny.
* **4 systemd timers → 4 K8s `CronJob`s** (export `0 * * * *`, parity `5 * * * *`, gx `30 * * * *`, backup `20 * * * *`), each `timeZone: UTC` + `concurrencyPolicy: Forbid` + `startingDeadlineSeconds: 300` + `backoffLimit` + `activeDeadlineSeconds` + `ttlSecondsAfterFinished`. SQL/schemas baked into job image (`COPY warehouse/sql` — not ConfigMap), bash `docker exec` → Python `clickhouse-connect`.
* **Backup: `BACKUP TABLE default.raw_events` to `gs://wikistream-505003-ch-backups/` via S3 disk over HTTP** — K8s CronJob orchestrates backup of VM-hosted CH. Ponytail: `BACKUP DATABASE` + weekly `RESTORE` verify when audit requires.
* **Platform boundary:** Terraform for platform (cluster, VPC, IAM/WI, GCS, AR, Secret Manager, `ResourceQuota`/`LimitRange`/`NetworkPolicy`); workloads via `kubectl apply -k infra/k8s/` (ponytail: ArgoCD/Helm chart + ESO `ExternalSecret` when team >3 or >1 cluster). Secrets via `kubernetes_secret` `sensitive = true` + WI (ponytail: ESO).
* **Observability:** Cloud Logging automatic + GMP `PodMonitoring` required + `logBasedMetric`/`alert_policy` for `CronJobFailed` decoupled from `pipeline_health` (jobs can't write `value 0` if wedged).
* Cost: total ~£42-50/mo → under £70.

### DevSync (SWE CV)

* First GCP Terraform for DevSync: GKE Ingress + ManagedCertificate TLS, Artifact Registry, `google-github-actions/auth` WIF replacing AWS creds (mirrors WikiStream's WIF pattern).
* **Self-host Postgres + Redis on a small Compute Engine VM instead of Cloud SQL/Memorystore.** Same "stateful tier off K8s, on a VM" principle as WikiStream's ClickHouse — Memorystore has no free tier (~$36/mo min), Cloud SQL charges idle-IP fee (~$9.57/mo) before compute.
* Socket.IO Redis adapter → rooms survive >1 replica — real horizontal-signal.
* HPA on Socket.IO connection metrics (not CPU) — owns the scaling story KEDA couldn't own in WikiStream.
* k6 load testing (Tier 2) runs **AFTER** this migration — testing HPA-scaled GKE, not fixed-replica ECS Fargate.
* OPEN: is DevSync's AWS deployment live or torn down? Affects migration vs fresh deploy (same question as LAAD).

### SWE-Qwen (AI CV)

* Eval harness → GKE batch `Jobs` (CPU-only, own per-project cluster) — lower priority than it looks, Modal already does this well; mainly closes "K8s Jobs" pattern for this CV.
* Serving stays on Modal — proven, not replaced.
* GPU KServe/vLLM = **canary/traffic-split demo, explicitly build → run → capture evidence → teardown, done last.** This is the bounded GPU-cost answer — exposure by design, not by monitoring.
* GPU demo is **last in the order** for cost reasons — everything else proves K8s without GPU spend.

### NOT SCOPED YET

Exact namespace/RBAC isolation per project (no shared tenants); DevSync self-hosted PG/Redis VM sizing; SWE-Qwen canary traffic-split mechanics (KServe `InferenceService` canary vs Gateway `HTTPRoute` weight). WikiStream's Prometheus metric/threshold for autoscaling is **closed: none — singleton**; DevSync's HPA metric is the open one.
