# Bootstrap — state bucket, identity & artifact repo (ADR-007 deviation)

One-time setup, applied **manually with local state** — it is never part of the
normal CI/CD flow and never destroyed by `infra/main`'s `terraform destroy`.

This config owns three things that share the same "can't bootstrap itself"
property — each must exist before the first gated CI run and survive destroy:

1. **State bucket** — `<project_id>-terraform-state` holds the remote state for
   `infra/main` (ADR-007). If the main config destroyed it, it would delete the
   state file tracking the destroy operation itself — so it lives here,
   separate, with `prevent_destroy` as a second line of defense.
2. **Identity** — the GitHub OIDC workload identity pool/provider (trusting
   repo `AhmedIkram05/WikiStream` only), the `wikistream-deploy` service
   account with its project roles, and the WIF → SA binding. CI authenticates
   with this before any Terraform runs, so CI cannot create it itself.
3. **Artifact Registry repo** — `wikistream-consumer` (us-central1, DOCKER).
   The build-push CI job runs BEFORE the gated apply, so the repo must exist
   from the very first merge and survive `infra/main` destroy.

This is a recorded deviation from ADR-007's "bootstrap = bucket only" wording:
identity and the AR repo have the same property as the bucket — they cannot be
bootstrapped by CI, because CI depends on them to run at all.

## Deploy SA roles — documented broad grant

`wikistream-deploy` is the CI deploy identity: it pushes images and runs
`terraform apply` for `infra/main`. Its project-level roles are broader than a
runtime service account's because Terraform creates project primitives
(VPC/firewall, BigQuery dataset, GCS buckets, secrets, monitoring policies)
that have no existing resource to scope a binding to.

| Role | Why it is needed |
| --- | --- |
| `roles/compute.admin` | create/replace the VM, disk, static IP, VPC, subnet and firewall rules |
| `roles/artifactregistry.admin` | bootstrap owns the `wikistream-consumer` repo; CI pushes consumer and gx images |
| `roles/secretmanager.admin` | create secrets and versions managed by `infra/main` |
| `roles/iam.serviceAccountAdmin` + `roles/iam.serviceAccountUser` | create the VM service account and run the instance as it |
| `roles/resourcemanager.projectIamAdmin` | bind `roles/compute.osLogin` for the human operator |
| `roles/bigquery.admin` | create the dataset and scheduled transfer, and manage its IAM |
| `roles/storage.admin` | create the staging/backup buckets before bucket-scoped bindings exist |
| `roles/monitoring.editor` | create alert policies and notification channels |

This is a **documented exception** to least privilege, not an oversight. See
`docs/planning/iam-review.md` for the full principal × binding review. Reducing
it further requires custom roles or resource conditions; do that before sharing
the project with other operators, and re-run the bootstrap apply so the change
is recorded in state.

## Apply (once, by hand)

```sh
gcloud config set project wikistream-505003   # NOT the gcloud default project
gcloud auth application-default login         # if not already done
terraform init
terraform apply -var project_id=wikistream-505003
```

The bucket is created with versioning enabled (state history) and uniform
bucket-level access. The apply ceremony is unchanged.
