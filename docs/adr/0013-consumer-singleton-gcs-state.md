# ADR 0013: Consumer as Singleton Deployment with GCS State

**Status:** Accepted — v4 ephemeral per phase-6-kubernetes-integration.md §16 (2026-09-06; v4 changes cluster ownership only, decision stands) — v2 intent preserved as ponytail upgrade path
**Date:** 2026-09-02

## Context

`consumer/src/consumer.py` tails Wikimedia SSE (one cursor `Last-Event-ID`, composite Kafka IDs via `_cursor_ts`/`_max_id`). `EventBatcher` batches 1000/5s to ClickHouse. State lives in `/state/consumer_state.json` on VM PD. Dedup ring is in-memory 50k (~20 min at 44 ev/s).

On K8s each replica would open its own SSE connection and duplicate events. Per-pod dedup cannot dedup across pods. KEDA on "queue depth" would increase duplicates, not throughput. v1 left gaps: `exec` probes, missing securityContext, TF-state secrets, bare CH hostname, GCS race undocumented.

## Decision

- `Deployment` `replicas: 1`, `revisionHistoryLimit: 5`, `RollingUpdate` (`maxUnavailable: 0, maxSurge: 1`), `terminationGracePeriodSeconds: 30`, `topologySpreadConstraints` (hostname, ScheduleAnyway), `priorityClassName: wikistream-consumer` (1000), `PodDisruptionBudget` `maxUnavailable: 0`.
- **State moves to GCS** `gs://wikistream-505003-consumer-state/consumer_state.json` via `google-cloud-storage` (`state_gcs.py`). `STATE_BACKEND=gcs` when `STATE_BUCKET` set. Debounced 2s save kept, now with **`if_generation_match` + retry** to close the RollingUpdate race (`maxSurge 1` + `preStop sleep 5` gives the old pod a drain window; generation guard catches the brief 2-pod overlap). Bucket hygiene: `uniform_bucket_level_access`, `versioning`, `soft_delete 7d`, `lifecycle` (abort multipart 7d, delete noncurrent 30d), CMEK optional follow-up. IAM: `storage.objectCreator + objectViewer` (not `objectAdmin`).
- **No PVC.** Service `wikistream-clickhouse` (`Service` + `Endpoints` with VM internal IP) is the CH target, not bare `wikistream-vm` hostname. Firewall uses `module.gke.cluster_ipv4_cidr`.
- **Security hardened:** `securityContext: runAsNonRoot, runAsUser 65532, seccomp RuntimeDefault`; container `allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities.drop: [ALL]`, `USER 65532` in Dockerfile (base + `uv` SHA-pinned). `emptyDir` `/tmp` for `readOnlyRootFilesystem`.
- **Probes are `httpGet /healthz:8080`**, not `exec uv run`. New `src/healthz.py` stdlib `http.server` on 8080 reusing `healthcheck.py:is_fresh` (`HEALTH_STALE_SECONDS=300`) — 200 fresh, 500 stale. Started as asyncio task in `consumer.py:main` alongside `heartbeat_loop`. `preStop: sleep 5` for GCS drain.
- **Secrets via ESO:** `ExternalSecret wikistream-secrets` + `SecretStore` (GCP SM via WI); `secretKeyRef` unchanged for pod but TF never sees value.
- **Observability:** GMP `PodMonitoring` scrapes `:8080/metrics` (`wikistream_consumer_state_age_seconds` gauge); alerts decoupled from `pipeline_health` via `logBasedMetric` + `alert_policy` (see ADR-0014).
- Standard labels `app.kubernetes.io/*` on every object.

## Alternatives

- KEDA `ScaledObject` on custom Prometheus `batch_queue_depth` — rejected: duplicates ingress, no backpressure relieved, contradicts singleton.
- HPA on CPU — rejected: singleton.
- PVC `ReadWriteOnce` — rejected: pins pod to node, adds `prevent_destroy` disk; GCS already in stack and survives reschedule.
- Stateless + replay gap — rejected: loses zero-loss SIGTERM flush guarantee (§2.2, `consumer.py:365-384`).
- `exec` probes — rejected in v2: `uv` overhead, exec failure modes, not HTTP-native; `httpGet` is industry standard.
- `kubernetes_secret` from TF — rejected in v2: value in state.

## Consequences

- Zero-loss resume across rollout/node recycle (GCS durable, single writer, generation guard).
- Interview answer: "Scaling the SSE consumer would duplicate the stream — it's a singleton by design. PDB + priorityClass protect it; `preStop` + `if_generation_match` close the GCS race."
- Cost: one small GCS object, `objectCreator+objectViewer` via WI. No PVC cost.

## Amendment v3 slim (2026-09-02) — execution vs intent

v2 intent is preserved as the platform-team upgrade path; v3 slim is the DE-CV execution. This ADR's decision still stands, but the following v2 specifics are deferred to ponytail comments in the Phase 6 plan (not executed in v3):

- `topologySpreadConstraints`, `priorityClassName: wikistream-consumer`, `PodDisruptionBudget maxUnavailable:0`, `preStop sleep 5` → deleted (cargo-culting on singleton `replicas:1`; see plan §4.4 ponytail)
- `securityContext` full hardening (`runAsUser 65532`, `readOnlyRootFilesystem:true`, `capabilities.drop:[ALL]`, `seccomp`, `USER 65532`, `emptyDir /tmp`, base+uv SHA pin) → slimmed to one `securityContext: {runAsNonRoot:true, runAsUser:1000, allowPrivilegeEscalation:false}` (plan §4.4 ponytail for full when compliance requires)
- `cosign`/`Binary Authorization`/`SBOM` + ESO → SHA pin + one Trivy scan + `kubernetes_secret sensitive` (ponytail when compliance/rotation — plan §9/§10)
- Keep: `replicas:1` + `terminationGracePeriod:30` + `httpGet /healthz:8080` + `if_generation_match` GCS state + `Service wikistream-clickhouse`+`Endpoints`+`NetworkPolicy`+`ResourceQuota`+`GMP PodMonitoring` log alerts — all retained in v3.
- Interview answer remains honest; upgrade path is noted not ignored. See phase-6-kubernetes-integration.md §16 changelog and ponytail comments at §4.4/§7/§9/§10/§11.

## References

- `consumer.py:88-145, 155-384`, `batcher.py:12-139`, `healthcheck.py:35-52`, `heartbeat.py`, Phase 6 plan §4, §9, §11.
