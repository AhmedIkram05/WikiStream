variable "project_id" {
  description = "GCP project owning the service account and secrets"
  type        = string
}

variable "secret_ids" {
  description = "Secret Manager secrets the VM SA may read"
  type        = list(string)
}

variable "oslogin_human_member" {
  description = "Human principal granted roles/compute.osLogin (OS Login SSH, Q5)"
  type        = string
}
