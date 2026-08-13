-- 007_dead_letter.sql
-- Dead-letter table for validation-failed events (plan §4A): invalid JSON,
-- bad timestamps, schema violations. Written with async_insert=0 (immediately
-- visible) so quality gaps surface in Grafana/BigQuery instead of vanishing.
-- 90-day retention; distinct rows per second via sipHash64(event) in ORDER BY.
CREATE TABLE IF NOT EXISTS default.dead_letter (
    inserted_at DateTime64(3, 'UTC'),
    reason String,
    wiki String,
    title String,
    event String
) ENGINE = MergeTree
ORDER BY (inserted_at, sipHash64(event))
TTL inserted_at + INTERVAL 90 DAY
