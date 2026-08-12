-- Inner query: `ts` avoids aliasing the formatted value as `inserted_at`,
-- which would shadow the source column and silently break every WHERE
-- predicate on it. Outer query renames ts back to inserted_at for the BQ schema.
SELECT
    ts AS inserted_at,
    event,
    wiki,
    title,
    user,
    is_bot,
    event_type
FROM (
    SELECT
        formatDateTime(inserted_at, '%Y-%m-%dT%H:%i:%sZ') AS ts,
        event,
        wiki,
        title,
        user,
        if(is_bot, 'true', 'false') AS is_bot,
        event_type
    FROM default.raw_events
    WHERE inserted_at >= '{START}'
      AND inserted_at < '{END}'
      AND sipHash64(event) % 100 < 10
)
