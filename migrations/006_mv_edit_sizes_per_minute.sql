-- 006_mv_edit_sizes_per_minute.sql
-- Per-minute histogram of |length delta| buckets (ADR-006). multiIf label
-- boundaries are the contract — tests/mv asserts each of the six buckets
-- against the raw count with this exact expression. No guard line and no
-- POPULATE: CREATE ... IF NOT EXISTS is idempotent and history starts at
-- the 3B deploy.
CREATE MATERIALIZED VIEW IF NOT EXISTS default.mv_edit_sizes_per_minute
ENGINE = SummingMergeTree
ORDER BY (minute, bucket)
AS SELECT
    toStartOfMinute(inserted_at) AS minute,
    multiIf(
        abs(toInt64(length_new) - toInt64(length_old)) = 0, '0',
        abs(toInt64(length_new) - toInt64(length_old)) <= 10, '1-10',
        abs(toInt64(length_new) - toInt64(length_old)) <= 100, '11-100',
        abs(toInt64(length_new) - toInt64(length_old)) <= 1000, '101-1000',
        abs(toInt64(length_new) - toInt64(length_old)) <= 10000, '1001-10000',
        '10000+'
    ) AS bucket,
    count() AS edits
FROM default.raw_events
WHERE event_type IN ('edit', 'new') AND wiki != ''
GROUP BY minute, bucket
