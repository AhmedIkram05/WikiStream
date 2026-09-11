-- Idempotent reload of one window: export.sh loads the window into
-- kpi_top_pages_hourly_staging, then MERGEs it here. Re-runs upsert the same
-- (hour, title, wiki) keys instead of appending duplicates.
-- ponytail: upsert-only, no BY SOURCE DELETE — see merge_edits.sql.
MERGE wikistream.kpi_top_pages_hourly T
USING (
  SELECT hour, title, wiki, edits, bytes_delta
  FROM wikistream.kpi_top_pages_hourly_staging
  WHERE hour >= TIMESTAMP('{START}') AND hour < TIMESTAMP('{END}')
) S
ON T.hour = S.hour AND T.title = S.title AND T.wiki = S.wiki
WHEN MATCHED THEN UPDATE SET edits = S.edits, bytes_delta = S.bytes_delta
WHEN NOT MATCHED THEN INSERT (hour, title, wiki, edits, bytes_delta)
VALUES (S.hour, S.title, S.wiki, S.edits, S.bytes_delta)
