# Phase 4B: lift bucket for the hourly ClickHouse `default` DB backup. The
# local `backups` disk is the primary copy; GCS is a backup-of-a-backup, so a
# 2-day lifecycle keeps the bucket from growing unbounded. The VM SA writes
# (objectCreator) and reads back for restore (objectViewer).
resource "google_storage_bucket" "wikistream_backups" {
  name                        = "wikistream-505003-backups"
  location                    = "US"
  project                     = var.project_id
  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      age = 2
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "backups_creator" {
  bucket = google_storage_bucket.wikistream_backups.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${var.service_account_email}"
}

resource "google_storage_bucket_iam_member" "backups_viewer" {
  bucket = google_storage_bucket.wikistream_backups.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.service_account_email}"
}

# gcloud storage cp resolves the destination bucket's metadata (location /
# endpoint routing) via storage.buckets.get, which objectCreator/objectViewer
# do not include; legacyBucketReader supplies it, bucket-scoped only.
resource "google_storage_bucket_iam_member" "backups_reader" {
  bucket = google_storage_bucket.wikistream_backups.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${var.service_account_email}"
}
