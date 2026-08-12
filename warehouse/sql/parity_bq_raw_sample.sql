SELECT COUNT(*) AS row_count
FROM wikistream.raw_events_sample
WHERE inserted_at >= TIMESTAMP('{START}') AND inserted_at < TIMESTAMP('{END}')
