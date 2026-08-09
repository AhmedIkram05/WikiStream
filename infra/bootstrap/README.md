# Bootstrap — remote-state bucket (ADR-007)

One-time setup, applied **manually with local state** — it is never part of the
normal CI/CD flow and never destroyed by `infra/main`'s `terraform destroy`.

The GCS bucket `<project_id>-terraform-state` holds the remote state for
`infra/main`. If the main config destroyed it, it would delete the state file
tracking the destroy operation itself — so it lives here, separate, with
`prevent_destroy` as a second line of defense.

## Apply (once, by hand)

```sh
gcloud config set project wikistream-505003   # NOT the gcloud default project
gcloud auth application-default login         # if not already done
terraform init
terraform apply -var project_id=wikistream-505003
```

The bucket is created with versioning enabled (state history) and uniform
bucket-level access.
