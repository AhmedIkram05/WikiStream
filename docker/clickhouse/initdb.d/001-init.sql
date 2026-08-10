-- Phase 1 walking-skeleton bootstrap. Stand-in for versioned migrations (Phase 3A).
-- Spike-verified recipe (implementation-log 0.4): 26.3 `default` user is localhost-only.

CREATE USER IF NOT EXISTS wikistream IDENTIFIED WITH plaintext_password BY 'wikistream_dev_password' HOST ANY;
GRANT SELECT, INSERT, CREATE, ALTER, DROP, TRUNCATE, OPTIMIZE ON default.* TO wikistream;

CREATE TABLE IF NOT EXISTS default.raw_events (
    inserted_at DateTime64(3, 'UTC'),   -- explicit UTC: panel's now()-based window stays unambiguous
    event String
) ENGINE = MergeTree
ORDER BY inserted_at;
