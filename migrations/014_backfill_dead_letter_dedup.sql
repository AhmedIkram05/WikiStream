-- guard: (SELECT count() FROM system.tables WHERE database='default' AND name='dead_letter_new') = 1 AND (SELECT engine FROM system.tables WHERE database='default' AND name='dead_letter') = 'MergeTree'
-- header: lossless backfill into the ReplacingMergeTree replacement before the
-- 015 swap (same shape as the proven 002/010 backfills). LEFT ANTI JOIN on
-- (event, inserted_at) keeps a re-run retry-safe — and any row it did re-copy
-- is byte-identical, so the RMT collapses it at the next merge anyway.
-- Guard: only while the OLD MergeTree table still owns the name
-- default.dead_letter; once 015 has swapped, this is skipped forever (wiped
-- schema_migrations replay stays clean).
INSERT INTO default.dead_letter_new (inserted_at, reason, wiki, title, event)
SELECT v.inserted_at, v.reason, v.wiki, v.title, v.event
FROM default.dead_letter AS v
LEFT ANTI JOIN default.dead_letter_new AS n
    ON n.event = v.event AND n.inserted_at = v.inserted_at
