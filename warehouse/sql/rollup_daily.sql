-- Daily gold rollup: hourly -> daily MERGE (idempotent). Scans one extra day
-- before {START} so LAG() sees the day preceding the window; only window days
-- are merged, so the boundary row's prev_day_edits is correct on re-runs.
MERGE wikistream.kpi_daily T
USING (
  WITH daily AS (
    SELECT DATE(hour) AS day, wiki, SUM(edits) AS edits, SUM(bytes_delta) AS bytes_delta
    FROM wikistream.kpi_edits_hourly
    WHERE hour >= TIMESTAMP_SUB(TIMESTAMP('{START}'), INTERVAL 1 DAY)
      AND hour < TIMESTAMP('{END}')
    GROUP BY day, wiki
  ),
  with_prev AS (
    SELECT day, wiki, edits, bytes_delta, LAG(edits) OVER (PARTITION BY wiki ORDER BY day) AS prev_day_edits
    FROM daily
  )
  SELECT day, wiki, edits, bytes_delta, prev_day_edits, SAFE_DIVIDE(edits - prev_day_edits, prev_day_edits) AS dod_growth_pct
  FROM with_prev
  WHERE day >= DATE(TIMESTAMP('{START}')) AND day <= DATE(TIMESTAMP('{END}'))
) AS S
ON T.day = S.day AND T.wiki = S.wiki
WHEN MATCHED THEN UPDATE SET edits = S.edits, bytes_delta = S.bytes_delta, prev_day_edits = S.prev_day_edits, dod_growth_pct = S.dod_growth_pct
WHEN NOT MATCHED THEN INSERT (day, wiki, edits, bytes_delta, prev_day_edits, dod_growth_pct) VALUES (S.day, S.wiki, S.edits, S.bytes_delta, S.prev_day_edits, S.dod_growth_pct)
