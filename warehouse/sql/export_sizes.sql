SELECT
    formatDateTime(toStartOfHour(minute), '%Y-%m-%dT%H:%i:%sZ') AS hour,
    bucket,
    sum(edits) AS edits
FROM default.mv_edit_sizes_per_minute
WHERE minute >= '{START}'
  AND minute < '{END}'
GROUP BY hour, bucket
ORDER BY hour
