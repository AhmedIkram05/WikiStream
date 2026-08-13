variable "project_id" {
  description = "GCP project owning the monitoring resources"
  type        = string
}

variable "alert_email" {
  description = "Email address receiving Cloud Monitoring alert notifications"
  type        = string
}

variable "labels" {
  description = "Standard resource labels"
  type        = map(string)
}
