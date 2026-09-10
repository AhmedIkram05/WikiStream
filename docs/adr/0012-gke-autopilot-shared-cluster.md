# ADR 0012: GKE Autopilot Ephemeral Clusters, Per-Project (shared superseded v4)

**Status:** Accepted — v4 ephemeral (shared superseded)
**Date:** 2026-09-02
**Deciders:** Ahmed, grilling session + Tier 1+2 closure
**Supersedes:** v1 (zonal, `kubernetes_manifest` for workloads, TF-state secrets)

## Context

WikiStream, DevSync, and SWE-Qwen each need a K8s story for the CV. Three separate clusters triples bill and ops. A single zonal Standard cluster is free-tier but needs node pool management. LAAD-anchored cluster superseded. v1 used zonal Autopilot and `kubernetes_manifest` for workloads with TF-state secrets — flagged as anti-patterns in Tier 1 review.

## Decision

Each GCP project owns + tears down its own **zonal ephemeral Autopilot** cluster with its project lifecycle — WikiStream's lives in `infra/main/modules/gke` (`wikistream-autopilot`, `us-central1-a`). No cross-project reference: sequential build→evidence→teardown breaks shared reference by design (a shared cluster has no owner once the first project tears down). Namespace `wikistream` keeps concrete `ResourceQuota`/`LimitRange` (§9: 2 CPU/4Gi requests, 10 pods) + `NetworkPolicy` default-deny.

- **Regional, not zonal:** Autopilot management fee is $0.10/hr (~$74/mo) whether zonal or regional — regional HA is free, so zonal "to save money" is the wrong answer for Autopilot (that's a Standard fact). Module outputs `cluster_ipv4_cidr` for firewall `allow-k8s-to-ch:8123`.
- **Platform vs workload boundary:** Terraform owns cluster, VPC/firewall, IAM/SA+WI, GCS buckets, AR, Secret Manager, ResourceQuota/LimitRange/NetworkPolicy/PDB/PriorityClass, ESO + ArgoCD installs. **Workloads (Deployment + 5 CronJobs + Service/Endpoints + PodMonitoring + ExternalSecret) are delivered via ArgoCD `Application` from `infra/k8s/chart` (Helm) — or `helm_release` interim — not `kubernetes_manifest`.** Fixes TF-state secret leak and gives GitOps reconciliation.
- **Secrets:** ESO `ExternalSecret` + `SecretStore` via Workload Identity; TF never sees value. `kubernetes_secret` from `data.google_secret_manager_secret_version` rejected (value in state).
- **Supply chain + observability:** Images SHA-pinned + Trivy + cosign/Binary Authorization; GMP `PodMonitoring` required; `google_billing_budget` with 50/80/100% thresholds.
- **Standard labels** `app.kubernetes.io/*` on every object; Helm chart at `infra/k8s/chart`.

## Alternatives

- Three clusters — rejected v1–v3 (assumed cost/duplication); adopted v4 as per-project ephemeral: sequential build→evidence→teardown, likely cheaper with no idle-gap billing (see Amendment v4).
- Single Standard zonal — rejected: node pool toil, no saving over Autopilot credit.
- LAAD-anchored cluster — rejected: superseded, wrong project ownership.
- Zonal Autopilot — rejected in v2: same cost as regional, worse HA.
- `kubernetes_manifest` for workloads — rejected in v2: no reconciliation, secret in state, conflates platform + app.
- CSI Secrets Store — considered, ESO chosen for simpler CRD + ArgoCD compat.

## Consequences

- Positive: each project self-contained — own `terraform destroy`, no cross-project owner. Sequential ephemeral likely cheaper: no idle-gap billing; each bounded build window plausibly absorbed by that month's $74.40 Autopilot credit (renews monthly). GitOps gives honest workload drift. ESO keeps TF state clean. NetworkPolicy + Quota make noisy-neighbor credible.
- Negative: ArgoCD is one more controller; `helm_release` interim is acceptable ponytail. ESO adds a CRD.
- Follow-up: Verify `ip_allocation_policy` + `cluster_ipv4_cidr` firewall; verify `services_ipv4_cidr` if needed; enable Binary Authorization on the cluster.

## Amendment v3 slim (2026-09-02) — execution vs intent

v2 intent is preserved as the platform-team upgrade path; v3 slim is the DE-CV execution. This ADR's decision still stands, but the following v2 specifics are deferred to ponytail comments in the Phase 6 plan (not executed in v3):

- Regional → Zonal `us-central1-a` (matches VM zone, co-located CIDR; regional is HA theatre for singleton-on-VM; Autopilot fee $0.10/hr ~$74/mo, first months covered by $74 credit — see plan §3/§8)
- ESO `ExternalSecret`+`SecretStore` → `kubernetes_secret` `sensitive = true` + WI (ESO when >1 cluster or rotation needed — plan §9)
- ArgoCD `Application`/Helm `infra/k8s/chart` / `helm_release` → `kubectl apply -k infra/k8s/` + `kustomization.yaml` (ArgoCD when team >3 — plan §7)
- 5 CronJobs (incl. restore-verify) → 4 CronJobs (restore-verify deferred)
- PDB/PriorityClass/ResourceQuota hardening details → kept minimal (ResourceQuota 2 CPU/4Gi + LimitRange, PDB deferred)
- Interview answer remains honest; upgrade path is noted not ignored. See phase-6-kubernetes-integration.md §16 changelog and ponytail comments at §4.4/§7/§9/§10/§11.

## Amendment v4 (2026-09-06): shared → ephemeral

Shared superseded. Each GCP project owns + tears down its own zonal Autopilot cluster with its project lifecycle — sequential, no `data.google_container_cluster` cross-project reference (sequential build→evidence→teardown breaks shared reference by design). Sequential likely cheaper — no idle-gap billing between projects; each bounded build window plausibly absorbed by that month's $74.40 Autopilot credit (renews monthly). Zonal, WI, `kubectl apply -k`, cost numbers unchanged. Interview: don't volunteer sharing — none exists now, nothing to explain if asked.

## References

- Phase 6 plan §3, §7, §8, §9, §11. Trade-off table in `vision-and-adr.md` §4 now updated (glossary drift fix).
