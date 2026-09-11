-- Raw samples have no natural key, so no MERGE key exists: idempotency is a
-- window DELETE + INSERT from raw_events_sample_staging. Re-runs replace the
-- window instead of appending duplicates. Wrapped in a transaction so a crash
-- between statements can never leave the window empty.
BEGIN TRANSACTION;
DELETE wikistream.raw_events_sample
WHERE inserted_at >= TIMESTAMP('{START}') AND inserted_at < TIMESTAMP('{END}');
INSERT wikistream.raw_events_sample (inserted_at, event, wiki, title, user, is_bot, event_type)
SELECT inserted_at, event, wiki, title, user, is_bot, event_type
FROM wikistream.raw_events_sample_staging
WHERE inserted_at >= TIMESTAMP('{START}') AND inserted_at < TIMESTAMP('{END}');
COMMIT TRANSACTION;
