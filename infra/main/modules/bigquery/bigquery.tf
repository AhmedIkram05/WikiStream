# Central US dataset for all Phase 3 (warehouse) tables. Single dataset keeps
# IAM on the VM SA scoped to one container.
resource "google_bigquery_dataset" "wikistream" {
  project    = var.project_id
  dataset_id = "wikistream"
  location   = "US"
  labels     = var.labels
}

resource "google_bigquery_table" "kpi_edits_hourly" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.wikistream.dataset_id
  table_id   = "kpi_edits_hourly"
  schema     = file("${path.module}/../../../../warehouse/schemas/kpi_edits_hourly.json")
  labels     = var.labels

  time_partitioning {
    type  = "DAY"
    field = "hour"
  }

  clustering = ["wiki"]
}

resource "google_bigquery_table" "kpi_top_pages_hourly" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.wikistream.dataset_id
  table_id   = "kpi_top_pages_hourly"
  schema     = file("${path.module}/../../../../warehouse/schemas/kpi_top_pages_hourly.json")
  labels     = var.labels

  time_partitioning {
    type  = "DAY"
    field = "hour"
  }
}

resource "google_bigquery_table" "kpi_edit_sizes_hourly" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.wikistream.dataset_id
  table_id   = "kpi_edit_sizes_hourly"
  schema     = file("${path.module}/../../../../warehouse/schemas/kpi_edit_sizes_hourly.json")
  labels     = var.labels

  time_partitioning {
    type  = "DAY"
    field = "hour"
  }
}

resource "google_bigquery_table" "raw_events_sample" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.wikistream.dataset_id
  table_id   = "raw_events_sample"
  schema     = file("${path.module}/../../../../warehouse/schemas/raw_events_sample.json")
  labels     = var.labels

  time_partitioning {
    type  = "DAY"
    field = "inserted_at"
  }
}

resource "google_bigquery_table" "export_runs" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.wikistream.dataset_id
  table_id   = "export_runs"
  schema     = file("${path.module}/../../../../warehouse/schemas/export_runs.json")
  labels     = var.labels

  time_partitioning {
    type  = "DAY"
    field = "exported_at"
  }
}

# VM SA writes exports (load jobs) — dataset-scoped only, deliberately no
# project-level bigquery roles (least privilege).
resource "google_bigquery_dataset_iam_member" "vm_data_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.wikistream.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${var.service_account_email}"
}

# Staging bucket for exports (e.g. external tables / gs:// loads). Objects
# auto-delete after 7 days — transient staging only, not a backup.
resource "google_storage_bucket" "wikistream_bq_staging" {
  name                        = "wikistream-505003-bq-staging"
  project                     = var.project_id
  location                    = "US"
  uniform_bucket_level_access = true
  labels                      = var.labels

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }
}

# VM SA can write and read staging — bucket-scoped only.
resource "google_storage_bucket_iam_member" "wikistream_bq_staging_object_creator" {
  bucket = google_storage_bucket.wikistream_bq_staging.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${var.service_account_email}"
}

resource "google_storage_bucket_iam_member" "wikistream_bq_staging_object_viewer" {
  bucket = google_storage_bucket.wikistream_bq_staging.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.service_account_email}"
}
