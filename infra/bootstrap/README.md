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

## Apply (once, by hand)

```sh
gcloud config set project wikistream-505003   # NOT the gcloud default project
gcloud auth application-default login         # if not already done
terraform init
terraform apply -var project_id=wikistream-505003
```

The bucket is created with versioning enabled (state history) and uniform
bucket-level access. The apply ceremony is unchanged.
