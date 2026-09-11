# Central US dataset for all Phase 3 (warehouse) tables. Single dataset keeps
# IAM on the VM SA scoped to one container.
resource "google_bigquery_dataset" "wikistream" {
  project    = var.project_id
  dataset_id = "wikistream"
  location   = "US"
  labels     = var.labels
}

resource "google_bigquery_table" "kpi_edits_hourly" {
  project                  = var.project_id
  dataset_id               = google_bigquery_dataset.wikistream.dataset_id
  table_id                 = "kpi_edits_hourly"
  schema                   = file("${path.module}/../../../../warehouse/schemas/kpi_edits_hourly.json")
  labels                   = var.labels
  require_partition_filter = true

  time_partitioning {
    type          = "DAY"
    field         = "hour"
    expiration_ms = 63072000000 # 730d KPIs
  }

  clustering = ["wiki", "is_bot"]
}

resource "google_bigquery_table" "kpi_top_pages_hourly" {
  project                  = var.project_id
  dataset_id               = google_bigquery_dataset.wikistream.dataset_id
  table_id                 = "kpi_top_pages_hourly"
  schema                   = file("${path.module}/../../../../warehouse/schemas/kpi_top_pages_hourly.json")
  labels                   = var.labels
  require_partition_filter = true

  time_partitioning {
    type          = "DAY"
    field         = "hour"
    expiration_ms = 63072000000 # 730d KPIs
  }

  clustering = ["wiki"]
}

resource "google_bigquery_table" "kpi_edit_sizes_hourly" {
  project                  = var.project_id
  dataset_id               = google_bigquery_dataset.wikistream.dataset_id
  table_id                 = "kpi_edit_sizes_hourly"
  schema                   = file("${path.module}/../../../../warehouse/schemas/kpi_edit_sizes_hourly.json")
  labels                   = var.labels
  require_partition_filter = true

  time_partitioning {
    type          = "DAY"
    field         = "hour"
    expiration_ms = 63072000000 # 730d KPIs
  }
}

resource "google_bigquery_table" "raw_events_sample" {
  project                  = var.project_id
  dataset_id               = google_bigquery_dataset.wikistream.dataset_id
  table_id                 = "raw_events_sample"
  schema                   = file("${path.module}/../../../../warehouse/schemas/raw_events_sample.json")
  labels                   = var.labels
  require_partition_filter = true

  time_partitioning {
    type          = "DAY"
    field         = "inserted_at"
    expiration_ms = 7776000000 # 90d raw sample
  }

  clustering = ["wiki"]
}

# Idempotent hourly export (staging + MERGE): export.sh bq-loads each window
# into <final>_staging, then MERGEs it into the final table, so re-running a
# window upserts the same keys instead of appending duplicates.
# ponytail: schemas reused from the final tables (same JSON); DAY partitioning
# kept for prunability, clustering dropped — staging is merge-filtered anyway.
resource "google_bigquery_table" "kpi_edits_hourly_staging" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.wikistream.dataset_id
  table_id   = "kpi_edits_hourly_staging"
  schema     = file("${path.module}/../../../../warehouse/schemas/kpi_edits_hourly.json")
  labels     = var.labels

  time_partitioning {
    type          = "DAY"
    field         = "hour"
    expiration_ms = 604800000 # 7d staging
  }
}

resource "google_bigquery_table" "kpi_top_pages_hourly_staging" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.wikistream.dataset_id
  table_id   = "kpi_top_pages_hourly_staging"
  schema     = file("${path.module}/../../../../warehouse/schemas/kpi_top_pages_hourly.json")
  labels     = var.labels

  time_partitioning {
    type          = "DAY"
    field         = "hour"
    expiration_ms = 604800000 # 7d staging
  }
}

resource "google_bigquery_table" "kpi_edit_sizes_hourly_staging" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.wikistream.dataset_id
  table_id   = "kpi_edit_sizes_hourly_staging"
  schema     = file("${path.module}/../../../../warehouse/schemas/kpi_edit_sizes_hourly.json")
  labels     = var.labels

  time_partitioning {
    type          = "DAY"
    field         = "hour"
    expiration_ms = 604800000 # 7d staging
  }
}

