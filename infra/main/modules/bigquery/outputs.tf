output "dataset_id" {
  description = "BigQuery dataset ID holding the warehouse tables"
  value       = google_bigquery_dataset.wikistream.dataset_id
}

output "bucket_name" {
  description = "Name of the BQ staging bucket"
  value       = google_storage_bucket.wikistream_bq_staging.name
}
