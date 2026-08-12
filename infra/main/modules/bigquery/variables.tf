variable "project_id" {
  description = "GCP project owning the BigQuery dataset and staging bucket"
  type        = string
}

variable "service_account_email" {
  description = "VM service account granted dataset/bucket writer access"
  type        = string
}

variable "labels" {
  description = "Labels applied to the dataset and staging bucket"
  type        = map(string)
  default     = {}
}
