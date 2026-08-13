variable "project_id" {
  description = "GCP project owning the backups bucket"
  type        = string
}

variable "service_account_email" {
  description = "VM service account granted objectCreator/objectViewer on the backups bucket"
  type        = string
}