variable "project_id" {
  description = "GCP project owning the instance and address"
  type        = string
}

variable "region" {
  description = "Region for the static address"
  type        = string
}

variable "zone" {
  description = "Zone for the instance"
  type        = string
}

variable "labels" {
  description = "Standard labels applied to label-capable resources"
  type        = map(string)
}

variable "service_account_email" {
  description = "Service account attached to the instance"
  type        = string
}

variable "startup_script" {
  description = "Startup script content (injected from main.tf via file())"
  type        = string
}

variable "subnetwork_self_link" {
  description = "Self link of the subnetwork the instance attaches to"
  type        = string
}
