-- guard: (SELECT count() FROM system.tables WHERE database='default' AND name='raw_events') = 1 AND (SELECT count() FROM system.columns WHERE database='default' AND table='raw_events' AND name='wiki') = 0
-- header: detects a legacy (Phase 1/2) 2-column raw_events and renames it
-- aside so 002 can backfill it into the typed table.
--   clean DB (no raw_events, or it already has the 'wiki' column)
--        -> guard 0 -> recorded 'skipped'
--   Phase 1/2 VM (raw_events without 'wiki')
--        -> guard 1 -> RENAME TABLE (atomic)
--   after 3A has run -> its typed raw_events has a 'wiki' column, so the
--        guard is 0 forever (the typed table is never renamed)
RENAME TABLE IF EXISTS default.raw_events TO default.raw_events_v1;