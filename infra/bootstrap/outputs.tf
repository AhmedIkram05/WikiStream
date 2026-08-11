output "wif_provider_name" {
  description = "Full resource name of the GitHub WIF provider (CI passes this to google-github-actions/auth)"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deploy_sa_email" {
  description = "Email of the wikistream-deploy service account (CI authenticates as this)"
  value       = google_service_account.deploy.email
}
