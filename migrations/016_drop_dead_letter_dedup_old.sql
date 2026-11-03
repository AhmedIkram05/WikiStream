-- guard: (SELECT count() FROM system.tables WHERE database='default' AND name='dead_letter_old') = 1
-- header: drops the plain-MergeTree table once its rows are safe in the
-- ReplacingMergeTree default.dead_letter (014 copied, 015 swapped). Mirrors
-- 003/012's drop-with-rollback-affordance shape: may be moved to
-- migrations/held/ and moved back later; apply.sh picks it up on the next
-- boot. Rows the consumer wrote to the OLD table between the 014 copy and the
-- 015 swap live only here — dropping loses them, accepted at current DL
-- volume (see 013). Default recommendation is to apply immediately.
DROP TABLE IF EXISTS default.dead_letter_old
