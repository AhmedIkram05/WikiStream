SELECT COALESCE(SUM(edits), 0) AS edits, COALESCE(SUM(bytes_delta), 0) AS bytes_delta
FROM wikistream.kpi_edits_hourly
WHERE hour >= TIMESTAMP('{START}') AND hour < TIMESTAMP('{END}')
