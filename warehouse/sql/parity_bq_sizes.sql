SELECT COALESCE(SUM(edits), 0) AS edits
FROM wikistream.kpi_edit_sizes_hourly
WHERE hour >= TIMESTAMP('{START}') AND hour < TIMESTAMP('{END}')
