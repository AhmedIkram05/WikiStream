-- 001_raw_events.sql
-- Typed raw_events. All typed columns are MATERIALIZED over `event`, so the
-- consumer's insert shape (inserted_at, event) is unchanged (Q1): they
-- compute at insert and backfill, feeding the MVs, dashboards, warehouse
-- export, and Phase 4's GX checks.
-- 30-day TTL (ADR-006): raw events only; materialized aggregates are exempt.
-- max_suspicious_broken_parts = 1000: broken-parts-after-reset mitigation
-- (implementation-log §2.6) — every PR deploy is a reset, and 001 is its
-- only home now that initdb.d retires.
CREATE TABLE IF NOT EXISTS default.raw_events
(
    inserted_at    DateTime64(3, 'UTC'),
    event          String,
    wiki           String      MATERIALIZED JSONExtractString(event, 'wiki'),
    title          String      MATERIALIZED JSONExtractString(event, 'title'),
    user           String      MATERIALIZED JSONExtractString(event, 'user'),
    event_type     String      MATERIALIZED JSONExtractString(event, 'type'),
    is_bot         UInt8       MATERIALIZED JSONExtractBool(event, 'bot'),
    length_new     UInt32      MATERIALIZED JSONExtractUInt(event, 'length', 'new'),
    length_old     UInt32      MATERIALIZED JSONExtractUInt(event, 'length', 'old'),
    event_timestamp DateTime64(3, 'UTC') MATERIALIZED parseDateTime64BestEffort(JSONExtractString(event, 'timestamp'))
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(inserted_at)
ORDER BY (inserted_at, sipHash64(event))
TTL inserted_at + INTERVAL 30 DAY
SETTINGS max_suspicious_broken_parts = 1000
