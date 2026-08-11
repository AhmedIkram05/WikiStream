# Phase 2 secrets (task 2.3). random_password with special=false keeps the
# values alphanumeric so the startup script can interpolate them into the
# init.sql heredoc and .env without shell-escaping hazards.

resource "random_password" "ch_password" {
  length  = 24
  special = false
}

resource "random_password" "gf_password" {
  length  = 24
  special = false
}

resource "google_secret_manager_secret" "clickhouse_password" {
  secret_id = "clickhouse-password"
  project   = var.project_id
  labels    = local.labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "grafana_admin_password" {
  secret_id = "grafana-admin-password"
  project   = var.project_id
  labels    = local.labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "clickhouse_password_v1" {
  secret      = google_secret_manager_secret.clickhouse_password.id
  secret_data = random_password.ch_password.result
}

resource "google_secret_manager_secret_version" "grafana_admin_password_v1" {
  secret      = google_secret_manager_secret.grafana_admin_password.id
  secret_data = random_password.gf_password.result
}

output "clickhouse_password_secret_data" {
  description = "Live clickhouse-password secret value (alphanumeric, 24 chars)"
  value       = random_password.ch_password.result
  sensitive   = true
}

output "grafana_admin_password_secret_data" {
  description = "Live grafana-admin-password secret value (alphanumeric, 24 chars)"
  value       = random_password.gf_password.result
  sensitive   = true
}
