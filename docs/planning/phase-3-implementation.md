# Phase 3 Implementation Plan — Full Data Model, KPI Dashboard, Warehouse Tier

**Status:** LOCKED 2026-08-11. Decisions ratified in a grilling session with
Ahmed (questions Q1–Q12 below). This document is the input for an agentic
coding tool — every acceptance criterion is self-checkable (pass/fail an agent
can verify), per master plan §10.

**Reviewed 2026-08-11 by three parallel subagents (technical, logic,
senior-perspective review): 45 findings, all fixed.**

**Position in the hierarchy:** Master Plan §5 Phase 3 → this document. Nothing
here re-decides the ADR or master plan; it makes Phase 3 executable. Phase 3
splits into three subphases (3A schema, 3B analytics, 3C warehouse) — **planned
here in one pass, implemented in three PRs**, each merge → gated apply → VM
reset → verify its micro-gate before the next PR begins. 3A's micro-gate is a
HARD CHECKPOINT; 3B's live spot-check is the checkpoint before 3C.

---

## 1. Objective

Replace the walking skeleton's single-table/single-panel world with the full
data model: a versioned, migration-driven raw-events schema with 30-day TTL
(3A); the complete locked KPI set as ClickHouse materialized views plus the
"WikiStream Live Analytics" dashboard (3B); and a live BigQuery warehouse tier
with hourly export, freshness panel, and a scheduled parity check (3C). This is
the phase where WikiStream becomes a *real* data engineering project: versioned
schema evolution, aggregation guarantees, dashboards-as-code, and a
two-tier hot/cold architecture with cross-tier parity verification.

## 2. Scope

| In | Deliberately out (later phase) |
| --- | --- |
| **3A:** Versioned, numbered DDL migrations + bash runner + `schema_migrations` table (Q2); full typed `raw_events` schema with MATERIALIZED columns (Q1); lossless backfill of live data (Q3); 30-day TTL on raw events only (ADR-006); startup-script user bootstrap incl. the rotation-gap fix | Pydantic event models / dead-letter / restart-resume (Phase 4) |
| **3A:** Retire `docker/clickhouse/initdb.d` (repo file, compose mount, startup heredoc + checkout fence) | GX suite against typed columns (Phase 4) |
| **3A:** CI: CH-dependent test matrix entry (`analytics-tests`) + `@pytest.mark.ch`; compose-smoke gains bootstrap+migrate steps | Backups / native BACKUP restore (Phase 4) |
| **3A:** Durable ch-data disk (compute module: 30GB pd-standard, `prevent_destroy`) + startup.sh mount block + compose `${CH_DATA_DIR:-ch-data}` bind-mount — data survives the ForceNew VM recreate that the startup.sh edit triggers; pre-merge TSV capture/import of live data (Q3) | — |
| **3B:** MVs 004–006 (SummingMergeTree, per-minute, NO POPULATE — history starts at 3B deploy, Q4); MV equivalence pytest suite + one-time live spot-check (Q5) | New KPI discovery mid-build — the KPI list is locked; build exactly it (master plan §7) |
| **3B:** `wikistream-live` dashboard (uid `wikistream-live`, title "WikiStream Live Analytics" — WikiPulse is the deprecated name) replacing `phase1.json`; $window variable (1h/24h) | Ingestion scope filter (Wikipedia-only, exclude Wikidata/Commons) — Phase 4 decision |
| **3C:** Hourly-grain BQ rollups of the 3 MV aggregates + 10% deterministic raw sample (Q12) + `export_runs` metadata table (Q6); DATE-partitioned, explicit schema JSON files committed | BQ as hot path — Grafana keeps reading ClickHouse; BQ is a batch-query tier (ADR-003) |
| **3C:** `export.sh` (docker exec clickhouse-client → JSONL → `gcloud storage cp` → `bq load`, Q7); systemd export timer hourly :00 + parity timer :05 (exits non-zero on drift — Phase 5 alert hook) | Alerting/notifications on parity failure (Phase 5 — the exit code is the hook) |
| **3C:** Terraform `bigquery` module (5th module, ADR-007 extended): dataset + 5 tables + staging bucket (7-day lifecycle) + VM SA dataset-scoped `dataEditor` + bucket-scoped objectCreator/objectViewer (Q8); `bigquery.googleapis.com` in bootstrap (manual apply) | Grafana alert rules, Cloud Monitoring (Phase 5); cost FinOps note (Phase 7b) |
| **3C:** `grafana-bigquery-datasource` (GCE auth, `GF_PLUGINS_PREINSTALL`) + warehouse-freshness panel on the 3B dashboard (ADR-010) | — |

## 3. Locked decisions (from the grilling session)

