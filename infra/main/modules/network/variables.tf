variable "project_id" {
  description = "GCP project owning the VPC"
  type        = string
}

variable "region" {
  description = "Region for the subnetwork"
  type        = string
}

variable "allowed_ips" {
  description = "CIDRs allowed through SSH/Grafana/ClickHouse ingress rules"
  type        = list(string)
}
