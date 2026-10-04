# Local ClickHouse fixture: `legacy-raw-events.tsv.gz`

Repo-root file, **local-only** — gitignored (`.gitignore`), never committed.

## What it is

A gzipped TSV dump of raw stream events: **22.5 MB, 118,788 rows**
(sha256 `ee2063f0…5248`). Captured 2026-08-12 from the Phase-2 table on the
live VM (`docker exec` TSV export) as the bulletproof backstop behind the
`ch-data` persistent disk ahead of the 3.1.8 instance recreate. The recreate
import landed it losslessly (rows 3,168 → 122,329 — captured rows plus live
rows during import). Full story in
[docs/implementation-log.md](implementation-log.md) §3.1.8.

## Do you need it?

No, for normal local dev. `docker compose up` plus `./migrations/apply.sh`
gives you a working stack fed by the live stream. This file matters only if
you need to rebuild a table with historical-shaped data offline (e.g. after
wiping the ClickHouse volume with no network).

## Re-import pattern

Target schema lives in `migrations/001_raw_events.sql` (typed table;
`002`/`003` document the v1 backfill history). The shape used on the VM was,
in effect:

```bash
zcat legacy-raw-events.tsv.gz | clickhouse-client \
  --query "INSERT INTO default.raw_events FORMAT TabSeparated"
```

Column order must match the table — verify against `001_raw_events.sql`
before running. Prefer the checked-in `migrations/apply.sh` path whenever
the live stream is reachable; reach for this file only as the offline
backstop it was captured to be.