resource "google_bigquery_table" "raw_events_sample_staging" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.wikistream.dataset_id
  table_id   = "raw_events_sample_staging"
  schema     = file("${path.module}/../../../../warehouse/schemas/raw_events_sample.json")
  labels     = var.labels

  time_partitioning {
    type          = "DAY"
    field         = "inserted_at"
    expiration_ms = 604800000 # 7d staging
  }
}

# ponytail: no require_partition_filter — parity.sh freshness gate filters on
# window_end, not the exported_at partition column; the flag would reject it.
resource "google_bigquery_table" "export_runs" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.wikistream.dataset_id
  table_id   = "export_runs"
  schema     = file("${path.module}/../../../../warehouse/schemas/export_runs.json")
  labels     = var.labels

  time_partitioning {
    type          = "DAY"
    field         = "exported_at"
    expiration_ms = 31536000000 # 365d ops history
  }
}

# Cost governance view over INFORMATION_SCHEMA.JOBS (project-scoped, no
# partition filter possible on views). Query lives in warehouse/sql so it is
# versioned once and referenced here via file().
resource "google_bigquery_table" "v_bq_cost_daily" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.wikistream.dataset_id
  table_id   = "v_bq_cost_daily"
  labels     = var.labels

  view {
    query          = file("${path.module}/../../../../warehouse/sql/v_bq_cost_daily.sql")
    use_legacy_sql = false
  }
}

resource "google_bigquery_table" "kpi_daily" {
  project                  = var.project_id
  dataset_id               = google_bigquery_dataset.wikistream.dataset_id
  table_id                 = "kpi_daily"
  schema                   = file("${path.module}/../../../../warehouse/schemas/kpi_daily.json")
  labels                   = var.labels
  require_partition_filter = true

  time_partitioning {
    type          = "DAY"
    field         = "day"
    expiration_ms = 63072000000 # 730d KPIs
  }

  clustering = ["wiki"]
}

# Daily gold rollup: MERGE hourly -> daily (idempotent, 3-day lookback for
# late exports). SQL lives in warehouse/sql/rollup_daily.sql; {START}/{END}
# placeholders are swapped for a relative window so the scheduled run needs
# no manual dates (local tests still substitute via sed, like parity.sh).
# ponytail: no destination_dataset_id — BQ rejects destination tables for DML
# (MERGE); the query writes via MERGE, not a SELECT-into.
data "google_project" "project" {}

resource "google_project_iam_member" "transfer_token_creator" {
  project = var.project_id
  role    = "roles/iam.serviceAccountTokenCreator"
  member  = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-bigquerydatatransfer.iam.gserviceaccount.com"
}

resource "google_bigquery_data_transfer_config" "rollup_daily" {
  display_name         = "wikistream-rollup-daily"
  location             = "US"
  data_source_id       = "scheduled_query"
  schedule             = "every day 06:00"
  service_account_name = var.service_account_email

  params = {
    query = replace(replace(file("${path.module}/../../../../warehouse/sql/rollup_daily.sql"), "TIMESTAMP('{START}')", "TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 3 DAY)"), "TIMESTAMP('{END}')", "CURRENT_TIMESTAMP()")
  }

  depends_on = [google_bigquery_table.kpi_daily, google_project_iam_member.transfer_token_creator]
}

# VM SA reads/writes warehouse data — dataset-scoped dataEditor only.
resource "google_bigquery_dataset_iam_member" "vm_data_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.wikistream.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${var.service_account_email}"
}

# BigQuery jobs are PROJECT-scoped: roles/bigquery.dataEditor grants no
# bigquery.jobs.create, and dataset IAM cannot grant it, so every bq load
# (export.sh), bq query (parity.sh) and the Grafana GCE-auth datasource would
# fail AccessDenied. jobUser at project scope creates jobs but cannot read or
# write data — table access stays dataset-scoped above (ADR-010 least
# privilege). DEVIATION from plan Q8 "dataEditor ONLY": recorded in
# docs/implementation-log.md (code-review BLOCKER, 2026-08-12).
resource "google_project_iam_member" "vm_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${var.service_account_email}"
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
