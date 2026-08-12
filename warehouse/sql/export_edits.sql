SELECT
    formatDateTime(toStartOfHour(minute), '%Y-%m-%dT%H:%i:%sZ') AS hour,
    wiki,
    if(is_bot, 'true', 'false') AS is_bot,
    sum(edits) AS edits,
    sum(bytes_delta) AS bytes_delta
FROM default.mv_edits_per_minute
WHERE minute >= '{START}'
  AND minute < '{END}'
  AND wiki != ''
GROUP BY hour, wiki, is_bot
ORDER BY hour
