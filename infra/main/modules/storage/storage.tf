# Reader binding for the bootstrap-owned Artifact Registry repo
# (wikistream-consumer). The repo itself is created outside this config, so it
# is referenced by plain string id — no data source.
resource "google_artifact_registry_repository_iam_member" "consumer_reader" {
  project    = var.project_id
  location   = var.region
  repository = "wikistream-consumer"
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${var.service_account_email}"
}
