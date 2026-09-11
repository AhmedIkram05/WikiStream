-- Idempotent reload of one window: export.sh loads the window into
-- kpi_edit_sizes_hourly_staging, then MERGEs it here. Re-runs upsert the same
-- (hour, bucket) keys instead of appending duplicates.
-- ponytail: upsert-only, no BY SOURCE DELETE — see merge_edits.sql.
MERGE wikistream.kpi_edit_sizes_hourly T
USING (
  SELECT hour, bucket, edits
  FROM wikistream.kpi_edit_sizes_hourly_staging
  WHERE hour >= TIMESTAMP('{START}') AND hour < TIMESTAMP('{END}')
) S
ON T.hour = S.hour AND T.bucket = S.bucket
WHEN MATCHED THEN UPDATE SET edits = S.edits
WHEN NOT MATCHED THEN INSERT (hour, bucket, edits)
VALUES (S.hour, S.bucket, S.edits)
