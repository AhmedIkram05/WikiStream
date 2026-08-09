terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source = "hashicorp/google"
    }
  }
}

locals {
  bucket = "${var.project_id}-terraform-state"
}

provider "google" {
  project = var.project_id
}

# Remote state bucket for infra/main's backend. Deliberately in its own config
# with LOCAL state (ADR-007): if it lived inside the main config, terraform
# destroy would delete the bucket holding the state file of the destroy
# operation itself.
resource "google_storage_bucket" "tfstate" {
  name                        = local.bucket
  location                    = "US"
  force_destroy               = false
  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}
