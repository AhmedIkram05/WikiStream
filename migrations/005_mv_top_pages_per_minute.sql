-- 005_mv_top_pages_per_minute.sql
-- Per-minute, per-(title, wiki, minute) edit count and net byte delta —
-- top-pages dashboard feed (ADR-006). Composite ORDER BY (minute, title,
-- wiki): the same title on different wikis must NOT collapse. No guard line
-- and no POPULATE: CREATE ... IF NOT EXISTS is idempotent and history
-- starts at the 3B deploy.
CREATE MATERIALIZED VIEW IF NOT EXISTS default.mv_top_pages_per_minute
ENGINE = SummingMergeTree
ORDER BY (minute, title, wiki)
AS SELECT
    toStartOfMinute(inserted_at) AS minute,
    title,
    wiki,
    count() AS edits,
    sum(toInt64(length_new) - toInt64(length_old)) AS bytes_delta
FROM default.raw_events
WHERE event_type IN ('edit', 'new') AND wiki != ''
GROUP BY minute, title, wiki