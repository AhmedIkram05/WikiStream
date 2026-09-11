-- Idempotent reload of one window: export.sh loads the window into
-- kpi_edits_hourly_staging, then MERGEs it here. Re-runs upsert the same
-- (hour, wiki, is_bot) keys instead of appending duplicates.
-- ponytail: upsert-only, no BY SOURCE DELETE — a shrunk window leaves stale
-- keys; windows only grow in practice, add it if full convergence is needed.
MERGE wikistream.kpi_edits_hourly T
USING (
  SELECT hour, wiki, is_bot, edits, bytes_delta
  FROM wikistream.kpi_edits_hourly_staging
  WHERE hour >= TIMESTAMP('{START}') AND hour < TIMESTAMP('{END}')
) S
ON T.hour = S.hour AND T.wiki = S.wiki AND T.is_bot = S.is_bot
WHEN MATCHED THEN UPDATE SET edits = S.edits, bytes_delta = S.bytes_delta
WHEN NOT MATCHED THEN INSERT (hour, wiki, is_bot, edits, bytes_delta)
VALUES (S.hour, S.wiki, S.is_bot, S.edits, S.bytes_delta)
