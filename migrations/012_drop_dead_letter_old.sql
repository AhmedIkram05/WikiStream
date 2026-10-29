-- guard: (SELECT count() FROM system.tables WHERE database='default' AND name='dead_letter_old') = 1
-- header: drops the unpartitioned table once its rows are safe in the
-- partitioned default.dead_letter (010 copied, 011 swapped). Mirrors 003's
-- drop-with-rollback-affordance shape: may be moved to migrations/held/ and
-- moved back later; apply.sh picks it up on the next boot. Rows the consumer
-- wrote to the OLD table between the 010 copy and the 011 swap live only
-- here — dropping loses them, which is accepted at current DL volume (see
-- 009). Default recommendation is to apply immediately.
DROP TABLE IF EXISTS default.dead_letter_old
