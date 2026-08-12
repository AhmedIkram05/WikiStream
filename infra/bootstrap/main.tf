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

data "google_project" "project" {
  project_id = var.project_id
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

# APIs CI and infra/main depend on. Enabled here, by hand, so the first gated
# CI run starts with a project that is fully ready.
resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com", # service account CRUD (module.iam)
    "iamcredentials.googleapis.com",
    "sts.googleapis.com", # workload identity federation token exchange
    "oslogin.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "bigquery.googleapis.com",
  ])
  service = each.key
}

# GitHub Actions authenticates as the deploy SA via workload identity
# federation — no long-lived keys in CI. The pool/provider live in bootstrap
# because CI uses them to authenticate, so it cannot create them itself.
resource "google_iam_workload_identity_pool" "ci" {
  workload_identity_pool_id = "wikistream-ci"
  display_name              = "WikiStream CI"
}

# Trust only assertions from this exact repo. Deliberately not pinned to a
# branch: PRs, not just main, must be able to build and push artifacts.
resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.ci.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"

  attribute_condition = "assertion.repository == \"AhmedIkram05/WikiStream\""
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# CI's service account: what the WIF provider hands out a token for.
resource "google_service_account" "deploy" {
  account_id   = "wikistream-deploy"
  display_name = "WikiStream deploy (CI)"
}

# Project-level roles the deploy SA needs across infra/main's resources.
# projectIamAdmin: infra/main binds roles/compute.osLogin for the human
# account (project-level IAM) — requires resourcemanager.projects.setIamPolicy.
resource "google_project_iam_member" "deploy_project_roles" {
  for_each = toset([
    "roles/compute.admin",
    "roles/artifactregistry.admin",
    "roles/secretmanager.admin",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.serviceAccountUser",
    "roles/resourcemanager.projectIamAdmin",
    # 3C: infra/main now creates the BigQuery dataset (bigquery.datasets.create)
    # and the GCS staging bucket (storage.buckets.create); both are project
    # primitives that dataset/bucket-scoped roles cannot grant.
    "roles/bigquery.admin",
    "roles/storage.admin",
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

# State-bucket access is deliberately narrower: object admin on the tfstate
# bucket only, not project-wide storage admin.
resource "google_storage_bucket_iam_member" "deploy_tfstate_object_admin" {
  bucket = google_storage_bucket.tfstate.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.deploy.email}"
}

# Bind the GitHub WIF provider (for this repo) to the deploy SA.
resource "google_service_account_iam_member" "deploy_wif" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member = format(
    "principalSet://iam.googleapis.com/projects/%s/locations/global/workloadIdentityPools/%s/attribute.repository/%s",
    data.google_project.project.number,
    google_iam_workload_identity_pool.ci.workload_identity_pool_id,
    "AhmedIkram05/WikiStream",
  )
}

# Container image repo. Bootstrap-owned for the same reason as the bucket:
# the build-push CI job runs BEFORE the gated apply, so the repo must exist
# from the very first merge and survive infra/main's destroy.
resource "google_artifact_registry_repository" "wikistream_consumer" {
  repository_id = "wikistream-consumer"
  location      = "us-central1"
  format        = "DOCKER"
}
