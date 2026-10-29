-- guard: (SELECT count() FROM system.tables WHERE database='default' AND name='dead_letter') = 1 AND (SELECT count() FROM system.tables WHERE database='default' AND name='dead_letter_new') = 0 AND (SELECT partition_key FROM system.tables WHERE database='default' AND name='dead_letter') = ''
-- header: dead_letter PARTITION BY for cheap TTL eviction (Phase-2 audit N3
-- deferred item, Wave E B8). The 007 table is a plain MergeTree, so the
-- 90-day TTL evicts by rewriting whole parts during background merges; a
-- daily partition key makes eviction a metadata-only DROP PARTITION.
-- MergeTree cannot ALTER partitioning on an existing table, so this is an
-- append-only migration: 009 creates the replacement shape, 010 copies the
-- rows, 011 swaps the names with an atomic RENAME, 012 drops the old table.
-- The consumer keeps writing default.dead_letter throughout — schema, table
-- name and write path are unchanged.
-- The swap is instant, but the copy holds BOTH tables transiently (double
-- storage until 012 drops the old one), and rows written to the old table
-- between the 010 copy and the 011 swap stay in dead_letter_old (dropped
-- with it) — acceptable at current dead-letter volume.
-- Guard: skip forever once dead_letter is already partitioned (wiped
-- schema_migrations replay), or when the swap pair already exists.
CREATE TABLE IF NOT EXISTS default.dead_letter_new (
    inserted_at DateTime64(3, 'UTC'),
    reason String,
    wiki String,
    title String,
    event String
) ENGINE = MergeTree
PARTITION BY toYYYYMMDD(inserted_at)
ORDER BY (inserted_at, sipHash64(event))
TTL inserted_at + INTERVAL 90 DAY
