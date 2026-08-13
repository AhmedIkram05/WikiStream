terraform {
  required_version = ">= 1.5"

  backend "gcs" {
    bucket = "wikistream-505003-terraform-state"
    prefix = "main"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 7.43"
    }
    random = {
      source = "hashicorp/random"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  labels = {
    project    = "wikistream"
    managed-by = "terraform"
    phase      = "4"
  }
}

module "network" {
  source      = "./modules/network"
  project_id  = var.project_id
  region      = var.region
  allowed_ips = var.allowed_ips
}

module "iam" {
  source     = "./modules/iam"
  project_id = var.project_id
  secret_ids = [
    google_secret_manager_secret.clickhouse_password.secret_id,
    google_secret_manager_secret.grafana_admin_password.secret_id,
    google_secret_manager_secret.slack_webhook_url.secret_id,
  ]
  oslogin_human_member = var.oslogin_human_member
}

module "storage" {
  source                = "./modules/storage"
  project_id            = var.project_id
  region                = var.region
  service_account_email = module.iam.service_account_email
}

module "compute" {
  source                = "./modules/compute"
  project_id            = var.project_id
  region                = var.region
  zone                  = var.zone
  labels                = local.labels
  service_account_email = module.iam.service_account_email
  startup_script        = file("${path.module}/templates/startup.sh")
  subnetwork_self_link  = module.network.subnetwork_self_link
}

module "bigquery" {
  source                = "./modules/bigquery"
  project_id            = var.project_id
  service_account_email = module.iam.service_account_email
  labels                = local.labels
}

module "backups" {
  source                = "./modules/backups"
  project_id            = var.project_id
  service_account_email = module.iam.service_account_email
}

output "vm_static_ip" {
  description = "Static external IP of the wikistream VM"
  value       = module.compute.static_ip
}
