SELECT
    formatDateTime(inserted_at, '%Y-%m-%dT%H:%i:%sZ') AS inserted_at,
    event,
    wiki,
    title,
    user AS user,
    if(is_bot, 'true', 'false') AS is_bot,
    event_type
FROM default.raw_events
WHERE inserted_at >= '{START}'
  AND inserted_at < '{END}'
  AND sipHash64(event) % 100 < 10
