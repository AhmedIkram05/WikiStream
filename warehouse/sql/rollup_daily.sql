-- Daily gold rollup: hourly -> daily MERGE (idempotent). Scans from MIDNIGHT
-- of the day before {START} (not {START} - 1 day) so LAG() sees the FULL
-- preceding calendar day: with {START} mid-day, a {START}-1day scan would
-- contribute only the prior day's tail hours and dod_growth_pct would be
-- computed against a partial prior day. Only window days are merged, so the
-- boundary row's prev_day_edits stays correct on re-runs.
MERGE wikistream.kpi_daily T
USING (
  WITH daily AS (
    SELECT DATE(hour) AS day, wiki, SUM(edits) AS edits, SUM(bytes_delta) AS bytes_delta
    FROM wikistream.kpi_edits_hourly
    WHERE hour >= TIMESTAMP(DATE_SUB(DATE(TIMESTAMP('{START}')), INTERVAL 1 DAY))
      AND hour < TIMESTAMP('{END}')
    GROUP BY day, wiki
  ),
  with_prev AS (
    -- lag gap semantics: a wiki's zero-edit days have no row, so after an edit gap prev_day_edits is the last edited day (2+ days back) — deliberate, no fill.
    SELECT day, wiki, edits, bytes_delta, LAG(edits) OVER (PARTITION BY wiki ORDER BY day) AS prev_day_edits
    FROM daily
  )
  SELECT day, wiki, edits, bytes_delta, prev_day_edits, SAFE_DIVIDE(edits - prev_day_edits, prev_day_edits) AS dod_growth_pct
  FROM with_prev
  WHERE day >= DATE(TIMESTAMP('{START}')) AND day <= DATE(TIMESTAMP('{END}'))
) AS S
ON T.day = S.day AND T.wiki = S.wiki
  AND T.day >= DATE(TIMESTAMP('{START}')) AND T.day <= DATE(TIMESTAMP('{END}'))
WHEN MATCHED THEN UPDATE SET edits = S.edits, bytes_delta = S.bytes_delta, prev_day_edits = S.prev_day_edits, dod_growth_pct = S.dod_growth_pct
WHEN NOT MATCHED THEN INSERT (day, wiki, edits, bytes_delta, prev_day_edits, dod_growth_pct) VALUES (S.day, S.wiki, S.edits, S.bytes_delta, S.prev_day_edits, S.dod_growth_pct)
