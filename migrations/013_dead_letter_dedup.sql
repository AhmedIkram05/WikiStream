-- guard: (SELECT count() FROM system.tables WHERE database='default' AND name='dead_letter') = 1 AND (SELECT engine FROM system.tables WHERE database='default' AND name='dead_letter') = 'MergeTree' AND (SELECT count() FROM system.tables WHERE database='default' AND name='dead_letter_new') = 0
-- header: durable idempotency for the dead-letter queue (Phase-6 deep-dive
-- gap). dead_letter dedups only within one process (the landed-id ring in
-- dead_letter.py), so a restart inside the replay window re-inserts an
-- already-landed row; a plain MergeTree has no idempotency key. This
-- append-only migration rebuilds the table as a ReplacingMergeTree whose
-- ORDER BY is a deterministic CONTENT fingerprint
-- sipHash64(reason, wiki, title, event) — stable across replays, unlike
-- inserted_at (non-deterministic: now() at insert time). The consumer's
-- insert shape is unchanged: same 5 columns, no extra column to write —
-- ReplacingMergeTree keys on ORDER BY, not a declared key column.
-- 013 creates the replacement, 014 copies the rows, 015 swaps the names with
-- an atomic RENAME, 016 drops the old table.
-- CAVEAT (unavoidable): ReplacingMergeTree dedups only WITHIN a partition
-- (parts from different partitions never merge), and the existing
-- toYYYYMMDD(inserted_at) partition is kept for the cheap metadata-only TTL
-- eviction — so a replayed duplicate collapses only when it lands in the
-- SAME UTC-day partition; a replay crossing midnight UTC can still duplicate.
-- Dedup also applies at background-merge time, not at insert: readers needing
-- it immediately must use FINAL or GROUP BY the fingerprint.
-- Guard: skip forever once dead_letter is already ReplacingMergeTree (engine
-- no longer MergeTree) or when the swap pair already exists.
CREATE TABLE IF NOT EXISTS default.dead_letter_new (
    inserted_at DateTime64(3, 'UTC'),
    reason String,
    wiki String,
    title String,
    event String
) ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMMDD(inserted_at)
ORDER BY sipHash64(reason, wiki, title, event)
TTL inserted_at + INTERVAL 90 DAY
