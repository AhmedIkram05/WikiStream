-- guard: (SELECT count() FROM system.tables WHERE database='default' AND name='raw_events_v1') = 1
-- header: drops the legacy table once its rows are safe in the typed
-- raw_events. Rollback affordance: may be moved to migrations/held/ (the
-- runner only reads top-level [0-9]*.sql) and moved back later; apply.sh
-- picks it up on the next boot. Default recommendation is to apply
-- immediately — the backfill is lossless and the legacy table is derivable
-- from raw_events (the event strings are intact), so the affordance is
-- cheap insurance, not a requirement.
DROP TABLE IF EXISTS default.raw_events_v1;