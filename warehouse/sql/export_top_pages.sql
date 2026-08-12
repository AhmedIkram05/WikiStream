SELECT
    formatDateTime(toStartOfHour(minute), '%Y-%m-%dT%H:%i:%sZ') AS hour,
    title,
    wiki,
    sum(edits) AS edits,
    sum(bytes_delta) AS bytes_delta
FROM default.mv_top_pages_per_minute
WHERE minute >= '{START}'
  AND minute < '{END}'
GROUP BY hour, title, wiki
ORDER BY hour
