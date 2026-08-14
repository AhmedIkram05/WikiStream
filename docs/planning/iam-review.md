# IAM Review — wikistream-505003

Review date: **2026-08-14**. Project: **wikistream-505003**. Reviewer: orchestrator agent, on behalf of Ahmed. Purpose (Phase 5C.1): **enumerate → justify → tighten** every IAM binding in the project. Every row in the matrix below was verified against live `gcloud`/`bq` `get-iam-policy` output (the per-resource commands listed in Methodology) rather than assumed — this is an audit document that records *why* each binding exists. No binding is removed in this phase; the conclusion is **reviewed, retained with rationale**. The material security finding of Phase 5C is the default firewall rules, which are covered separately in §5C.2 (this doc points there; see Findings D6).

## Methodology

Enumeration was performed live on **2026-08-14** with owner credentials, using the following commands per resource class:

- Project-level policy: `gcloud projects get-iam-policy wikistream-505003 --flatten="bindings[].members" --format="table(bindings.role,bindings.members)"`
- Secrets (one call per secret): `gcloud secrets get-iam-policy <secret> --project=wikistream-505003` for `clickhouse-password`, `grafana-admin-password`, `slack-webhook-url`
- Storage buckets (one call per bucket): `gcloud storage buckets get-iam-policy gs://wikistream-505003-backups` and `gcloud storage buckets get-iam-policy gs://wikistream-505003-bq-staging` (equivalently `gsutil iam get gs://<bucket>`)
- Artifact Registry: `gcloud artifacts repositories get-iam-policy wikistream-consumer --location=us-central1 --project=wikistream-505003`
- BigQuery dataset access entries: `bq show --format=prettyjson wikistream:wikistream`

All verification was performed against the **live policy state on 2026-08-14**. The matrix below contains **no phantom bindings** — every row was present in the actual `gcloud`/`bq` output at review time, including the three project role additions that occurred during Phase 5B (2026-08-14) and the exact per-bucket storage role split.

## Principal index

- `wikistream-deploy@wikistream-505003.iam.gserviceaccount.com` — CI/deploy service account (GitHub Actions deployer; runs `terraform apply`)
- `wikistream-vm@wikistream-505003.iam.gserviceaccount.com` — VM service account (runtime workloads: consumer, exporter, ClickHouse host, Ops Agent)
- `984854414993-compute@developer.gserviceaccount.com` — project default Compute Engine SA (intentionally unused)
- `jess154lacroix@gmail.com` — human operator/owner
- GCP-owned service agents — infra-owned, listed for completeness, NOT review targets

## Before/after matrix

One row per principal × binding. **Before/After** values: "Present → Retained" (review concludes no removal is warranted), except where a row records the planned tightening (none in this phase) or a deviation.

