SELECT
  DATE(creation_time) AS day,
  user_email,
  COUNT(*) AS jobs,
  SUM(total_bytes_processed) / 1e9 AS gb_scanned,
  SUM(total_slot_ms) / 1000 AS slot_seconds
FROM `region-US`.INFORMATION_SCHEMA.JOBS
-- ponytail: 30-day cap inside the view so every Grafana refresh scans 30d of
-- JOBS history, not the full 180d retention.
WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY 1, 2
