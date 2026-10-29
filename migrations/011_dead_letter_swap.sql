-- guard: (SELECT count() FROM system.tables WHERE database='default' AND name='dead_letter_new') = 1 AND (SELECT partition_key FROM system.tables WHERE database='default' AND name='dead_letter') = ''
-- header: atomic name swap — one RENAME TABLE statement renames BOTH sides
-- simultaneously (all-or-nothing in ClickHouse), so default.dead_letter is
-- the partitioned table from this instant on; the consumer's INSERTs never
-- see a gap. The old table survives aside as dead_letter_old for 012 to
-- drop (and as a short-lived rollback affordance, mirroring 003).
-- Guard: same precondition as 010 — skip forever once swapped.
RENAME TABLE default.dead_letter TO default.dead_letter_old, default.dead_letter_new TO default.dead_letter
