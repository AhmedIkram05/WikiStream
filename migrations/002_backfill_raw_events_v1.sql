-- guard: (SELECT count() FROM system.tables WHERE database='default' AND name='raw_events_v1') = 1
-- header: lossless, idempotent backfill of any legacy rows into the typed
-- table. LEFT ANTI JOIN dedups on (event, inserted_at), so re-runs are safe
-- — retry-safe if the runner dies between the apply POST and the
-- bookkeeping POST — and the MATERIALIZED columns compute during the
-- backfill (Q3). One statement; at ~1M rows this is seconds, and it doesn't
-- touch the live raw_events being written concurrently (the 000 rename was
-- atomic).
-- NOTE (build-time verification, 3A): the originally-planned
-- `LEFT JOIN ... WHERE r.event IS NULL` anti-join is broken on ClickHouse
-- 26.3.17 — it returns zero rows even with an empty right table (reproduced
-- via the HTTP apply path and the native client, with and without
-- query_plan_filter_push_down). LEFT ANTI JOIN is the equivalent idempotent
-- form; verified both directions (empty right -> copied; duplicate in target
-- -> excluded).
INSERT INTO default.raw_events (inserted_at, event)
SELECT v.inserted_at, v.event
FROM default.raw_events_v1 AS v
LEFT ANTI JOIN default.raw_events AS r
    ON r.event = v.event AND r.inserted_at = v.inserted_at
