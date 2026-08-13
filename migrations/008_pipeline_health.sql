-- 008_pipeline_health.sql
-- pipeline_health: single source of truth for alerting telemetry (Phase 5):
-- one row per consumer heartbeat tick (fixed source/metric, value 1.0, JSON
-- detail with per-tick counter deltas + cumulative totals) drives Grafana
-- liveness/throughput alerts. 7-day retention; daily partitions keep
-- alerting reads cheap.
CREATE TABLE IF NOT EXISTS default.pipeline_health
(
    source LowCardinality(String),
    metric LowCardinality(String),
    ts     DateTime64(3, 'UTC'),
    value  Float64,
    detail String
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(ts)
ORDER BY (source, ts)
TTL ts + INTERVAL 7 DAY
