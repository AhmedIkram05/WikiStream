-- 007_dead_letter.sql
-- Dead-letter table for validation-failed events (plan §4A): invalid JSON,
-- bad timestamps, schema violations. Written buffered (async_insert=1,
-- wait_for_async_insert=0) at-most-once like the main batch, so quality gaps
-- surface in Grafana/BigQuery instead of vanishing.
-- 90-day retention; the hash co-locates same-timestamp rows (compression/query locality) and dedups nothing —
-- plain MergeTree, and the DL write path has no dedup ring, so a replayed validation failure can land twice.
CREATE TABLE IF NOT EXISTS default.dead_letter (
    inserted_at DateTime64(3, 'UTC'),
    reason String,
    wiki String,
    title String,
    event String
) ENGINE = MergeTree
ORDER BY (inserted_at, sipHash64(event))
TTL inserted_at + INTERVAL 90 DAY
