resource "google_service_account" "wikistream_vm" {
  account_id   = "wikistream-vm"
  display_name = "WikiStream VM service account"
  project      = var.project_id
}

# OS Login SSH for the human operator (Q5): explicit binding so SSH does not
# silently depend on the operator also being project owner (plan §4 review
# finding).
resource "google_project_iam_member" "oslogin_human" {
  project = var.project_id
  role    = "roles/compute.osLogin"
  member  = var.oslogin_human_member
}

# Scoped per secret: this SA can only read the two Phase 2 secrets, nothing
# else in the project.
resource "google_secret_manager_secret_iam_member" "secret_accessor" {
  for_each  = toset(var.secret_ids)
  project   = var.project_id
  secret_id = each.key
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.wikistream_vm.email}"
}

output "service_account_email" {
  value = google_service_account.wikistream_vm.email
}
