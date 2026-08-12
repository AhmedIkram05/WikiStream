-- 004_mv_edits_per_minute.sql
-- Per-minute, per-wiki edit count and net byte delta (ADR-006). No guard
-- line and no POPULATE: CREATE ... IF NOT EXISTS is idempotent and history
-- starts at the 3B deploy. Spot-check contract (tests/mv): must equal the
-- raw GROUP BY over the same window.
CREATE MATERIALIZED VIEW IF NOT EXISTS default.mv_edits_per_minute
ENGINE = SummingMergeTree
ORDER BY (minute, wiki, is_bot)
AS SELECT
    toStartOfMinute(inserted_at) AS minute,
    wiki,
    is_bot,
    count() AS edits,
    sum(toInt64(length_new) - toInt64(length_old)) AS bytes_delta
FROM default.raw_events
WHERE event_type IN ('edit', 'new') AND wiki != ''
GROUP BY minute, wiki, is_bot
