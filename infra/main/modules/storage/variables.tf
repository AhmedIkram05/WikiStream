variable "project_id" {
  description = "GCP project owning the Artifact Registry repo"
  type        = string
}

variable "service_account_email" {
  description = "VM service account granted roles/artifactregistry.reader"
  type        = string
}