| # | Decision |
| --- | --- |
| Q1 | **Typed columns are MATERIALIZED expressions over `event`.** `raw_events` keeps the consumer's insert shape `(inserted_at, event)` (consumer.py:68–86 confirmed — zero consumer change in Phase 3); typed columns compute at insert/backfill. Phase 4 Pydantic stays Phase 4. |
| Q2 | **Migrations = bash runner + `schema_migrations` version table + CI job.** Runner (`migrations/apply.sh`) applies pending `*.sql` files in sorted order, skips applied; CI proves clean-DB apply + re-runnable + TTL present. |
| Q3 | **Live VM data is preserved, not discarded — via BOTH a durable data disk and a TSV capture.** `metadata_startup_script` is ForceNew in the google provider (log §2.6, empirically proven): the 3A startup.sh edit recreates the instance and wipes the boot-disk volume, so 3A first attaches a persistent 30GB `ch-data` disk (bind-mounted at `/mnt/ch-data/clickhouse`, `prevent_destroy` — data survives every future recreate), AND a pre-merge TSV capture (count → export → gzip → scp) of the live table is imported post-deploy as belt-and-braces (docker exec `-i`). On the VM the `RENAME + INSERT SELECT` backfill path is moot (no legacy table survives the recreate) — its real exercise is CI/local synthetic legacy + `test_legacy_migration`. |
| Q4 | **KPI tables = per-minute SummingMergeTree + query-time rolling windows (1h/24h)** — NOT AggregatingMergeTree. Window size is a dashboard variable, not a table. |
| Q5 | **MV correctness = pytest equivalence suite (MV output == raw GROUP BY for same window) + one-time live spot-check** after 3B deploy. This is ADR-006's named risk ("silently-wrong MV") closed by assertion, not by eyeball. |
| Q6 | **BQ layer shape:** hourly-grain rollups of the 3 MV aggregates + 10% raw sample + `export_runs` metadata table (exported_at, window_start, window_end, status, per-table row counts) — powers the freshness panel + parity check. DATE-partitioned; explicit schema JSON committed to repo. **Delivery semantic (named, pre-empting the classic interview question): at-least-once with idempotent per-window re-export** (windowed DELETE + reload remediates a re-exported window; drift is detected by the parity check; exactly-once is not claimed). |
| Q7 | **Export mechanism = bash + `docker exec` clickhouse-client + `gcloud`/`bq` CLIs** — zero new host dependencies (Cloud SDK already installed by startup.sh). SQL lives as committed repo files with `{START}`/`{END}` placeholders; the same files are exercised by the pytest suite and parity.sh (one source of truth). **Recorded deviation from master plan §5's mechanism wording** ("existing clickhouse-connect client"): docker exec uses the in-container client, so the host needs no CH client dependency — the parity path reuses the SAME committed SQL shapes via BQ-dialect twin queries (`parity_bq_*.sql`), comparing windowed SUMS, never row counts (grains differ: BQ hourly vs CH minute). |
| Q8 | **BQ IAM/APIs:** VM SA gets `roles/bigquery.dataEditor` on the dataset ONLY (covers the datasource's queries via bigquery.jobs.create) + `storage.objectCreator` + `storage.objectViewer` scoped to the staging bucket ONLY. `bigquery.googleapis.com` is the single new API (bootstrap, manual apply). Staging bucket has a 7-day object lifecycle rule. |
| Q9 | **Delivery: 3 PRs (3A → 3B → 3C), each through the existing gated apply + VM reset.** NO full destroy/reapply. **Gate 1 is ALREADY recorded as GO with a documented caveat in implementation-log §2.8** (the destroy leg was skipped per Ahmed's Phase 2 call; stability evidenced by repeated applies runs 2–4, reset→recover, and CI gate reliability) — task 3.1.8 confirms and cross-references that record, it is NOT re-recorded. The log's cold-reapply recommendation is answered by: the 3A deploy itself proves apply-from-current-state, AC1's clean-DB apply is proven in CI, and the new ch-data disk makes future recreations data-safe. |
| Q10 | **One dashboard replaces `phase1.json` (deleted):** title "WikiStream Live Analytics" (the project is WikiStream; WikiPulse is deprecated), uid `wikistream-live`. 5 ClickHouse panels in 3B; the BQ warehouse-freshness panel is added to the SAME dashboard in 3C. |
| Q11 | **Coverage boundary extends to Phase 3 artifacts:** `migrations/` + `warehouse/` SQL and the equivalence assertions (tests/migrations, tests/mv, tests/warehouse) join the business-critical 100% story; the thin bash wrappers stay outside the coverage gate (their exit codes are verified by the parity timer's runtime behavior). |
| Q12 | **Raw sample ratio = 10%, deterministic** via `sipHash64(event) % 100 < 10`. Ownership: Ahmed deferred the arithmetic, NOT the design — the constraint set (deterministic, reproducible, bounded growth, representative) and the 10%-vs-5/25 reasoning are this plan's. Rationale: 100% makes BQ a second raw store that grows forever while the hot tier TTLs raw at 30 days — a design contradiction; 10% tells the coherent CV story (hot tier = full-fidelity 30-day window; warehouse = lifetime hourly rollups + statistically-representative deterministic sample). Why 10% and not 5/25: the ratio is sized to the story (hot = full fidelity for 30 days, cold = representative lifetime sample), not to the data. At the measured ≈44 ev/s (Phase 2 live run: 431→3308 rows over 65s) that's ≈ 3.8M events/day → **sample ≈ 380k rows/day**. |

Plus handoffs that are **constraints, not choices**:

- **The KPI list is locked** (research notes): rolling edit velocity (per minute/hour) by project and language; bot vs human edit ratio; top-edited pages (rolling window); edit size (bytes changed) distribution; project/language distribution. New-page-rate metric is OUT (page-create stream, only if time allows — it doesn't). Build exactly this list.
- 30-day TTL applies to raw events ONLY; materialized aggregates are exempt (ADR-006).
- MVs use NO POPULATE — MV history starts at 3B deploy; POPULATE with concurrent inserts risks double-counting the overlap window, and at ≈44 ev/s catch-up is minutes (live dashboard fills within minutes of deploy).
- Dashboard JSON `"format"` is the numeric enum (`0` = timeseries, `1` = table) — the string `"time_series"` 500s on plugin 4.20.0 (Phase 1 fix carries forward; panel-level enum verified at build).
- Grafana's env interpolator has NO `:-default` support — provisioning yaml uses plain `${VAR}`; compose supplies defaults (Phase 1 contract).
- `GF_INSTALL_PLUGINS` is broken in Grafana 13.1.1 — `GF_PLUGINS_PREINSTALL` is the only path (Phase 1 fix carries forward).
- `infra/bootstrap` is applied MANUALLY with local state (same ceremony as log 0.2); CI never touches bootstrap.
- Consumer log contract + count()-sample protocol carry forward unchanged; transient `insert_failed`/`clickhouse_unavailable` WARNINGs during CH cold-start and the migration window are the documented resilience contract, NOT failures (no Tracebacks).
- The deploy SA's CI identity, WIF, AR repo, SM secrets, firewall, static IP all carry forward untouched.
- ADR-011 non-goals: no Kafka, no Airflow, no Prometheus, no ML.
- **Executor-level design note (from the CI mechanics of retiring initdb.d):** CI/local ClickHouse containers lose their user with initdb.d gone. `migrations/bootstrap-user.dev.sql` (committed, dev credential = the compose default `wikistream_dev_password`) bootstraps the user in CI via `docker compose exec ... --multiquery`; the VM path is boot.sh's heredoc with the REAL secret. Keep the two SQL bodies in sync (one rule, noted in the file header). **Two execution paths, one deliberate split:** host curl for migrations (the `wikistream` user reaches the HTTP API); docker exec for user bootstrap (the localhost-only `default` user must run inside the container).

## 4. Prerequisites

- Phase 2 exit criteria met (log) **and** Gate 1 recorded GO-with-caveat — ALREADY on record in implementation-log §2.8; task 3.1.8 confirms + cross-references it (log §2.7's cold-reapply recommendation is answered by the 3A deploy + AC1's CI clean-DB apply + the ch-data disk).
- `infra/bootstrap` + `infra/main` in their applied state from Phase 2 run #4 (VM running, static IP 34.148.138.220, stack green).
- **Build-time re-verification checklist** (Vision §9; verify at build):

| Item | Status |
| --- | --- |
| `parseDateTime64BestEffort(JSONExtractString(event,'timestamp'))` handles the trailing `Z` (Wikimedia timestamps are `2026-08-11T12:34:56Z`). If not, the fix is a NEW migration, not an edit of 001: `ALTER TABLE default.raw_events MODIFY COLUMN event_timestamp DateTime64(3,'UTC') MATERIALIZED <fallback expr>` + `ALTER TABLE default.raw_events MATERIALIZE COLUMN event_timestamp` (recomputes existing rows) — schema evolution, the runner handles it | verify at build with a real event; outcome logged |
| clickhouse HTTP API via curl: `curl -sS --fail-with-body -H 'X-ClickHouse-User: wikistream' -H 'X-ClickHouse-Key: ...' -X POST --data-binary @file 'http://host:8123/'` — response 200 = success; errors return 4xx with body; header auth keeps the password out of the URL | ✅ standard; verify at build |
| `docker compose exec -T clickhouse clickhouse-client --multiquery` accepts SQL on stdin (user bootstrap path, host → container → localhost default user) | ✅ standard; verify at build |
| **`metadata_startup_script` is ForceNew** (log §2.6, empirically proven): any startup.sh edit recreates the instance with a fresh boot disk — hence ch-data on a separate `prevent_destroy` disk (3.1.1) and NO startup.sh edits after 3A (3C lands in boot.sh) | ✅ by design; survival proven by the 3A recreate itself |
| `docker exec -i` (stdin flag) required whenever clickhouse-client reads piped input (TSV import, export SELECTs, SYSTEM FLUSH) — `docker compose exec -T` is for the no-stdin bootstrap path | ✅ standard; asserted in tests |
| `SYSTEM FLUSH ASYNC INSERT QUEUE` (before export SELECTs) — the consumer's `async_insert=1` can lag ~10s behind the :00 window boundary, which would false-drift parity | ✅ standard; verify on VM at first export |
| `SETTINGS max_suspicious_broken_parts = 1000` on the 001 DDL — log §2.6's broken-parts-after-reset mitigation; every PR deploy is a reset, and 001 is its only home now that initdb.d retires | ✅ standard; asserted via AC3-adjacent SHOW CREATE |
| `sipHash64(event) % 100 < 10` — sipHash64 returns UInt64; modulo over the full hash space gives ~10% | ✅ deterministic; verify counts in CI test |
| ClickHouse `JSONExtractBool` → UInt8; `JSONExtractUInt(event,'length','new')` nested-path syntax; `multiIf` bucket expressions | ✅ standard; covered by tests/mv |
| `SHOW CREATE TABLE default.raw_events` renders `TTL inserted_at + toIntervalDay(30)` (or `TTL inserted_at + INTERVAL 30 DAY` — assert either form) | verify exact string at build; AC3 asserts via `system.tables.ttl_expression` (canonical form) |
| SummingMergeTree direct SELECT may return unmerged duplicate rows — ALL panel/export/parity SQL must aggregate (`sum()`) rather than trust raw rows | ✅ by design; assert in tests/mv |
| JSONEachRow timestamp rendering: DateTime64 → `2026-08-11 12:00:00.000` (space form). **The bq JSON loader requires RFC 3339 (`T`); space-form is CSV-only** — so the cast is UNCONDITIONAL design, not a fallback: every timestamp column in the export SQL is wrapped `formatDateTime(..., '%Y-%m-%dT%H:%i:%sZ')` (schema stays TIMESTAMP) | by design; asserted in tests/warehouse |
| `bq load --source_format=NEWLINE_DELIMITED_JSON --time_partitioning_field=<ts> --schema=warehouse/schemas/<table>.json` — BOOL columns: the JSON parser accepts `true`/`false` (not `0`/`1`) — the export SQL UNCONDITIONALLY casts `if(is_bot, 'true', 'false') AS is_bot` | by design; asserted in tests/warehouse |
| `google_bigquery_table` with `schema = file("${path.module}/../../../../warehouse/schemas/<name>.json")` (spelled-out relative path to the repo's warehouse/schemas/ — the single source) + `time_partitioning { type = "DAY", field = <ts> }` | ✅ shape confirmed from provider docs; verify exact attribute names at build |
| `gcloud storage cp` (new client, ships with google-cloud-cli) + `gcloud storage ls` | ✅ standard; verify at build |
| systemd timer syntax `OnCalendar=*-*-* *:00:00` + `Persistent=true` (catches missed runs after VM downtime) | ✅ standard; verify at build |
| grafana-bigquery-datasource **3.2.0** (latest, 2026-06-17; requires Grafana ≥ 11.6 — compatible with 13.1.1); plugin id is `grafana-bigquery-datasource` (`google-bigquery-datasource` 404s); config `jsonData: {authenticationType: gce, defaultProject}` | ✅ verified 2026-08-11; pin exactly 3.2.0 |
| `GF_PLUGINS_PREINSTALL` comma-list format: `grafana-clickhouse-datasource@4.20.0,grafana-bigquery-datasource@3.2.0` | ✅ standard; verify at build |
| GCE-auth datasource only works on the VM (metadata server). Local dev compose will preinstall the plugin + provision the datasource but queries fail — expected, documented (§9) | ✅ known |
| **Every checklist item gets a log outcome line** in implementation-log Phase 3: "confirmed standard" vs "fallback used: X" (also folded into AC21) — the checklist is evidence, not ambiance | each phase-final task records it |
| Runner globs `[0-9]*.sql` ONLY (bootstrap-user.dev.sql is never seen); `schema_migrations` has a `status` column; skipped-guard files ARE recorded (status `skipped`) so AC1's count == number-of-files holds in every environment | ✅ design; asserted in tests/migrations |

## 5. Target file structure

```
scripts/
  boot.sh                            # NEW (3A): post-compose-up seam — CH wait, user bootstrap (heredoc echoed to log), migrations; 3C adds systemd install HERE. startup.sh is never edited again after 3A (ForceNew)
migrations/                          # NEW (3A fills; 3B extends; 3C extends)
  apply.sh                           # bash runner: curl → CH HTTP API, schema_migrations bookkeeping (status column, record-all)
  bootstrap-user.dev.sql             # dev-credential user bootstrap for CI/local (VM = boot.sh heredoc)
  000_detect_legacy.sql              # guard + RENAME raw_events → raw_events_v1
  001_raw_events.sql                 # full schema, MATERIALIZED columns, 30-day TTL, max_suspicious_broken_parts
  002_backfill_raw_events_v1.sql     # guard + idempotent LEFT-JOIN backfill
  003_drop_raw_events_v1.sql         # guard + DROP v1 (holdable for rollback, see 3.1.3)
  004_mv_edits_per_minute.sql        # NEW (3B)
  005_mv_top_pages_per_minute.sql    # NEW (3B)
  006_mv_edit_sizes_per_minute.sql   # NEW (3B)
warehouse/                           # NEW (3C)
  export.sh                          # hourly export: CH → JSONL → GCS → bq load → export_runs (SCRIPT_DIR-relative paths)
  parity.sh                          # :05 parity: BQ vs CH windowed SUMS + freshness; exit non-zero on drift
  sql/export_edits.sql               # {START}/{END} placeholders; shared by export/parity/tests
  sql/export_top_pages.sql
  sql/export_sizes.sql
  sql/export_raw_sample.sql
  sql/parity_bq_edits.sql            # BQ-dialect twin queries for parity (SUM over window — grain differs from CH)
  sql/parity_bq_top_pages.sql
  sql/parity_bq_sizes.sql
  sql/parity_bq_raw_sample.sql
  schemas/{kpi_edits_hourly,kpi_top_pages_hourly,kpi_edit_sizes_hourly,raw_events_sample,export_runs}.json  # SINGLE source — TF module reads these via file() (see 3.3.2)
  wikistream-export.{service,timer}  # OnCalendar *:00:00, Persistent=true, Type=oneshot
  wikistream-parity.{service,timer}  # OnCalendar *:05:00, Persistent=true, Type=oneshot
tests/
  migrations/test_migrations.py      # NEW (3A, @pytest.mark.ch)
  mv/test_mv_equivalence.py          # NEW (3B, @pytest.mark.ch)
  warehouse/test_export_parity.py    # NEW (3C, @pytest.mark.ch)
grafana/
  dashboards/wikistream-live.json    # NEW (3B; 3C adds freshness panel) — phase1.json DELETED in 3B
  provisioning/datasources/bigquery.yaml  # NEW (3C)
  provisioning/dashboards/dashboards.yaml  # 3B: provider name "phase1" → "wikistream" (cosmetic)
docker/
  clickhouse/initdb.d/               # DELETED (3A) incl. 001-init.sql; compose mount removed
docker-compose.yml                   # 3A: initdb.d mount gone + CH_DATA_DIR bind-mount; 3C: GF_PLUGINS_PREINSTALL extended
infra/main/
  main.tf                            # 3C: labels.phase → "3", module "bigquery" wiring
  modules/compute/compute.tf         # 3A: ch-data disk + attachment (prevent_destroy)
  modules/bigquery/                  # NEW (3C): bigquery.tf, variables.tf (schemas via file() → warehouse/schemas)
  templates/startup.sh               # 3A ONLY: fence + heredoc + §2 comments removed; mount block; boot.sh invocation.
                                     # NO edits after 3A — 3C lands in boot.sh (startup.sh edit = ForceNew recreate)
infra/bootstrap/main.tf              # 3C: + "bigquery.googleapis.com" in apis for_each
.github/workflows/ci.yml             # 3A: analytics-tests entry, -m "not ch", markers, smoke reorder
                                     # 3B: smoke AC5 string → "WikiStream Live Analytics"
pytest.ini                           # 3A: markers = ch
docs/planning/coverage-boundary.md   # 3B/3C: corrected migrations path + warehouse row
docs/implementation-log.md           # each PR's evidence (incl. per-§4-item outcomes)
```

## 6. Tasks

Task tracker — pre-populated 2026-08-11 for implementation. Flip each row's
status as the task is worked; task detail below; evidence and numbers land in
`docs/implementation-log.md` Phase 3 (task headings pre-populated there too).

| # | Task | Status |
| --- | --- | --- |
| 3.1.1 | Durable ch-data disk: compute module disk + attach (prevent_destroy), startup.sh mount block, compose CH_DATA_DIR bind-mount (Q3) | ☐ to do |
| 3.1.2 | Migration runner + `schema_migrations` (status column, record-all) + bootstrap-user.dev.sql (Q2) | ☐ to do |
| 3.1.3 | Migration files 000–003 (Q1/Q3; 001 += broken-parts setting; 002 idempotent) | ☐ to do |
| 3.1.4 | Startup rework: boot.sh seam + user bootstrap + rotation fix + migrations (Q2) | ☐ to do |
| 3.1.5 | Retire initdb.d: repo file, compose mount, fence | ☐ to do |
| 3.1.6 | CI: analytics-tests entry (-m ch), markers, smoke reorder | ☐ to do |
| 3.1.7 | tests/migrations suite | ☐ to do |
| 3.1.8 | 3A PR → pre-merge TSV capture → deploy → TSV import → **micro-gate** → log (Gate 1 record cross-referenced) | ☐ to do |
| 3.2.1 | MV migrations 004–006 (Q4) | ☐ to do |
| 3.2.2 | tests/mv equivalence suite (Q5) | ☐ to do |
| 3.2.3 | `wikistream-live` dashboard (5 panels) + delete phase1.json + ci.yml AC5 string (Q10) | ☐ to do |
| 3.2.4 | 3B PR → gated deploy → **live spot-check** → log | ☐ to do |
| 3.2.5 | Docs name sweep: WikiPulse → WikiStream (master-plan, vision-and-adr, others) | ☐ to do |
| 3.3.1 | Bootstrap: `bigquery.googleapis.com` (manual apply) (Q8) | ☐ to do |
| 3.3.2 | `modules/bigquery`: dataset, 5 tables, IAM + main.tf wiring, labels phase 3 (schemas = single source) | ☐ to do |
| 3.3.3 | warehouse/sql + warehouse/schemas + parity_bq twins (Q6/Q7) | ☐ to do |
| 3.3.4 | export.sh + parity.sh (Q7) | ☐ to do |
| 3.3.5 | systemd units (Type=oneshot) + timers + boot.sh install step | ☐ to do |
| 3.3.6 | compose plugin preinstall + bigquery datasource yaml + freshness panel (ADR-010) | ☐ to do |
| 3.3.7 | tests/warehouse suite | ☐ to do |
| 3.3.8 | 3C PR → gated deploy → verification battery → log + coverage-boundary | ☐ to do |

---

### 3A — Schema (PR 1)

### 3.1.1 — Durable ch-data disk (Q3; log §2.6 ForceNew fix)

Why: `metadata_startup_script` is ForceNew (log §2.6, empirically proven) — the
3A startup.sh edit recreates the instance with a fresh boot disk, wiping the
named `ch-data` volume. This task makes data survive instance lifecycle
("compute is cattle, data is a pet") and implements log §2.6's own
recommendation.

**`infra/main/modules/compute/compute.tf`:**
- `google_compute_disk "ch_data"`: name `ch-data`, type `pd-standard`, size
  30GB, zone `var.zone`, `lifecycle { prevent_destroy = true }` (protected like
  the tfstate bucket).
- `google_compute_attached_disk`: instance `google_compute_instance.vm.name`,
  disk = disk id, `device_name = "ch-data"` (GCE device
  `/dev/disk/by-id/google-ch-data`).
- Module wiring unchanged (compute module already receives zone/labels).

**`infra/main/templates/startup.sh`** — stable mount block (added once at 3A,
before the .env render; idempotent across reboots):

```sh
DEV=/dev/disk/by-id/google-ch-data
if [ -b "$DEV" ]; then
  if ! blkid "$DEV" >/dev/null 2>&1; then mkfs.ext4 "$DEV"; fi
  UUID=$(blkid -s UUID -o value "$DEV")
  grep -q "$UUID" /etc/fstab || echo "UUID=$UUID /mnt/ch-data ext4 defaults,nofail 0 2" >> /etc/fstab
  mountpoint -q /mnt/ch-data || mount /mnt/ch-data
  mkdir -p /mnt/ch-data/clickhouse
  export CH_DATA_DIR=/mnt/ch-data/clickhouse
fi
```

(`nofail` keeps boot working if the disk is ever detached.) The .env render
adds `CH_DATA_DIR` when set.

**`docker-compose.yml`:** volume line becomes
`${CH_DATA_DIR:-ch-data}:/var/lib/clickhouse` (env-interpolated: local dev
keeps the named volume; the VM bind-mounts the durable disk; the
`volumes: ch-data:` declaration stays for local dev).

**Verify:** `terraform plan` shows disk + attachment; on a fresh boot: startup
log shows the mount block, `df /mnt/ch-data` exists, `docker inspect
wikistream-clickhouse` shows the bind mount; after the 3A recreate the data
(imported TSV + new inserts) survives.

### 3.1.2 — Migration runner + `schema_migrations` (Q2)

**`migrations/apply.sh`** — bash, no ClickHouse client binary: speaks to the
CH HTTP API via `curl`, so it runs identically on the VM host, CI runner, and
laptop. `set -euo pipefail`. Env: `CH_HOST` (default `localhost`), `CH_PORT`
(default `8123`), `CH_USER` (default `wikistream`), `CH_PASSWORD` (required;
`MIGRATIONS_DIR` default = script's own `migrations/` dir). Behavior:

1. Readiness wait: retry loop (30 × 2s) on `SELECT 1` over HTTP as `$CH_USER`
   (same header auth + `--fail-with-body` as the apply step, so an auth error
   is not-ready, not ready) — fails non-zero if never ready.
2. Ensure bookkeeping: `CREATE TABLE IF NOT EXISTS default.schema_migrations
   (version String, status String, applied_at DateTime DEFAULT now())
   ENGINE = MergeTree ORDER BY version`.
3. For each `*.sql` in `$MIGRATIONS_DIR` matching `[0-9]*.sql` (sorted;
   `bootstrap-user.dev.sql` is never seen):
   - version = file stem (e.g. `001_raw_events`); skip if recorded in
     `schema_migrations`.
   - Optional guard: if the file's FIRST line matches `-- guard: <expr>`, run
     `SELECT <expr>` — apply iff the single result is `1`.
   - Apply: `curl -sS --fail-with-body -H "X-ClickHouse-User: ${CH_USER}" -H
     "X-ClickHouse-Key: ${CH_PASSWORD}" -X POST --data-binary @<file>
     "http://${CH_HOST}:${CH_PORT}/"` — `--fail-with-body` makes any non-2xx
     (incl. 516 auth errors) exit non-zero; header auth keeps the password out
     of the URL/argv. Non-200 → print body, exit non-zero (a half-applied
     migration is retry-safe: `IF NOT EXISTS`/`IF EXISTS` bodies are
     idempotent; 002's backfill is the LEFT-JOIN idempotent form, see 3.1.3).
   - Record EVERY file after evaluation: `INSERT INTO
     default.schema_migrations (version, status) VALUES ('<v>', '<applied|
     skipped>')` — a guard-0 file is recorded as `skipped`, NOT left out
     (AC1's count == number of files holds in every environment; no guard in
     this set can ever flip back to 1 after 003 drops v1).
4. Print a per-file SKIP/APPLY line (the log's evidence); exit 0.

**`migrations/bootstrap-user.dev.sql`** — committed, dev credential:
`CREATE USER IF NOT EXISTS wikistream IDENTIFIED WITH plaintext_password BY
'wikistream_dev_password' HOST ANY;` + `GRANT SELECT, INSERT, CREATE, ALTER,
DROP, TRUNCATE, OPTIMIZE ON default.* TO wikistream;` + `ALTER USER IF EXISTS
wikistream IDENTIFIED WITH plaintext_password BY 'wikistream_dev_password'
HOST ANY;` — header comment: "dev copy; the VM path is startup.sh's heredoc
with the real secret — keep in sync."

**Verify:** local `docker compose up -d clickhouse` → `docker compose exec -T
clickhouse clickhouse-client --multiquery < migrations/bootstrap-user.dev.sql`
→ `CH_PASSWORD=wikistream_dev_password ./migrations/apply.sh` → all files
APPLY, exit 0; second run → all SKIP, exit 0. (Full assertion in 3.1.6.)

### 3.1.3 — Migration files 000–003 (Q1/Q3)

Guard convention: first line `-- guard: <expr>`; runner evaluates `SELECT
<expr>`; apply iff result is `1`.

**`000_detect_legacy.sql`** — guard: `(SELECT count() FROM system.tables WHERE
database='default' AND name='raw_events') = 1 AND (SELECT count() FROM
system.columns WHERE database='default' AND table='raw_events' AND
name='wiki') = 0`. Body: `RENAME TABLE IF EXISTS default.raw_events TO
default.raw_events_v1`. (On a clean DB the guard is 0 → recorded as `skipped`;
on the VM it renames the Phase 1/2 table; after 3A it's 0 forever — the legacy
exercise lives in CI/local + the 3.1.8 TSV capture. Re-runnable by
construction.)

**`001_raw_events.sql`** — no guard (CREATE IF NOT EXISTS is idempotent):

```sql
CREATE TABLE IF NOT EXISTS default.raw_events (
    inserted_at   DateTime64(3, 'UTC'),
    event         String,
    wiki          String MATERIALIZED JSONExtractString(event, 'wiki'),
    title         String MATERIALIZED JSONExtractString(event, 'title'),
    user          String MATERIALIZED JSONExtractString(event, 'user'),
    event_type    String MATERIALIZED JSONExtractString(event, 'type'),
    is_bot        UInt8   MATERIALIZED JSONExtractBool(event, 'bot'),
    length_new    UInt32  MATERIALIZED JSONExtractUInt(event, 'length', 'new'),
    length_old    UInt32  MATERIALIZED JSONExtractUInt(event, 'length', 'old'),
    event_timestamp DateTime64(3, 'UTC')
        MATERIALIZED parseDateTime64BestEffort(JSONExtractString(event, 'timestamp'))
) ENGINE = MergeTree
PARTITION BY toYYYYMMDD(inserted_at)
ORDER BY (inserted_at, sipHash64(event))
TTL inserted_at + INTERVAL 30 DAY
SETTINGS max_suspicious_broken_parts = 1000
```

Notes: consumer insert shape `(inserted_at, event)` untouched (Q1); typed
columns are for MVs, dashboards, the warehouse export, and Phase 4's GX checks
(event_timestamp makes "timestamp not stale/future" possible). Verify
`parseDateTime64BestEffort` handles the trailing `Z` at build (fallback path
documented in §4 — a new migration, not an edit of 001). `(inserted_at,
sipHash64(event))` order key keeps the table partition-friendly and cheap to
dedupe later (Phase 4). `max_suspicious_broken_parts = 1000` is log §2.6's
broken-parts-after-reset mitigation — every PR deploy is a reset, and 001 is
its only home now that initdb.d retires.

**`002_backfill_raw_events_v1.sql`** — guard: `(SELECT count() FROM
system.tables WHERE database='default' AND name='raw_events_v1') = 1`. Body
(idempotent — retry-safe if the runner dies between the apply POST and the
bookkeeping POST):

```sql
INSERT INTO default.raw_events (inserted_at, event)
SELECT v.inserted_at, v.event
FROM default.raw_events_v1 v
LEFT JOIN default.raw_events r ON r.event = v.event AND r.inserted_at = v.inserted_at
WHERE r.event IS NULL
```

MATERIALIZED columns compute during backfill — lossless (Q3). One statement;
at ~1M rows this is seconds. Consumer keeps inserting into the live
`raw_events` during the backfill — no conflict (different source tables; the
rename in 000 is atomic). On the VM post-recreate there IS no `raw_events_v1`
(3.1.1 wipe) → the guard is 0 → recorded `skipped`; the VM's legacy story is
the 3.1.8 TSV capture/import.

**`003_drop_raw_events_v1.sql`** — guard: same as 002. Body:
`DROP TABLE IF EXISTS default.raw_events_v1`. **Rollback affordance:** the file
ships in the PR; if the executor wants the hold-back window (until 3B's live
spot-check passes), move the file to `migrations/held/` (runner only reads
top-level `*.sql`) and move it back afterwards — apply.sh will pick it up on
the next boot. Default recommendation: apply immediately — the backfill is
lossless and the legacy table is derivable from `raw_events` (event strings
intact), so the affordance is cheap insurance, not a requirement.

**Verify:** 3.1.7's suite covers all four paths (clean, legacy, re-run). On the
VM: after deploy + TSV import, `SELECT count() FROM raw_events` == the
captured pre-3A count (3.1.8); `SELECT count() FROM raw_events WHERE wiki !=
''` > 0 (typed columns populated).

### 3.1.4 — Startup rework: boot.sh seam + user bootstrap + rotation fix (Q2)

**`infra/main/templates/startup.sh`** — edited ONCE here; never again after 3A
(any startup.sh edit is a ForceNew recreate):

- DELETE the initdb.d checkout-fence line, the §2 comment block explaining it,
  and §5 (the initdb.d heredoc render) — the rendered-secret file no longer
  exists, so the tree stays clean and the dead-mechanism comments go with it.
- KEEP: §0/§1 installs, pull-or-clone, `chmod -R a+rX` (still needed for grafana
  provisioning traversal), metadata project-id, .env render (now incl.
  CH_DATA_DIR from 3.1.1), configure-docker.
- The single `docker compose up -d --no-build` stays (NO two-phase start —
  CI's compose-smoke mirrors this exact order: up → boot.sh → polls; the
  consumer's `clickhouse_unavailable`/`insert_failed` WARNINGs in the
  boot-shim gap are the documented Phase 1 resilience contract, not failures —
  zero Tracebacks, so Phase 1 AC2 still passes).
- Last step before `startup done`: `bash /opt/wikistream/scripts/boot.sh`
  (git-tracked, pulled at boot). `set -euo pipefail` → boot.sh failure fails
  startup loudly (log ends without `startup done`).

**`scripts/boot.sh`** (NEW):
1. Wait CH ready: `docker compose exec -T clickhouse clickhouse-client --query
   "SELECT 1"` retry 30 × 2s (first boot CH needs ~10–20s).
2. **User bootstrap** (AC7's grep target). Default user is localhost-only →
   must run inside the container; this is why the bootstrap path is docker
   exec, not host curl (host curl is for migrations, where the `wikistream`
   user reaches the HTTP API). The SQL is built in a variable FIRST (so
   `${CH_PASSWORD}` expands — a quoted heredoc would pipe the literal text),
   echoed to the log with the password redacted (/var/log is world-readable),
   then piped into the client (the client's stdout for CREATE/GRANT/ALTER is
   EMPTY — echoing the SQL text itself is what puts the ALTER USER statement
   in the log for AC7):

   ```
   BOOTSTRAP_SQL="CREATE USER IF NOT EXISTS wikistream IDENTIFIED WITH plaintext_password BY '${CH_PASSWORD}' HOST ANY;
   GRANT SELECT, INSERT, CREATE, ALTER, DROP, TRUNCATE, OPTIMIZE ON default.* TO wikistream;
   ALTER USER IF EXISTS wikistream IDENTIFIED WITH plaintext_password BY '${CH_PASSWORD}' HOST ANY;"
   echo "user bootstrap: $(echo "$BOOTSTRAP_SQL" | sed "s/${CH_PASSWORD}/<redacted>/g")"
   echo "$BOOTSTRAP_SQL" | docker compose exec -T clickhouse clickhouse-client --multiquery
   echo "user bootstrap ok"
   ```

   The trailing ALTER is the **rotation-gap fix** (Phase 2 log §2.7 gap:
   initdb.d only ran on empty volumes, so a rotated password was never
   applied). It runs EVERY boot with the current secret version (AC7/AC8
   evidence).
3. Migrations: `CH_HOST=localhost CH_PORT=8123 CH_USER=wikistream
   CH_PASSWORD=${CH_PASSWORD} MIGRATIONS_DIR=/opt/wikistream/migrations bash
   /opt/wikistream/migrations/apply.sh` (per-file APPLY/SKIP lines; non-zero
   exit aborts boot.sh).
4. (3C, task 3.3.5): install systemd units + enable timers — HERE in boot.sh,
   so 3C makes NO startup.sh edit and no recreate.

**Verify:** fresh-boot VM reaches `startup done` with the echoed SQL +
`user bootstrap ok` + APPLY/SKIP lines in the log; two `count()` samples
strictly increasing; `ALTER USER` statement text present in the log
(rotation-fix evidence, AC7).

### 3.1.5 — Retire initdb.d

- Delete `docker/clickhouse/initdb.d/001-init.sql` (and the dir).
- `docker-compose.yml`: remove the `./docker/clickhouse/initdb.d:...` volume
  mount (line ~12). Everything else unchanged.
- No other references remain (grep `initdb.d` = 0 hits outside docs).

**Verify:** `rg -n "initdb" --glob '!docs/**'` → no hits; compose config valid.

### 3.1.6 — CI (Q2 + the CI user gap)

**`migrations/bootstrap-user.dev.sql`** created in 3.1.2 is the CI path.

**`.github/workflows/ci.yml`:**

- `pytest.ini`: add `markers = ch: requires ClickHouse (runs against localhost:8123)`.
- Matrix `unit-tests` entry: both pytest invocations gain `-m "not ch"`.
- New matrix entry `analytics-tests` (uses the existing shared install-deps
  step — it's a non-compose-smoke entry, so `uv sync --project consumer
  --frozen` runs and clickhouse-connect is available): checkout → install deps
  → `docker compose up -d clickhouse` → wait ready → `docker compose exec -T
  clickhouse clickhouse-client --multiquery < migrations/bootstrap-user.dev.sql`
   → `CH_HOST=localhost CH_USER=wikistream CH_PASSWORD=wikistream_dev_password
   uv run --project consumer pytest -q --tb=short -m ch` — testpaths=tests
   collects whatever ch-marked files exist; the 3B/3C suites join automatically
   when their PRs land (NO explicit paths: a missing dir would exit 4 and make
   the 3A PR's own CI red).
- `compose-smoke` entry: after `docker compose up -d --build` (and before the
  Phase 1 AC2/AC3 polls): wait CH ready first — `for i in $(seq 1 30); do
  docker compose exec -T clickhouse clickhouse-client --query "SELECT 1" &&
  break; sleep 2; done` (initdb.d is retired, so CH needs ~10–30s before it
  accepts connections — without the wait the first 3A CI run is flaky), then
  bootstrap user (same `--multiquery` line), then
  `CH_HOST=localhost CH_USER=wikistream CH_PASSWORD=wikistream_dev_password
  ./migrations/apply.sh` (its own readiness wait makes the smoke stable
  regardless). The consumer may log `clickhouse_unavailable` /
  `insert_failed` WARNINGs in the gap — the existing Phase 1 AC2 (0 Tracebacks)
  still passes (WARNINGs are the contract, not failures).
- 3B PR additionally updates the smoke AC5 check string: `/api/search` contains
  "WikiStream Live Analytics" (was "Phase 1").

**Verify:** PR shows `analytics-tests` green (fresh runner, clean volume =
clean-DB apply proof, AC1); `compose-smoke` green.

### 3.1.7 — tests/migrations (`tests/migrations/test_migrations.py`, `@pytest.mark.ch`)

All tests connect to `localhost:8123` as `wikistream`/dev password (env-driven
via the same vars as apply.sh). Uses a dedicated test database? NO — the runner
targets `default` (matches prod); tests run against the ephemeral CI container
only; locally they need a disposable container (`docker compose up -d
clickhouse` + wipe volume). Fixtures:

- `test_clean_db_apply`: run runner on a fresh container → exit 0; ALL
  `[0-9]*.sql` files recorded — count == number of files — each with status
  `applied` or `skipped` (guard-0 files on a clean DB are `skipped`, not
  absent); `raw_events` exists with the 8 typed columns (assert via
  `system.columns`).
- `test_re_run_idempotent`: run runner twice → second run exits 0, records
  nothing new, `schema_migrations` row count unchanged.
- `test_ttl_present`: `SELECT ttl_expression FROM system.tables WHERE name =
  'raw_events'` contains `toIntervalDay(30)` (canonical form; fallback assert
  on `SHOW CREATE TABLE`).
- `test_legacy_migration`: create the old-shape table + insert one realistic
  event row (full Wikimedia JSON in `event`), run runner → `raw_events_v1`
  renamed, backfilled, typed columns populated correctly (wiki/title/user/
  is_bot/length values match the JSON), `raw_events_v1` dropped (or still
  present if 003 held — assert per current repo state).
- `test_materialized_compute`: insert an event with bot=true + lengths; assert
  the MATERIALIZED columns carry the right values immediately (computed at
  insert, not by a background job).

### 3.1.8 — 3A PR → capture → deploy → import → micro-gate → log

1. **Confirm the existing Gate 1 record** (Q9): implementation-log §2.8
   already records "GO (with caveats)" — cross-reference it in the Phase 3
   opening; do NOT re-record.
2. **Pre-merge TSV capture** (on the VM, before the PR merges and the auto-apply
   recreates the instance; recorded in the log):
   - `sudo docker exec wikistream-clickhouse clickhouse-client --query
     "SELECT count() FROM default.raw_events"` → record the pre-count (sudo
     needed: the OS Login user isn't in the docker group — log §2.5).
   - `sudo docker exec wikistream-clickhouse clickhouse-client --query
     "SELECT inserted_at, event FROM default.raw_events ORDER BY inserted_at
     FORMAT TSV" > /tmp/legacy-raw-events.tsv` → `gzip
     /tmp/legacy-raw-events.tsv` →
     `gcloud compute scp wikistream-vm:/tmp/legacy-raw-events.tsv.gz
     --zone us-east1-b ./` (local).
3. PR `feature/phase-3a-schema` → merge → gated apply → VM reset (recreate —
   expected: startup.sh changed; ch-data disk attaches fresh) → startup log
   evidence (mount block, echoed bootstrap SQL, `user bootstrap ok`, migration
   APPLY/SKIP lines, `startup done`).
4. **Post-deploy TSV import**: `gcloud compute scp ./legacy-raw-events.tsv.gz
   wikistream-vm:/tmp/ --zone us-east1-b` → on VM: `zcat
   /tmp/legacy-raw-events.tsv.gz | sudo docker exec -i wikistream-clickhouse
   clickhouse-client --query "INSERT INTO default.raw_events (inserted_at,
   event) FORMAT TSV"` (note `-i` — stdin must reach the container). Verify
   `SELECT count() FROM default.raw_events` == captured pre-count (AC5).
5. **Micro-gate (HARD CHECKPOINT before 3B):**
   - Re-run `apply.sh` on the VM by hand → all SKIP, exit 0 (re-runnable, AC2).
   - `SELECT ttl_expression FROM system.tables WHERE name='raw_events'` →
     `toIntervalDay(30)` present (AC3).
   - Imported count == pre-count + typed-column spot-check
     (`WHERE wiki != ''` > 0) (AC5).
   - `ALTER USER` evidence in the startup log (AC7) + CI `analytics-tests`
     green (clean-DB apply, AC1).
   - Record numbers in the log; only then does 3B PR work begin.
6. Log: per-§4-checklist-item outcomes ("confirmed standard" vs "fallback
   used: X"), deviations, rotation-fix evidence, micro-gate evidence.

---

### 3B — Analytics (PR 2)

### 3.2.1 — MV migrations 004–006 (Q4)

All three: `CREATE MATERIALIZED VIEW IF NOT EXISTS default.mv_<x> ... ENGINE
= SummingMergeTree ... AS SELECT ... FROM default.raw_events WHERE ... GROUP
BY ...` — **NO POPULATE** (history starts at 3B deploy; fills within minutes).

**`004_mv_edits_per_minute.sql`:**

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS default.mv_edits_per_minute
ENGINE = SummingMergeTree ORDER BY (minute, wiki, is_bot)
AS SELECT
    toStartOfMinute(inserted_at) AS minute,
    wiki,
    is_bot,
    count() AS edits,
    sum(toInt64(length_new) - toInt64(length_old)) AS bytes_delta
FROM default.raw_events
WHERE event_type IN ('edit', 'new') AND wiki != ''
GROUP BY minute, wiki, is_bot
```

Velocity, bot/human ratio, and project/language breakdown all derive from this
one table at query time (rolling windows via `WHERE minute >= now() - INTERVAL
${window}`).

**`005_mv_top_pages_per_minute.sql`:** `minute, title, wiki, count() AS edits,
sum(toInt64(length_new) - toInt64(length_old)) AS bytes_delta` — WHERE
`event_type IN ('edit', 'new') AND wiki != ''`; ORDER BY
`(minute, title, wiki)` (title alone collides across wikis).

**`006_mv_edit_sizes_per_minute.sql`:** `minute, bucket, count() AS edits` —
bucket via `multiIf` on `abs(toInt64(length_new) - toInt64(length_old))`:
`= 0 → '0'`, `<= 10 → '1-10'`, `<= 100 → '11-100'`, `<= 1000 → '101-1000'`,
`<= 10000 → '1001-10000'`, else `'10000+'`; WHERE `event_type IN ('edit',
'new') AND wiki != ''`; ORDER BY `(minute, bucket)`.

**Verify:** `SHOW TABLES LIKE 'mv_%'` → 3; CI suite (3.2.2).

### 3.2.2 — tests/mv (`tests/mv/test_mv_equivalence.py`, `@pytest.mark.ch`)

The ADR-006 spot-check guarantee as assertions: **MV output == equivalent raw
GROUP BY for the same window**, for synthetic data covering the edge matrix:
edit/new/log event types, bots + humans, missing `length_old` (new events),
empty `wiki` (log rows — excluded by the MV filter), sizes spanning all
buckets. Because SummingMergeTree may return unmerged duplicates, BOTH sides
are compared as aggregated sums over the window. Also asserted: the
`warehouse/sql/export_*.sql` files (3C, empty-file-safe until then) produce the
same numbers as the MV tables they read — the export SQL is the SAME rollup
logic exercised in CI (Q7's one-source-of-truth).

### 3.2.3 — Dashboard (Q10)

**`grafana/dashboards/wikistream-live.json`** (uid `wikistream-live`, title
"WikiStream Live Analytics", tags `[wikistream]`, timezone utc, refresh 10s,
time `now-1h → now`, datasource uid `wikistream-clickhouse`; every target's
`"format"` is the numeric enum — `0` for timeseries, `1` for table-consuming
panels — verified at build). Dashboard variable `$window` (custom values
`1h`/`24h`, default `1h`) feeds the rolling panels. Panels:

1. **Edit velocity** (timeseries, format 0): `SELECT toStartOfMinute(minute)
   AS t, if(is_bot = 1, 'bot', 'human') AS series, sum(edits) AS edits FROM
   default.mv_edits_per_minute WHERE minute >= now() - INTERVAL 1 HOUR GROUP
   BY t, series ORDER BY t` — stacked human vs bot (deliberate: fixed 1h
   velocity view; panels 2–5 use the ${window} variable).
2. **Bot vs human ratio** (pie, format 1): `SELECT if(is_bot = 1, 'bot',
   'human') AS series, sum(edits) AS edits FROM default.mv_edits_per_minute
   WHERE minute >= now() - INTERVAL ${window} GROUP BY series`.
3. **Top pages** (bar gauge, format 1): `SELECT title, wiki, sum(edits) AS
   edits FROM default.mv_top_pages_per_minute WHERE minute >= now() - INTERVAL
   ${window} GROUP BY title, wiki ORDER BY edits DESC LIMIT 10`.
4. **Project/language breakdown** (bar, format 1): `SELECT wiki, sum(edits) AS
   edits FROM default.mv_edits_per_minute WHERE minute >= now() - INTERVAL
   ${window} GROUP BY wiki ORDER BY edits DESC LIMIT 15`.
5. **Edit-size histogram** (bar, format 1): `SELECT bucket, sum(edits) AS
   edits FROM default.mv_edit_sizes_per_minute WHERE minute >= now() -
   INTERVAL ${window} GROUP BY bucket ORDER BY multiIf(bucket = '0', 0,
   bucket = '1-10', 1, bucket = '11-100', 2, bucket = '101-1000', 3, bucket =
   '1001-10000', 4, 5)` — numeric bucket order (plain ORDER BY bucket is
   lexicographic and would misplace '10000+' before '1001-10000').

(3C adds panel 6 — warehouse freshness, BQ datasource, task 3.3.6.)

- DELETE `grafana/dashboards/phase1.json`.
- `grafana/provisioning/dashboards/dashboards.yaml`: provider name
  `phase1` → `wikistream` (cosmetic; path/interval unchanged).
- `ci.yml` compose-smoke AC5 string → "WikiStream Live Analytics".

**Verify:** `curl -u admin:<SM grafana-admin-password value>
http://<ip>:3000/api/search` contains "WikiStream Live Analytics" and NOT
"Phase 1" (the VM authenticates with the Secret Manager value per Phase 2
§2.5 — `admin:admin` exists only in local compose/ci.yml); each panel's SQL
via `/api/ds/query` (uid wikistream-clickhouse) returns rows (recorded in log).

### 3.2.4 — 3B PR → deploy → live spot-check → log

1. PR `feature/phase-3b-analytics` → merge → gated apply → VM reset.
2. **Live spot-check (checkpoint before 3C)** — the real-data equivalence,
   run 15–60 min after deploy (MV must have ≥1 window of data):
   - `SELECT sum(edits) FROM default.mv_edits_per_minute WHERE minute >= now()
      - INTERVAL 15 MINUTE` vs the raw twin (`... FROM default.raw_events WHERE
     event_type IN ('edit','new') AND wiki != '' AND inserted_at >= now() -
     INTERVAL 15 MINUTE`) — equal (sums are exact regardless of merge state;
     the raw twin carries the MV's canonical filters, 3.2.1). Same for
     top_pages (title-level) and sizes (bucket-level) — their raw twins carry
     the same canonical filters — or one representative totals asserted for
     all three.
   - Panels render non-null: `/api/ds/query` per panel SQL returns rows.
   - Record numbers in the log; only then does 3C work begin.
3. Log: coverage-boundary correction (migrations/ + tests/migrations now real
   paths; MV definitions row moves to DONE-style note), dashboard evidence,
   spot-check evidence.

---

### 3C — Warehouse (PR 3)

### 3.3.1 — Bootstrap API (Q8)

**`infra/bootstrap/main.tf`**: add `"bigquery.googleapis.com"` to the
`google_project_service` for_each list. Apply manually (local state, same
ceremony as log 0.2): `terraform -chdir=infra/bootstrap apply`. Record in log
(CI never touches bootstrap). **Verify:** `gcloud services list --enabled
--filter=bigquery` shows `bigquery.googleapis.com`.

### 3.3.2 — `modules/bigquery` (ADR-007 extension)

**`infra/main/modules/bigquery/bigquery.tf`:**

- `google_bigquery_dataset wikistream` (dataset_id `wikistream`, location `US`,
  labels).
- 5 tables (`google_bigquery_table`, `schema = file("${path.module}/../../../
  ../warehouse/schemas/<name>.json")` — spelled-out relative path to
  warehouse/schemas/, the SINGLE source (Q7; no copies to drift),
  `time_partitioning { type = "DAY", field = <ts> }`, labels):
  - `kpi_edits_hourly` (hour TIMESTAMP, wiki STRING, is_bot BOOL, edits INT64,
    bytes_delta INT64; partition hour; cluster wiki).
  - `kpi_top_pages_hourly` (hour TIMESTAMP, title STRING, wiki STRING, edits
    INT64, bytes_delta INT64; partition hour).
  - `kpi_edit_sizes_hourly` (hour TIMESTAMP, bucket STRING, edits INT64;
    partition hour).
  - `raw_events_sample` (inserted_at TIMESTAMP, event STRING, wiki STRING,
    title STRING, user STRING, is_bot BOOL, event_type STRING; partition
    inserted_at).
  - `export_runs` (exported_at TIMESTAMP, window_start TIMESTAMP, window_end
    TIMESTAMP, status STRING, rows_edits INT64, rows_top_pages INT64,
    rows_sizes INT64, rows_raw_sample INT64; partition exported_at).
- `google_bigquery_dataset_iam_member`: `roles/bigquery.dataEditor`, member =
  VM SA email (dataset-scoped ONLY — Q8; covers bq queries AND the Grafana
  datasource's jobs).
- `google_storage_bucket wikistream-505003-bq-staging` (location US,
  uniform_bucket_level_access, lifecycle_rule {action {type = "Delete"},
  condition {age = 7}}) + bucket IAM: `storage.objectCreator` +
  `storage.objectViewer` for the VM SA (Q8 — dataEditor does NOT grant GCS).
- Outputs: dataset_id, bucket name.

**`infra/main/main.tf`:** wire `module "bigquery"` (project_id, region,
service_account_email = module.iam.service_account_email, labels); bump
`locals.labels.phase` → `"3"` (in-place label update).

**Verify:** after apply: `bq show wikistream`, `bq ls wikistream` → 5 tables;
`gsutil ls gs://wikistream-505003-bq-staging`; IAM visible via `bq show
--format=prettyjson wikistream` (dataEditor member = VM SA).

### 3.3.3 — warehouse/sql + warehouse/schemas (Q6/Q7)

`warehouse/sql/export_*.sql` — `{START}`/`{END}` placeholders (UTC timestamps
substituted by export.sh/parity.sh/tests — one source of truth, Q7):

- `export_edits.sql`: `SELECT toStartOfHour(minute) AS hour, wiki, is_bot,
  sum(edits) AS edits, sum(bytes_delta) AS bytes_delta FROM
  default.mv_edits_per_minute WHERE minute >= '{START}' AND minute < '{END}'
  AND wiki != '' GROUP BY hour, wiki, is_bot ORDER BY hour`.
- `export_top_pages.sql` / `export_sizes.sql`: same shape over their MV tables.
- `export_raw_sample.sql`: `SELECT inserted_at, event, wiki, title, user,
  is_bot, event_type FROM default.raw_events WHERE inserted_at >= '{START}'
  AND inserted_at < '{END}' AND sipHash64(event) % 100 < 10` (Q12: 10%,
  deterministic — same events sample every re-run of a window).

`warehouse/schemas/*.json` — explicit BQ schemas mirroring the table definitions
above; the SINGLE source: referenced by `bq load --schema=...` AND read
directly by the TF module's `schema` attribute via a spelled-out relative path
(3.3.2) — no copies to drift.

**Verify:** tests/warehouse (3.3.7) asserts the SQL shapes' output.

### 3.3.4 — export.sh + parity.sh (Q7)

**`warehouse/export.sh`** — `set -euo pipefail`; sources `/opt/wikistream/.env`
for `CLICKHOUSE_PASSWORD` (no secrets in units); optional `START`/`END` args
(manual catch-up); default window = last completed UTC hour
(`END = start of current hour`, `START = END - 1h`). Per table (kpi_edits →
kpi_top_pages → kpi_sizes → raw_sample):

1. `docker exec -i wikistream-clickhouse clickhouse-client --user wikistream
   --password "$CLICKHOUSE_PASSWORD" --format JSONEachRow <
   <(sed "s/{START}/$START/; s/{END}/$END/" warehouse/sql/export_<x>.sql)` (-i
   required — piped stdin, §4 rule) →
   `/tmp/wikistream-export/<table>/<YYYYMMDDHH>.jsonl`; `wc -l` captures rows.
2. `gcloud storage cp <jsonl> gs://wikistream-505003-bq-staging/<table>/<YYYYMMDDHH>.jsonl`.
3. `bq load --source_format=NEWLINE_DELIMITED_JSON
   --time_partitioning_field=<ts> --schema=warehouse/schemas/<table>.json
   wikistream.<table> <jsonl>` (append semantics; see §9 for the duplicate-row
   remediation if a window is ever re-exported).
4. Build the `export_runs` row (exported_at=now, window_start/end, status
   "success", the four row counts) → `bq load` a single generated JSONL line
   against the committed `export_runs.json` schema.
Any failure → non-zero exit (timer shows failed; parity detects staleness).

**`warehouse/parity.sh`** — run at :05, validates the window the :00 export
just produced. Sources .env; for the SAME `[START, END)` window:

- Freshness: latest `export_runs` row's window_end == `END` AND status ==
  "success" — else exit non-zero.
- Counts: for each of the 4 tables, BQ count for the window (`bq query
  --use_legacy_sql=false --format=json` with the same `{START}`/`{END}`
substituted SQL from warehouse/sql) vs CH count (docker exec -i — piped
stdin, §4 rule — the identical SQL) — any mismatch → exit non-zero. Writes a
one-line JSON result to
  `/var/log/wikistream-parity.log` (the Phase 5 alert hook).
- CH-source counts use the SAME rollup SQL as the export (sums over the
  window) so merge-state can't cause false drift.

**Verify:** local dry-run against synthetic data via 3.3.7; on-VM: two
consecutive green :05 runs (systemctl + journal + log lines, AC17).

### 3.3.5 — systemd units + timers + boot.sh install step

- `warehouse/wikistream-export.{service,timer}` — timer `OnCalendar=*-*-*
  *:00:00`, `Persistent=true`; service `ExecStart=/opt/wikistream/warehouse/
  export.sh` (+ `ExecStartPre` touch to the log dir).
- `warehouse/wikistream-parity.{service,timer}` — timer `OnCalendar=*-*-*
  *:05:00`, `Persistent=true` (5 min after the export so the window is loaded).
- boot.sh (3C step, task 3.1.4's seam — NO startup.sh edit, no recreate):
  `cp /opt/wikistream/warehouse/wikistream-export.service
  /opt/wikistream/warehouse/wikistream-export.timer
  /opt/wikistream/warehouse/wikistream-parity.service
  /opt/wikistream/warehouse/wikistream-parity.timer /etc/systemd/system/ &&
  systemctl daemon-reload && systemctl enable --now wikistream-export.timer
  wikistream-parity.timer` (absolute paths — boot.sh's CWD isn't guaranteed;
  idempotent). Scripts must be executable in git
  (`chmod +x`, git records the bit).
- Timers run on the host as root; CH access via docker exec, GCP via the VM
  SA's metadata token (cloud-platform scopes cover bq + gcloud storage).

**Verify:** `systemctl is-active wikistream-export.timer wikistream-parity.timer`
→ active; `systemctl list-timers | grep wikistream` shows next fire times.

### 3.3.6 — BigQuery datasource + freshness panel (ADR-010)

- `docker-compose.yml`: `GF_PLUGINS_PREINSTALL` →
  `grafana-clickhouse-datasource@4.20.0,grafana-bigquery-datasource@3.2.0`.
- `grafana/provisioning/datasources/bigquery.yaml`: name BigQuery, uid
  `wikistream-bigquery`, type `grafana-bigquery-datasource` (the plugin id —
  `google-bigquery-datasource` 404s), jsonData `{authenticationType: gce,
  defaultProject: wikistream-505003}` (the 3.x plugin uses defaultProject;
  dataset is optional — the panel SQL fully qualifies
  `wikistream.export_runs`). isDefault stays false (ClickHouse remains
  default). GCE auth =
  plugin reads the VM SA token from the metadata server; the dataset-scoped
  dataEditor binding covers its jobs (no new IAM — Q8).
- Panel 6 on `wikistream-live.json` (stat, datasource uid wikistream-bigquery,
  format 1): `SELECT TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(exported_at),
  MINUTE) AS minutes_since FROM wikistream.export_runs` — thresholds
  green < 60, orange 60–120, red > 120; unit "min". Title "Warehouse freshness".

**Verify:** `/api/datasources` lists BigQuery; `/api/ds/query` over
wikistream-bigquery returns a non-null minutes_since (AC19).

### 3.3.7 — tests/warehouse (`tests/warehouse/test_export_parity.py`, `@pytest.mark.ch`)

Runs against local CH only (gcloud/bq mocked or skipped — local equivalence is
the point): build synthetic data in the 3 MV tables + raw_events; substitute a
synthetic window into the committed `warehouse/sql/export_*.sql`; assert:

- export SQL output == expected hourly rollup (known fixture numbers);
- raw sample SQL returns only the deterministic `sipHash64 % 100 < 10` subset;
- parity count queries return identical numbers for CH side vs export output
  (the drift the real parity.sh detects = BQ side mismatch);
- `export_runs` shape (status, counts) matches the fixture.
Also asserts the SQL files parse and reference existing columns (catches
schema drift between CH and the committed SQL).

### 3.3.8 — 3C PR → deploy → verification battery → log

1. PR `feature/phase-3c-warehouse` → merge → gated apply → VM reset. (Manual
   bootstrap apply happened in 3.3.1 BEFORE this merge — the apply's plan will
   otherwise fail on the missing API.)
2. Verification battery (the 3C gate):
   - BQ dataset rows grow in lockstep with CH: `bq query` counts for
     `kpi_edits_hourly` today vs yesterday.
   - Sample-window spot-check: BQ vs CH for one completed hour — equal (manual
     bq query vs docker exec; recorded in log).
   - Parity timer green for ≥2 consecutive hours (systemctl + journal +
     parity log lines).
   - Freshness panel renders a live value (< 120 min).
   - Staging bucket lifecycle rule present in TF config + no objects older
     than 7 days.
3. Log: coverage-boundary row for warehouse/ SQL + tests/warehouse; bootstrap
   API record; deviation notes; cost line (BQ ~fractions of a cent/day, staging
   GCS pennies — Phase 7b formalizes).

---

## 7. Acceptance criteria (self-checkable)

| # | Criterion | How an agent verifies it |
| --- | --- | --- |
| AC1 | Migrations apply cleanly to a clean DB | CI `analytics-tests` green (fresh runner, empty volume); locally: wipe ch-data, `docker compose up -d clickhouse`, bootstrap, `./migrations/apply.sh` → exit 0; `SELECT count() FROM schema_migrations` = number of migration files |
| AC2 | Migrations are re-runnable | `./migrations/apply.sh` a second time → exit 0, zero new version rows, log lines all SKIP |
| AC3 | TTL clause present on raw_events | `SELECT ttl_expression FROM system.tables WHERE name='raw_events' AND database='default'` contains `toIntervalDay(30)` |
| AC4 | Full typed schema in place | `SELECT name, type FROM system.columns WHERE table='raw_events'` includes inserted_at, event, wiki, title, user, event_type, is_bot, length_new, length_old, event_timestamp |
| AC5 | Legacy data migrated losslessly | On VM: `SELECT count() FROM raw_events` == pre-3A count captured pre-merge (TSV capture/import, task 3.1.8); `SELECT count() FROM raw_events WHERE wiki != ''` > 0 (typed columns populated); CI `test_legacy_migration` green (the legacy exercise lives in CI/local — the VM recreates fresh) |
| AC6 | initdb.d fully retired | `rg -n "initdb" --glob '!docs/**' .` → no hits; `docker compose config` valid; startup.sh contains no initdb heredoc/fence |
| AC7 | Rotation-gap fix live | Startup log contains the `ALTER USER IF EXISTS wikistream ...` execution evidence (echoed by boot.sh, task 3.1.4); grep on the log file |
| AC8 | User bootstrap works from startup script | Startup log has `user bootstrap ok`; consumer reaches `connected url=` with the REAL secret (no `insert_failed` after migrations complete); two `count()` samples strictly increasing |
| AC9 | MVs exist | `SHOW TABLES LIKE 'mv_%'` on the VM → mv_edits_per_minute, mv_top_pages_per_minute, mv_edit_sizes_per_minute |
| AC10 | MV equivalence (synthetic) | CI `tests/mv` green (MV output == raw GROUP BY over the edge-case matrix) |
| AC11 | MV equivalence (live, real data) | Recorded in log: 15-min window MV-vs-raw sums equal for all 3 MVs (task 3.2.4 evidence) |
| AC12 | Dashboard provisioned + phase1 gone | `/api/search` (basic auth) contains "WikiStream Live Analytics" and not "Phase 1"; dashboard uid `wikistream-live` present |
| AC13 | All 5 panels query non-null | `/api/ds/query` per panel SQL over uid `wikistream-clickhouse` returns rows with values (recorded in log) |
| AC14 | bigquery API enabled | `gcloud services list --enabled --filter=config.name:bigquery.googleapis.com` shows it; bootstrap apply recorded in log |
| AC15 | BQ dataset + 5 tables + staging bucket exist | `bq ls wikistream` → 5 tables; `bq show wikistream.kpi_edits_hourly` shows DAY partitioning on hour; `gsutil ls gs://wikistream-505003-bq-staging` reachable; dataset IAM has VM SA as dataEditor (`bq show --format=prettyjson wikistream`) |
| AC16 | Export timer produces data | `systemctl is-active wikistream-export.timer` → active; after ≥1 fire: `bq query "SELECT count(*) FROM wikistream.export_runs"` > 0 with status success; each KPI table has rows for the last completed hour |
| AC17 | Parity check green on schedule | `systemctl is-active wikistream-parity.timer` → active; `systemctl show wikistream-parity.service --property=ExecMainStatus` = 0 for ≥2 consecutive runs (or journalctl exit-code lines); `/var/log/wikistream-parity.log` has matching window rows |
| AC18 | BQ matches CH for a real window | Manual spot-check recorded: one completed hour — BQ count == CH count per table (identical SQL, task 3.3.8 evidence) |
| AC19 | Freshness panel renders | `/api/ds/query` over uid `wikistream-bigquery` returns non-null minutes_since (< 120); panel visible on the dashboard |
| AC20 | Staging lifecycle enforced | TF config shows the 7-day Delete rule; `gcloud storage ls` shows no objects older than 7 days |
| AC21 | Coverage boundary + log consistent | `docs/planning/coverage-boundary.md` corrected (migrations/ + warehouse/ SQL + 3 test suites as the 100% story; bash wrappers noted as thin, exit-code-verified); implementation-log Phase 3 populated with CI URLs, startup-log lines, micro-gate + spot-check + parity evidence, Gate 1 record, deviations |

## 8. Verification gates (master plan wording)

> Phase 3 objective: replace the skeleton's single table and single panel with
> the full data model (ADR-006), complete KPI dashboard, and warehouse tier.

Phase exit criteria = AC1–AC21. Three checkpoints, in order:

1. **3A micro-gate (HARD CHECKPOINT before 3B):** migrations apply cleanly to
   a clean database (AC1, CI-proven) AND are re-runnable (AC2) AND the TTL
   clause is confirmed present (AC3) — plus the live legacy-migration evidence
   (AC5) and rotation-fix evidence (AC7). No 3B work starts until the log
   records these.
2. **3B spot-check (checkpoint before 3C):** each MV's output spot-checked
   against an equivalent raw-table query for the same window on REAL data
   (AC11) — a silently-wrong MV is ADR-006's named risk; this catches it before
   the accumulation window trusts it. All KPI panels render correctly against
   real data (AC12/AC13).
3. **3C verification battery (phase-final):** BQ dataset rows grow in lockstep
   with the source (AC16); a sample window in BQ matches the equivalent CH MV
   query (AC18); parity timer runs green at :05 for ≥2 consecutive hours
   (AC17); freshness panel renders in Grafana (AC19); export path re-runnable
   from clean (AC1's CI job covers the SQL side + AC16's re-fire).

Then **Go/No-Go for Phase 4 (master plan §4) — an explicit final step, not a
separate doc:** all of AC1–AC21 pass with recorded evidence; Gate 1's GO-with-
caveat is on record (task 3.1.8); the warehouse is a live, visible system
(freshness panel + parity green) — not a dead-end dump. No-Go = any AC fails
without a recorded, understood fix.

## 9. Troubleshooting notes

- **Consumer logs `insert_failed`/`clickhouse_unavailable` right after a 3A
  deploy:** expected — the migration window sits between compose start and
  user/table readiness; the consumer's retry loop (Phase 1 contract) recovers;
  verify `count()` resumes increasing. Startup log should show `user bootstrap
  ok` + APPLY lines before `startup done`.
- **`apply.sh` fails with HTTP 4xx:** read the response body (CH returns the
  SQL error); common: user not yet created (boot.sh runs bootstrap BEFORE
  migrations — if it still fails, check the `user bootstrap` echo line in the
  startup log), password mismatch (.env stale vs secret version — rotation
  fix re-syncs on next boot), a migration file's SQL error (fix the file; the
  failed version is NOT recorded, so re-running retries it — bodies are
  `IF NOT EXISTS`-safe).
- **`raw_events_v1` backfill looks empty:** on CI/local the 000 guard evaluated
  0 (table already renamed) — check `system.tables` for `raw_events` +
  `raw_events_v1` names; `SELECT count() FROM raw_events_v1` before 003 drops
  it. On the VM (recreated fresh) there is no v1: if `raw_events` count !=
  captured pre-count, the 3.1.8 TSV import is the suspect — re-run it (note
  the `-i` flag: stdin must reach the container).
- **MV panels empty after 3B deploy:** NO POPULATE by design — wait ~1–2 min at
  ≈44 ev/s; if still empty, check the consumer is inserting (`count()` rising)
  and the dashboard time range covers post-deploy data.
- **MV spot-check mismatch:** ensure BOTH sides are windowed sums (merge state
  can leave duplicate per-minute rows); a mismatch on equal SQL = data
  dropped at insert — check `insert_failed` counts in consumer logs.
- **`parseDateTime64BestEffort` mis-parses timestamps (build check):** use the
  §4 fallback (`replaceRegexpOne(..., 'Z$', '')` + `toDateTime64(..., 3,
  'UTC')`); re-run migrations (column definition change = edit 001 + re-apply).
- **bq load rejects timestamps (build check):** JSONEachRow renders
  `2026-08-11 12:00:00.000`; if `bq load` rejects, cast in the export SQL via
  `formatDateTime(..., '%Y-%m-%dT%H:%i:%sZ')` (schema stays TIMESTAMP).
- **Duplicate rows in BQ after a manual re-export of an already-exported
  window:** bq load appends. Remediate per table: `bq query --use_legacy_sql=
  false "DELETE FROM wikistream.kpi_edits_hourly WHERE hour >= TIMESTAMP('...')
  AND hour < TIMESTAMP('...')"` then re-export. The parity check surfaces this
  automatically (count mismatch → non-zero).
- **Parity exits non-zero:** check the parity log line; causes: export timer
  failed that hour (export_runs missing/stale — rerun export.sh with explicit
  START/END for the missed window), CH briefly down (retry), BQ load failure
  (bq log). Persistent failure → the Phase 5 alert hook exists precisely for
  this.
- **BigQuery datasource shows errors on LOCAL dev compose:** GCE auth needs the
  VM metadata server; locally the plugin can't auth (no service account).
  Expected — only the freshness panel is affected; CH panels work. On the VM it
  works (AC19).
- **Grafana restarts with plugin errors:** GF_PLUGINS_PREINSTALL is a
  comma-separated single string (`...,grafana-bigquery-datasource@3.2.0` — no
  spaces); check `docker compose logs grafana` for plugin download lines.
- **`bq` not found on the VM:** bq ships with google-cloud-cli (startup.sh §0
  installs it); after a manual install drift, re-run the §0 install block.
- **TTL not visibly deleting:** TTL removes rows in background merges; verify
  `system.parts` / the TTL expression (AC3) — deletes lag, by design.
- **Cost sanity (Phase 3 delta):** BQ storage + query ~fractions of a cent/day
  at this volume; staging GCS pennies; systemd free. Phase 2's ~$25.5/mo
  baseline unchanged (labels on new resources keep billing attribution).
- **Timer missed a fire (VM down):** `Persistent=true` triggers on next boot —
  boot.sh installs/enables units after CH is up, so a same-boot catch-up runs
  once CH is back.

## 10. Handoff to Phase 4 (what Phase 4 inherits)

- **A versioned schema with bookkeeping:** `migrations/apply.sh` + numbered
  DDL + `schema_migrations` — new schema changes are one migration file + one
  boot away; CI proves clean-DB apply every run.
- **Typed raw_events columns**, incl. `event_timestamp`: the GX suite's
  "required fields present / bot flag boolean / timestamp not stale-future"
  checks (research notes) now have typed columns to assert against — the
  Phase 4 GX work is assertions, not schema plumbing. `sipHash64(event)` order
  key makes Phase 4 dedup cheap.
- **Live KPI aggregates:** the 3 MV tables are the backup targets and the
  dashboard's source; Phase 4's native BACKUP-to-GCS cadence (ADR-006) protects
  them; restore+spot-check exercises the MV spot-check discipline again.
- **Warehouse tier live:** hourly BQ rollups + 10% raw sample + export_runs;
  the parity check's exit code and log are Phase 5's alert source; the
  freshness panel is the Phase 5 alert's visual twin.
- **Rotation-gap fix closed:** user password re-syncs every boot — Phase 4's
  secret rotation story (re-apply → reset) now works end to end.
- **CI shape:** `-m "not ch"` vs `@pytest.mark.ch` split; new Python packages
  (gx, pydantic) plug into the existing extension seams (uv sync lines,
  BUSINESS_CRITICAL_MODULES list).
- **Coverage boundary:** migrations/ + warehouse/ SQL + the 3 test suites are
  the Phase 3 entries in the business-critical 100% story; Phase 4 adds
  models.py/batcher.py/dead_letter.py/gx suite per the boundary doc.
- **Still open (Phase 4 decisions):** Pydantic validation + dead-letter +
  restart-resume in the consumer, dedup, ingestion scope filter (Wikipedia-only
  domains), native BACKUP schedule, GX suite + enforcement.