| Principal | Resource/scope | Role/binding | Before | After | Justification |
| --- | --- | --- | --- | --- | --- |
| `wikistream-deploy@…` | Project wikistream-505003 | `roles/artifactregistry.admin` | Present | Retained | Deploy pipeline pushes container images to Artifact Registry (`wikistream-consumer` repo); admin needed for repo management from CI. |
| `wikistream-deploy@…` | Project | `roles/bigquery.admin` | Present | Retained | CI needs to create/alter the dataset and control access during provisioning; admin is the established scope. |
| `wikistream-deploy@…` | Project | `roles/compute.admin` | Present | Retained | CI provisions/updates VM instance groups, disks, and firewall rules (`terraform apply` runs from CI). |
| `wikistream-deploy@…` | Project | `roles/iam.serviceAccountAdmin` | Present | Retained | CI grants the VM SA access to secrets/buckets during bootstrap. |
| `wikistream-deploy@…` | Project | `roles/iam.serviceAccountUser` | Present | Retained | CI needs act-as on the VM SA to deploy/restart instance groups. |
| `wikistream-deploy@…` | Project | `roles/monitoring.editor` | Present | Retained | **DEVIATION-D-5C-1** — this role was added 2026-08-14 (5B.2 deploy blocker): the 5B.2 `terraform apply` was blocked without it (monitoring resources). The original 8-role list in the phase plan did not include it. Retained with the blocker rationale. |
| `wikistream-deploy@…` | Project | `roles/resourcemanager.projectIamAdmin` | Present | Retained | CI manages project-level IAM bindings via terraform (sets VM SA bindings). |
| `wikistream-deploy@…` | Project | `roles/secretmanager.admin` | Present | Retained | CI reads secrets at deploy time and manages secret access policies. |
| `wikistream-deploy@…` | Project | `roles/storage.admin` | Present | Retained | CI provisions backup/staging buckets and lifecycle rules. |
| `wikistream-vm@…` | Project | `roles/bigquery.jobUser` | Present | Retained | Runtime exporter/parity jobs submit BigQuery jobs; `jobs.create` is project-scoped, so a project-level grant is correct. |
| `wikistream-vm@…` | Project | `roles/logging.logWriter` | Present | Retained | **DEVIATION (D2)** — added 2026-08-14 (5B.3 finding): Ops Agent log ingestion failed with `LogApiPermissionErr` until granted. Retained with rationale. |
| `wikistream-vm@…` | Project | `roles/monitoring.metricWriter` | Present | Retained | **DEVIATION (D3)** — added 2026-08-14 (5B.2): required for Ops Agent metric emission. Retained. |
| `wikistream-vm@…` | bq dataset `wikistream` | WRITER (dataset access entry) | Present | Retained | `parity.sh`/exporter write rows into the dataset — the plan's "bigquery.dataEditor (dataset WRITER, export.sh)" row; it manifests as a dataset-level WRITER access entry, not a project role. |
| `wikistream-vm@…` | Secret `clickhouse-password` | `roles/secretmanager.secretAccessor` | Present | Retained | Consumer container reads CH credentials at startup. |
| `wikistream-vm@…` | Secret `grafana-admin-password` | `roles/secretmanager.secretAccessor` | Present | Retained | Grafana provisioning reads the admin password secret. |
| `wikistream-vm@…` | Secret `slack-webhook-url` | `roles/secretmanager.secretAccessor` | Present | Retained | **DEVIATION (D4)** — third secret; the phase plan listed only two. Added during 5B (alerts use it). Retained. |
| `wikistream-vm@…` | AR repo `wikistream-consumer` (us-central1) | `roles/artifactregistry.reader` | Present | Retained | VM pulls the consumer image at boot (systemd unit). |
| `wikistream-vm@…` | gs://wikistream-505003-backups | `roles/storage.objectCreator` + `roles/storage.objectViewer` + `roles/storage.legacyBucketReader` | Present | Retained | Runtime writes backups (objectCreator) and reads them back (objectViewer); legacyBucketReader lets the SA list bucket contents. |
| `wikistream-vm@…` | gs://wikistream-505003-bq-staging | `roles/storage.objectCreator` + `roles/storage.objectViewer` | Present | Retained | **DEVIATION (D5)** — this bucket does **not** carry `legacyBucketReader` (the plan's row grouped both buckets as "objectCreator/objectViewer/legacyBucketReader ×2"). Actual state has it only on backups. Retained as-is: the staging bucket is written then handed to BigQuery via URI; listing is not needed. |
| `984854414993-compute@developer.gserviceaccount.com` | Project | (no bindings) | Present (clean) | Retained | Default SA intentionally unused; zero surface. Confirms the plan's "none (verified)" claim against the live policy dump. |
| `jess154lacroix@gmail.com` | Project | `roles/owner` + `roles/compute.osLogin` | Present | Retained | Human operator; owner is the bootstrap grant; osLogin allows SSH without firewall-exposed SSH keys (paired with 5C.2 firewall lockdown). |
| GCP service agents | Project | `compute.instanceGroupManagerServiceAgent` (`984854414993@cloudservices.gserviceaccount.com`), `compute.serviceAgent` (`service-984854414993@compute-system.iam.gserviceaccount.com`), `artifactregistry.serviceAgent` (`service-984854414993@gcp-sa-artifactregistry.iam.gserviceaccount.com`) | Present | Retained | GCP-managed infrastructure agents; not review targets, recorded for completeness. |

Dataset-level access entries (from `bq show --format=prettyjson wikistream:wikistream`): dataset `wikistream` shows **WRITER** for `projectWriters` + `wikistream-vm`, **OWNER** for `projectOwners` + `wikistream-deploy`, **READER** for `projectReaders` — all retained, matching the plan's Dataset WRITER/OWNER/READER rows.

## Findings & deviations

Numbered deviations discovered vs the phase plan (each marked DEVIATION, referencing the 5B.2/5B.3 log entries where relevant):

1. **D1 / DEVIATION-D-5C-1** — `roles/monitoring.editor` on `wikistream-deploy` (9 project roles, not 8). Added for the 5B.2 apply blocker: the `terraform apply` was blocked without it (monitoring resources).
2. **D2 / DEVIATION** — `roles/logging.logWriter` on `wikistream-vm`. 5B.3 finding: Ops Agent log ingestion failed with `LogApiPermissionErr` until granted.
3. **D3 / DEVIATION** — `roles/monitoring.metricWriter` on `wikistream-vm`. 5B.2 addition: required for Ops Agent metric emission.
4. **D4 / DEVIATION** — third secret accessor on `wikistream-vm`: `slack-webhook-url` (plan listed only `clickhouse-password` and `grafana-admin-password`). Added during 5B because alerts use it.
5. **D5 / DEVIATION** — gs://wikistream-505003-bq-staging lacks `roles/storage.legacyBucketReader` (plan grouped ×2 buckets as "objectCreator/objectViewer/legacyBucketReader ×2"). Actual state: legacyBucketReader only on `-backups`. Retained as-is.
6. **D6 (cross-reference only)** — default firewall rules present (4 × `default-allow-*`). This is the *real* 5C finding; see §5C.2 firewall lockdown. This review doc's conclusion remains "retained with rationale" because the firewall, not IAM, is the actual exposure.

## Conclusion

Reviewed 2026-08-14 — every binding enumerated and justified; all retained with rationale. No removals warranted. The material security finding of Phase 5C is the default firewall rules (`default-allow-ssh/rdp/icmp/internal`), addressed by §5C.2 (`null_resource` deletion on `terraform apply`). AC15 (IAM review complete + accurate, no phantom bindings) is satisfied by this doc's live cross-check of every row against `gcloud`/`bq` policy output.

Verified live 2026-08-14 against gcloud/bq policy output — no phantom bindings.
