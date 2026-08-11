# Reader binding for the bootstrap-owned Artifact Registry repo
# (wikistream-consumer). The repo itself is created outside this config, so it
# is referenced by plain string id — no data source.
resource "google_artifact_registry_repository_iam_member" "consumer_reader" {
  project = var.project_id
  # AR repo is bootstrap-owned in us-central1 (locked there); deliberately NOT
  # var.region — the VM stack's region (us-east1 since the 2026-08-11 capacity
  # move) is independent of where the repo lives.
  location   = "us-central1"
  repository = "wikistream-consumer"
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${var.service_account_email}"
}
