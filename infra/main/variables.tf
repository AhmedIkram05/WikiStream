variable "project_id" {
  description = "GCP project that owns all wikistream infrastructure"
  type        = string
}

variable "region" {
  description = "GCP region for regional resources"
  type        = string
}

variable "zone" {
  description = "GCP zone for zonal resources"
  type        = string
}

variable "allowed_ips" {
  description = "CIDRs allowed to reach SSH (22), Grafana (3000) and ClickHouse (8123)"
  type        = list(string)

  validation {
    condition     = alltrue([for c in var.allowed_ips : can(cidrhost(c, 0))])
    error_message = "Each entry must be a CIDR range (e.g. \"203.0.113.5/32\")."
  }
}

variable "oslogin_human_member" {
  description = "Human principal granted roles/compute.osLogin (OS Login SSH, Q5)"
  type        = string
}
