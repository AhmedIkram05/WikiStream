-- guard: (SELECT count() FROM system.tables WHERE database='default' AND name='dead_letter_new') = 1 AND (SELECT engine FROM system.tables WHERE database='default' AND name='dead_letter') = 'MergeTree'
-- header: atomic name swap — one RENAME TABLE statement renames BOTH sides
-- simultaneously (all-or-nothing in ClickHouse), so default.dead_letter is the
-- ReplacingMergeTree from this instant on; the consumer's INSERTs never see a
-- gap. The old MergeTree table survives aside as dead_letter_old for 016 to
-- drop (and as a short-lived rollback affordance, mirroring 011).
-- Guard: same precondition as 014 — skip forever once swapped.
RENAME TABLE default.dead_letter TO default.dead_letter_old, default.dead_letter_new TO default.dead_letter
