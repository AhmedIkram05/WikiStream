-- guard: (SELECT count() FROM system.tables WHERE database='default' AND name='dead_letter_new') = 1 AND (SELECT partition_key FROM system.tables WHERE database='default' AND name='dead_letter') = ''
-- header: lossless backfill of the existing dead_letter rows into the
-- partitioned replacement before the 011 swap (same shape as the proven 002
-- backfill). LEFT ANTI JOIN dedups on (event, inserted_at), so a re-run
-- copies nothing twice — retry-safe if the runner dies between the apply
-- POST and the bookkeeping POST. Guard: only while the OLD unpartitioned
-- table still owns the name default.dead_letter; once 011 has swapped,
-- this is skipped forever (wiped schema_migrations replay stays clean).
INSERT INTO default.dead_letter_new (inserted_at, reason, wiki, title, event)
SELECT v.inserted_at, v.reason, v.wiki, v.title, v.event
FROM default.dead_letter AS v
LEFT ANTI JOIN default.dead_letter_new AS n
    ON n.event = v.event AND n.inserted_at = v.inserted_at
