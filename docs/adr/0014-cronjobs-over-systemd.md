# ADR 0014: Systemd Timers → Kubernetes CronJobs (Bake SQL, Rewrite Bash)

**Status:** Accepted — v4 ephemeral per phase-6-kubernetes-integration.md §16 (2026-09-06; v4 changes cluster ownership only, decision stands) — v2 intent preserved as ponytail upgrade path
**Date:** 2026-09-02

## Context

VM has 4 `systemd` timers (`boot.sh:54-60`): `export *:00`, `parity *:05`, `backup *:20`, `gx *:30`. `export.sh`/`parity.sh` are bash that `docker exec clickhouse-client` + `gcloud storage cp` + `bq load`; `gx/suite.py` is Python already. v1 left gaps: `BACKUP TABLE` not `DATABASE`, missing `timeZone`/`startingDeadlineSeconds`/`backoffLimit`, no restore-verify, workload via `kubernetes_manifest`, GMP optional, `pipeline_health` circular alert.

Goal: make batch surface K8s-native without inventing a bottleneck. Budget £70, Terraform owns platform, SQL must be available at runtime. Helm/ArgoCD for workloads (v2).

## Decision

- All 4 timers become **`CronJob`** (`concurrencyPolicy: Forbid`, `restartPolicy: OnFailure`, `ttlSecondsAfterFinished: 86400`, plus **v2 hardening**: `timeZone: UTC`, `startingDeadlineSeconds: 300`, `backoffLimit: 3` (backup `2`, restore-verify `1`), `activeDeadlineSeconds: 900` export/parity, `1800` gx, `600` backup, `1800` restore-verify, `priorityClassName: wikistream-jobs` (500), `securityContext` harden same as consumer), **plus 5th job `wikistream-restore-verify` weekly** — total 5 CronJobs.
- **Bake** `warehouse/sql/*.sql` + `warehouse/schemas/*.json` into job images (`COPY warehouse`), not `ConfigMap` — fewer objects, immutable artifact. `warehouse/Dockerfile.jobs` now pins `uv` + base SHA, sets `USER 65532`, `readOnlyRootFilesystem`.
- **Rewrite** `export.sh`/`parity.sh`/`backup.sh` as Python (`warehouse/export.py` etc.) using `clickhouse-connect`, `google-cloud-storage`, `google-cloud-bigquery`. Keep window math, `RUN_ID`, `export_runs`/`pipeline_health` writes, and `compare_table` SUM semantics verbatim. CH host is `wikistream-clickhouse` Service (§9).
- **Backup is `BACKUP DATABASE default` to `gs://wikistream-505003-ch-backups/`** (not single table) — covers MVs + dead_letter; `BACKUP TABLE raw_events` is the incremental fallback. Weekly `restore-verify` does `RESTORE DATABASE default AS _restore_verify FROM S3(...)`, checksum vs live, writes `pipeline_health` `restore_verify 0/1`, then `DROP DATABASE`.
- **Deliver via Helm chart** `infra/k8s/chart` (Chart.yaml, values.yaml with digest-pinned images, templates for 5 CronJobs + Service/Endpoints + NetworkPolicy + PDB/PriorityClass + ResourceQuota/LimitRange + ExternalSecret + PodMonitoring) deployed by **ArgoCD `Application` (preferred) or `helm_release` interim** — not `kubernetes_manifest` per workload. Images are digest-pinned, Trivy-scanned, cosigned (Binary Authorization), SBOM published. `terraform plan -out=plan.out` + checkov/tfsec/tflint, `plan.out` as CI artifact.
- **Observability decoupled:** GMP `PodMonitoring` required; `google_logging_metric` + `google_monitoring_alert_policy` for `CronJobFailed` (BackoffLimitExceeded) and `ConsumerLag` (`state_age >600s`) — not `pipeline_health`-only (circular: a wedged job cannot write `value 0`). `pipeline_health` stays as Grafana panel.

## Alternatives

- Keep on systemd — rejected: leaves half the batch surface off K8s; story is weaker.
- ConfigMap for SQL — rejected: extra object + rollout on SQL change; `COPY` is one fewer moving part.
- Keep bash + `clickhouse-client` in job image — rejected: heavier image, still needs SDK for GCS/BQ.
- `kubectl apply` in GHA — rejected: breaks "everything is Terraform" principle.
- `kubernetes_manifest` for workloads (v1) — rejected in v2: secret in state, no reconciliation; replaced by Helm/ArgoCD.
- `BACKUP TABLE` only — rejected in v2 target: use `DATABASE` so MVs are covered.
- No restore-verify — rejected: a backup never restored is hope, not guarantee.

## Consequences

- One `warehouse/Dockerfile.jobs` builds all batch images; `gx/suite.py` reused as-is; weekly verify proves RTO.
- `boot.sh` timers remain on VM but `systemctl disable` after K8s green; rebuild stays clean.
- CI enforces supply chain (Trivy + cosign + SBOM + SHA pin).
- Interview: "Four systemd timers became five CronJobs; bash docker exec became Python clickhouse-connect. `BACKUP DATABASE` with weekly `RESTORE` verify."

## Amendment v3 slim (2026-09-02) — execution vs intent

v2 intent is preserved as the platform-team upgrade path; v3 slim is the DE-CV execution. This ADR's decision still stands, but the following v2 specifics are deferred to ponytail comments in the Phase 6 plan (not executed in v3):

- 5 CronJobs (incl. `wikistream-restore-verify` weekly) → 4 CronJobs (`export`, `parity`, `gx`, `backup`; restore-verify deferred — see plan §6 ponytail)
- `BACKUP DATABASE default` + `RESTORE DATABASE ... AS _restore_verify` weekly → `BACKUP TABLE default.raw_events` (atomic DATABASE + verify when >1 table needs atomicity or audit — plan §6/§13)
- Helm chart `infra/k8s/chart` + ArgoCD `Application` + `priorityClassName` + full `securityContext` + `Trivy+cosign+SBOM+BinaryAuth` + `terraform plan -out=plan.out`+checkov → `kubectl apply -k` + `kustomization.yaml` + single `securityContext` block + SHA pin+Trivy only (ponytail checkov/tfsec when public — plan §7/§10)
- Keep: bake SQL via `COPY warehouse/sql` (not ConfigMap), rewrite bash→Python via `clickhouse-connect`, all CronJobs `timeZone:UTC`+`startingDeadlineSeconds:300`+`backoffLimit`+`activeDeadlineSeconds`+`ttlSecondsAfterFinished`, `NetworkPolicy` + `ResourceQuota` + decoupled `logBasedMetric` alerts.
- Interview answer remains honest; upgrade path is noted not ignored. See phase-6-kubernetes-integration.md §16 changelog and ponytail comments at §4.4/§7/§9/§10/§11.

## References

- `warehouse/export.sh`, `parity.sh`, `gx/suite.py`, `docker-compose.yml:87-103`, Phase 6 plan §5-§7, §9, §11.
