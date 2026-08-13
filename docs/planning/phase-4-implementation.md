# Phase 4 Implementation Plan — Consumer Resilience, Data Quality (GX), and Backup/Restore

**Status:** LOCKED 2026-08-12. Decisions ratified in a grilling session with
Ahmed (questions Q1–Q12 below). This document is the input for an agentic
coding tool — every acceptance criterion is self-checkable (pass/fail an agent
can verify), per master plan §10.

**Reviewed 2026-08-12 by a single code-review subagent (logical + syntactic
soundness): 18 findings (1 blocker, 7 medium, 10 nits), all fixed.**

**Position in the hierarchy:** Master Plan §5 Phase 4 → this document. Nothing
here re-decides the ADR or master plan; it makes Phase 4 executable. Phase 4
splits into two subphases (4A consumer resilience — PR 1; 4B GX data quality +
healthchecks + backup/restore — PR 2) — **planned here in one pass, implemented
in two PRs**, each merge → gated apply → VM reset → verify its micro-gate
before the next PR begins. 4A is the only PR whose changes touch consumer
pipeline logic (4B adds the healthcheck module to the same image — a 2-line
src addition, not a pipeline change). Phase
4 is independent of Phase 5 (may interleave, master plan §5) and is the phase
where the **Go/No-Go Gate 2** is recorded (master plan §4: "does resilience
actually hold, not just exist in config?").

**Branch note (user convention):** all Phase 3 work rode `feature/Data-Model-
Depth` — Phase 4 does the same regardless of the per-PR branch labels used
below, logged once in implementation-log.

---

## 1. Objective

Wire in and **prove** — not just configure — everything ADR-005 (validation),
ADR-004's resilience details (restart-resume, batching, dead-letter), and
ADR-006's in-window backup cadence (native ClickHouse BACKUP to GCS, plus a
restore spot-check) that the walking skeleton "didn't yet need". Phase 3 left
the consumer as a single-process per-event writer with no validation, no
durability state, and no healthchecks; the pipeline's only recovery was Docker's
restart policy. Phase 4 makes the consumer validate on the real event shape,
survive kills without dropping or duplicating events, prove malformed input
routes to a dead-letter table instead of crashing — and gives the warehouse
tier a restorable, scheduled ClickHouse backup with a demonstrated restore.
"Demonstrated" is the operative discipline: an untested healthcheck or an
unrestored backup hasn't proven anything to the Gate 2 reviewer.

The data lifecycle is deliberately tiered, with the **mission-critical tier at the
top — hot**: ClickHouse (live: the only store holding 100% of raw events, 30-day
TTL per ADR-006, plus the minute aggregates and dead_letter), **warm**: BigQuery
warehouse (permanent, partitioned aggregates + the deterministic 10% raw sample),
**cold**: GCS backups (in-window recovery, 2-day lifecycle) — and every Phase 4
mechanism (TTL, DLQ, health-probe, backup/restore) is a control on one of those
tiers. The
warehouse SQL is a **SQL-mesh-style single source of truth**: export, parity,
and tests all exercise the same committed `{START}/{END}` SQL files (no dbt
dependency — one shared artifact, three consumers).

## 2. Scope

| In | Deliberately out (later phase) |
| --- | --- |
| **4A:** A slim Pydantic model as the **versioned data contract** on the real event shape (Q1/Q2); `dead_letter` table via migration 007 + sync router as the **contract-violation DLQ**, validation-failures only (Q3); restart-resume `consumer_state.json` on the durable disk (Q4); real client-side batcher — 1000 rows or 5s triggers (Q7); bounded in-memory dedup ring of event ids (Q5); extended stats log line (the Phase 5 DLQ-rate substrate) | Ingestion scope filter (Wikipedia-only) — **declined, recorded** (Q6): wikidata/commons ARE the volume; query-time filtering suffices; panel 4 already shows all projects = better demo |
| **4A:** SSE fixture server harness (tests/sse_fixture.py) + ch-marked integration tests: malformed → dead_letter, kill/resume zero-loss; `HEALTH_STALE_SECONDS`-style overrides so CI can prove paths without wall-clock waits | Dead-letter for transport failures — `insert_failed` stays logged+dropped; dead_letter is validation-only by design (Q3, keeps the Phase 5 DLQ-rate panel semantically honest) |
| **4B:** GX as a 4th compose service `gx` (own Dockerfile + pyproject, python 3.13 — GX pins pandas 2.1.4, no py3.14 wheel); 6-expectation suite as **contract enforcement on the warehouse side** vs the window's real data (Q10); failing-path ch-marked test; parity-style JSON log line + non-zero exit (Phase 5 alert hook) | GX enforcement inside Phase 4 — enforcement (blocking deploys on suite failure) is Phase 6, per master plan; Phase 4 proves the suite *catches* via the failing-path test |
| **4B:** hourly :30 systemd timer (`docker compose run --rm gx`), window = last completed hour (~160k rows) (Q9); CI build-push extended to push the gx image (same AR repo, no new infra) | — |
| **4B:** Healthchecks on all 3 services; consumer healthcheck = SEMANTIC freshness probe (CH `max(inserted_at)` stale → probe SIGTERMs PID 1 → container exits → `restart: unless-stopped` → resume) (Q12) | Quality SLOs + error budgets (freshness SLO, DLQ-rate SLO, insert-failure burn-rate) — owned by Phase 5: Grafana alert rules / Cloud Monitoring / Ops Agent |
| **4B:** Backup: ClickHouse local disk `backups` via `config.d/backups.xml` (lives on the ch-data disk), `BACKUP DATABASE default TO Disk(...) COMPRESSION 'zstd'` → lift to new GCS bucket `wikistream-505003-backups` (TF module, 2-day lifecycle), prune local keep 2; hourly :20 timer (Q11) | S3/HMAC off-site backups — local-disk→GCS-lift is the in-window cadence (ADR-006); S3 native interop stays out (Q11) |
| **4B:** Restore spot-check executed once for the record (RESTORE AS `restore_check` → count + sample-row match → DROP), Gate 2 recorded GO-with-caveat (procedural, no grill) | — |
| Coverage boundary update: `models.py`/`batcher.py`/`dead_letter.py`/`healthcheck.py`/`gx/suite.py` predicted → real + their test suites | — |

## 3. Locked decisions (from the grilling session)

| # | Decision |
| --- | --- |
| Q1 | **Slim Pydantic validation model** (`consumer/src/models.py`) — NOT the full 40-field Wikimedia schema. Fields: `wiki`, `title`, `user`, `event_type` required + `str`; `bot` coerced to `bool`; `length.new`/`length.old` optional ints with defaults (absent on log/no-edit events); `timestamp` parsed (`datetime.fromisoformat` — py3.13 handles the trailing `Z`; §4 verify item). `model_config = ConfigDict(extra="ignore")` — slimmest footprint that still catches schema drift. Policy narrative carries the interview value, not field count. |
| Q2 | **Timestamp policy SPLIT** (amends ADR-005 wording — recorded as an ADR-005 amendment note in the implementation-log). Per-event rejection: timestamp must (a) parse, (b) be non-empty, (c) NOT be > 5 min in the future. **Staleness** (median `event_timestamp ↔ inserted_at` lag; max future skew) is a **GX expectation, not a per-event rejection** — a reconnect re-delivers buffered events that are minutes old, so a strict stale-reject would flood dead_letter on every reconnect. |
| Q3 | **dead_letter = migration 007** (versioned, not consumer-owned): `(inserted_at DateTime64(3,'UTC'), reason String, wiki String, title String, event String)` ENGINE MergeTree, ORDER BY `(inserted_at, sipHash64(event))`, **TTL inserted_at + INTERVAL 90 DAY**. Sync single-row insert (rare path); log-and-drop on DL insert failure (accepted envelope, never crashes). **Dead letter is for INVALID DATA (validation failures) only** — transient insert_failed transport errors stay logged+dropped (keeps the Phase 5 DLQ-rate panel semantically "schema/validation drift"). In-memory DLQ counter feeds the extended stats line (4A) → Phase 5 rate. |
| Q4 | **Restart-resume**: persist `{last_event_id, total, updated_at}` as JSON on the ch-data disk via a NEW consumer compose mount. **Refinement (recorded):** the mount is `${STATE_DIR:-./state}:/state`, where `STATE_DIR=/mnt/ch-data/state` on the VM (same durable disk, sibling of `clickhouse/`) and `./state` locally (gitignored) — NOT `${CH_DATA_DIR}` itself: the checkpoint must never nest inside the CH data dir / backup scope. Write ATOMICALLY (tmp + `os.replace`), flush DEBOUNCED (~every 2s or on id change, never per event). **Critical invariant (design, §6 4.1.3): `last_event_id` in the file never advances past the last *durably-inserted* event id** (last successful batch flush or dead_letter insert) — an id held only in an unflushed batch must NOT be persisted, else a kill between flush and persist loses the gap events. ADD flush-on-exit to the SIGTERM handler (graceful path currently just cancels). kill-proof must demonstrate zero loss (AC4). |
| Q5 | **Dedup**: bounded in-memory **event-id ring** of the last ~50k ids (~20 min at ~44 ev/s), **mark-at-enqueue, skip-at-insert, count `duplicates_skipped`**. Ring is memory-only: after a kill the server's replay refills it, so no persistence needed. Scoped claim: "zero duplicates across observed reconnect/kill windows; the BQ parity check is the long-tail safety net" — recorded honestly, NO engine change, no MV double-count footgun; ceiling (50k ≈ 20 min) documented in-code. |
| Q6 | **Ingestion scope filter (Wikipedia-only): DECLINED, recorded as considered-and-declined.** Rationale: wikidata/commons ARE the volume (better demo, panel 4 shows all projects); query-time filtering already suffices; a consumer-side filter adds complexity with zero CV value. Logged as a considered decision, not silently skipped. |
| Q7 | **Real client-side batcher** (`batcher.py`, per ADR-004 design): batch to 1000 rows or 5 s (≈220 rows/batch at observed 44 ev/s); **both triggers flush**; flush = one `insert` with `(inserted_at, event)` pairs (MATERIALIZED columns compute at insert — 001 proven, so the batch insert shape is identical to today's). Per-event insert path is RETAINED only for dead_letter. `async_insert=1` stays (the 3C export's `SYSTEM FLUSH ASYNC INSERT QUEUE` guard remains the export-side cross-check). |
| Q8 | **GX runtime = 4th compose service `gx`**: own `gx/Dockerfile` (python:3.13-slim + the uv COPY pattern from consumer), own `gx/pyproject.toml` pinned `great_expectations[clickhouse]==0.18.22` + `clickhouse-sqlalchemy==0.2.9` + `clickhouse-driver==0.2.11` + `SQLAlchemy==1.4.54` (Phase 0.4 spike-known-good set), image `us-central1-docker.pkg.dev/wikistream-505003/wikistream-consumer/gx:latest`
(hardcoded — review fix F1: nothing on the VM sets `GX_IMAGE` and startup.sh is
frozen, so any `${GX_IMAGE:-x}` default besides the AR ref would make boot's
`docker compose pull` fail with "manifest unknown" on `x`; local dev gets the
same tag via `docker compose build gx`); run by systemd timer via `docker compose
run --rm gx`. Adding gx to the compose file is git-pulled at VM boot (**no startup.sh edit, no ForceNew**). |
| Q9 | **GX cadence:** hourly at :30 (offset after export :00 / parity :05, independent); window = **LAST COMPLETED HOUR** (~160k rows at observed rate); 24–48 scheduled runs over the accumulation window before Phase 6 enforcement. |
| Q10 | **GX expectation list** (each maps to a Phase 3 typed column): (1) row count in (50k, 500k) for the window; (2) null rate == 0 on wiki/title/event_type/is_bot/length_new/event_timestamp (materialized JSONExtract → null only if field absent = schema drift); (3) event_type distribution ⊆ known set {edit, new, log, categorize, …}; (4) event_timestamp freshness — median lag vs inserted_at < 5 min AND no future skew > 5 min; (5) wiki cardinality > 100; (6) bot ratio in (0.05, 0.40) — catches the bot flag breaking both directions. **PLUS failing-path ch-marked test**: suite run against a deliberately-bad fixture table asserts ≥ 1 expectation FAILS (proves the suite catches, not just runs). VM scheduled run green with parity-style JSON log line + non-zero exit hook for Phase 5. |
| Q11 | **Backup = ClickHouse local disk, no HMAC/S3 interop.** `config/clickhouse/config.d/backups.xml` defines local disk `backups` at `…/var/lib/clickhouse/backups/` — INSIDE the existing CH data bind → lives on the ch-data disk, survives recreates. `BACKUP DATABASE default TO Disk('backups','<name>') COMPRESSION 'zstd'` (raw_events + MVs + dead_letter + schema_migrations in one shot), then `gcloud storage cp -r` to NEW TF GCS bucket `wikistream-505003-backups` (lifecycle age 2 days) + VM SA objectCreator/objectViewer, then PRUNE local keep 2. Restore path: `gcloud storage cp` latest back → `RESTORE DATABASE default AS restore_check FROM Disk('backups','<name>')` → count + sample-row-match → `DROP DATABASE restore_check`. Exact BACKUP/RESTORE clause syntax + COMPRESSION keyword = §4 build-time items. **Grant note:** boot.sh's bootstrap + `bootstrap-user.dev.sql` add `GRANT BACKUP, RESTORE ON default.* TO wikistream` (privilege names = §4 item; boot.sh is git-tracked → editing it is NOT a ForceNew). |
| Q12 | **Healthchecks on ALL 3 services.** clickhouse = in-container `clickhouse-client --query "SELECT 1"`; grafana = HTTP `/api/health` (wget vs curl presence = §4 build-time item; default `wget -q --spider http://localhost:3000/api/health`, fallback `curl -fsS`); consumer = **SEMANTIC freshness probe** (`consumer/src/healthcheck.py`, sync, uses clickhouse-connect — image has it, no curl needed): CH `SELECT max(inserted_at)`; older than threshold → log + `os.kill(1, SIGTERM)` → PID 1 is `/bin/uv`, NOT python (the consumer CMD is
already exec-form `["uv","run","python","-m","src.consumer"]` — review fix F3) →
**verify uv forwards SIGTERM to the python child (§4 item)** → container exits → `restart: unless-stopped` restarts → resume reconnects from persisted state. This is what makes ADR-004's "a wedged-but-alive container is caught" actually TRUE (Docker's restart policy alone only catches exits). Threshold 5 min default (a Wikimedia outage > 5 min triggers a harmless restart — resume works, that's the point); `HEALTH_STALE_SECONDS` env override so CI/VM prove the stale → SIGTERM leg without waiting 5 min. |

Plus handoffs that are **constraints, not choices**:

- **The three proof legs are procedural commitments** (stated and agreed, no grill):
  1. **KILL** — `docker kill wikistream-consumer` mid-stream on the VM; watch unattended auto-restart + resume zero-loss (cross-uses 4A restart-resume).
  2. **MALFORMED** — `tests/sse_fixture.py`, a ~40-line stdlib SSE fixture server emitting proper CRLF frames incl. one malformed JSON/data event; consumer with `STREAM_URL` pointed at it. Used BOTH by CI (ch-marked integration test asserting the bad event lands in dead_letter) AND the VM demo.
  3. **RESTORE** — per 4.2.8 procedure, executed once for the record.
- **Gate 2** is recorded at 4B end with the same GO-with-caveat discipline as Gate 1 (recorded, cross-referenced, not re-litigated).
- **GX is py3.13-only**: GX pins pandas 2.1.4 (no py3.14 wheel) — the gx image must be 3.13 regardless of the host venv (Phase 3 precedent).
- `docker compose run --rm gx` needs the image present on the VM → CI build-push must push it BEFORE the 4B apply (job1 build-push runs before job2 apply — existing ordering).
- `system.tables.ttl_expression` ABSENT on CH 26.3 — the dead_letter TTL check reuses the tests' `hasColumnInTable → SHOW CREATE` fallback pattern (Phase 3 build fact).
- CH 26.3 rejects `INTERVAL 90d/1h` shorthand — dead_letter TTL must be `INTERVAL 90 DAY` (Phase 3 build fact, carries forward).
- docker exec `-i` (stdin) required for piped input — applies to backup and restore `clickhouse-client` invocations the same as export (Phase 3 build fact).
- Consumer log contract: ci.yml's compose-smoke greps `connected url=` and
  `Traceback` today (Phase 1 AC2); the Phase 5 DLQ-rate panel and this phase's
  hero metric parse `inserted events=N total=M` — the extended stats line KEEPS
  that prefix and appends counters as a SUFFIX (format-stability = §4 item; review nit N2).
- ADR-004's data-loss window (per-event insert gap) is what 4A's batcher + resume closes; the at-most-once insert semantics and the never-crash contract (BLE001 ignore) carry forward unchanged.
- Runtime/cost: no new GCP APIs (backups bucket = existing storage API), no new IAM principals, no bootstrap ceremony in Phase 4.

## 4. Prerequisites

- Phase 3 exit criteria met (implementation-log Phase 3, AC1–AC21) **and** Gate 1 recorded GO-with-caveat — ALREADY on record; Phase 4 opening cross-references it (§8).
- `infra/bootstrap` + `infra/main` in their applied state from Phase 3 (VM running, static IP 34.148.138.220, stack green, parity green).
- ch-data disk attached and mounted (Phase 3 carry-forward); `startup.sh` is FROZEN — every Phase 4 change lands in git-tracked files (compose, boot.sh, migrations, config/, scripts/, infra TF) that are pulled/applied at boot. **No startup.sh edit anywhere in Phase 4 → no ForceNew → no manual re-attach-disk dance.**
- **Build-time re-verification checklist** (Vision §9; verify at build — every item gets a log outcome line, "confirmed" vs "fallback used: X"):

| Item | Status |
| --- | --- |
| CH 26.3 native `BACKUP` syntax: `BACKUP DATABASE default TO Disk('backups','<name>') COMPRESSION 'zstd'` — clause order + COMPRESSION keyword availability (zstd became default-true in 22.x; keyword may be unrecognized on 26.3 → omit, log the default) | verify at build against the VM; fallback: no COMPRESSION clause |
| CH 26.3 `RESTORE` syntax: `RESTORE DATABASE default AS restore_check FROM Disk('backups','<name>')` — whether `AS <name>` on DATABASE-level restore is accepted; if not, `DROP`/`RENAME` the live DB is NOT acceptable → fallback: per-table RESTORE into a fresh `restore_check` DB is impossible (RESTORE creates tables); alternative fallback: restore into the same names is hazardous → **verify FIRST**, design the restore DB name per result | verify at build (the restore spot-check is blocked otherwise); fallback documented in §9 |
| `config.d/backups.xml` accepted: `/clickhouse/storage_configuration/disks/backups/type=local` + `path`, mounted read-only into the container; CH logs config load at start | ✅ standard; verify container starts + `SELECT * FROM system.disks` lists `backups` |
| Privileges `BACKUP` and `RESTORE` valid grant names on CH 26.3 (`GRANT BACKUP, RESTORE ON default.* TO wikistream`) | verify at build against the VM; fallback: verify the exact privilege spelling/error on 26.3 (blanket `GRANT ALL` is DENIED on 26.3 per implementation-log §0.4 — review nit N5) |
| GX 0.18.22 programmatic API shape: `get_context()`, `context.sources.add_sql(...)` (vs legacy DataContext/SimpleCheckpoint), `add_table_asset`, `get_validator`, `add_checkpoint(batch_request)`, `result.success` — the exact 0.18 surface | verify at build; fallback = ADR-005's named risk: if SQLAlchemy/GX-ClickHouse coupling breaks, **native-SQL fallback** (python + clickhouse-connect doing the same 6 checks) adopted FOR REAL, recorded |
| clickhouse-sqlalchemy engine URL + dialect against CH 26.3 (`clickhouse://wikistream:pass@host:8123/default`) | ✅ spike-proven (Phase 0.4); re-verify against the 3.13 image |
| `docker compose run --rm gx` picks up `.env` interpolation (CLICKHOUSE_PASSWORD) like the consumer service | ✅ same mechanism as consumer; verify with a run |
| pydantic v2 parses trailing-`Z` timestamps (py3.13 `datetime.fromisoformat` accepts `Z` since 3.11) | ✅ standard; verify against a captured real event in tests |
| **uv SIGTERM forwarding** — consumer CMD is ALREADY exec-form (`["uv","run","python","-m","src.consumer"]`), so PID 1 = `/bin/uv`; `os.kill(1,SIGTERM)` lands on uv and must reach the python handler (state save + exit) | ⚠️ VERIFY at build against the image; fallback: uv exits the container on SIGTERM anyway → `restart: unless-stopped` + durable-id replay → **same zero-loss guarantee, minus the graceful save-state** (review fix F3) |
| `wget` present in grafana:13.1.1 (alpine base) vs `curl`; `python` + `clickhouse-connect` in consumer image (probe needs no curl) | verify at build; fallback wget→curl or `wget --spider` |
| SSE fixture framing: Wikimedia recentchange `data:` lines are single-line JSON with CRLF framing; fixture server must emit `id:` lines + CRLF (WHATWG) so the parser exercises the real path | verify against a captured payload raw line in tests |
| Wikimedia re-delivery-on-reconnect (dedup assumption): does the endpoint replay from a `Last-Event-ID`? The 4A kill-proof measures it empirically regardless (duplicates_skipped + zero-loss both observed) | measured by the kill-proof; no assumption made in claims |
| Stats-line format stability: `inserted events=%d total=%d` prefix must remain for compose-smoke/Phase-1 AC2 greps; counters appended as ` dead_lettered=... insert_failed=... duplicates_skipped=... resumed_from=...` | ⚠️ verify existing grep strings in .github/workflows/ci.yml don't match the full line (they match substrings) — keep the prefix, extend the suffix |
| Consumer `/state` write permission: image runs as root; VM dir `/mnt/ch-data/state` root-owned → OK; local `./state` gitignored | ✅ by construction |
| gx image in CI: `docker/build-push-action` extended to push a second target (`wikistream-gx`) to the SAME AR repo — no new TF resource (AR repo hosts multiple images) | verify CI job1 output lists both images |
| systemd `StandardOutput=append:/var/log/wikistream-gx.log` (≥ v240, Debian bookworm OK) — gx's container FS is ephemeral (`--rm`), so the log must land on the host | ✅ standard; verify journald/append interplay at build |
| dead_letter TTL projection asserted via `SHOW CREATE` fallback (system.tables.ttl_expression is absent on 26.3) | ✅ Phase 3 pattern; asserted in tests/migrations |

## 5. Target file structure

```
consumer/
  src/
    models.py                        # NEW (4A): slim Pydantic model + timestamp policy (Q1/Q2)
    batcher.py                       # NEW (4A): 1000-rows/5s batch assembly + flush (Q7)
    dead_letter.py                   # NEW (4A): sync DL router — validation failures only (Q3)
healthcheck.py    # NEW (4B): freshness probe → SIGTERM PID 1 (Q12)
    consumer.py                      # EDIT (4A): validate→dedup→batch pipeline; resume; stats line
    sse.py                           # unchanged
  Dockerfile                         # EDIT (4A): exec-form CMD confirmed/fixed (§4)
  pyproject.toml                     # EDIT (4A): + pydantic>=2,<3
migrations/
  007_dead_letter.sql                # NEW (4A): dead_letter table + 90-day TTL (Q3)
bootstrap-user.dev.sql    # EDIT (4B): + GRANT BACKUP, RESTORE (Q11)
scripts/
  boot.sh                            # EDIT (4B): gx systemd install step + backup units install (extends the 3C cp line) + GRANT BACKUP, RESTORE in bootstrap SQL
warehouse/         # NEW (4B): same host-side layout as export/parity (Phase 3C precedent)
backup.sh          # NEW (4B): BACKUP DATABASE → lift to GCS → prune local (Q11)
  wikistream-backup.{service,timer}   # NEW (4B): OnCalendar *:20:00, Persistent, Type=oneshot
gx/                                  # NEW (4B)
  Dockerfile                         # python:3.13-slim + uv copy pattern (consumer precedent)
  pyproject.toml                     # pin GX 0.18.22 [clickhouse] + sqlalchemy 1.4.54 + clickhouse-*  ; requires-python >=3.13,<3.14
  uv.lock                            # NEW (4B): committed — Dockerfile `uv sync --frozen` requires it
  suite.py                           # programmatic GX context/validator, 6 expectations (Q10), log line + non-zero exit
  wikistream-gx.{service,timer}      # OnCalendar *:30:00, Persistent, Type=oneshot, docker compose run --rm gx
config/
  clickhouse/config.d/backups.xml    # NEW (4B): local disk 'backups' (Q11)
infra/main/
main.tf            # 4B: labels.phase → "4"; module "backups" wiring
modules/backups/   # NEW (4B): backups.tf + variables.tf (bucket, 2-day lifecycle, VM SA IAM)
  modules/compute/                   # unchanged (no startup.sh edits = no ForceNew)
docs/planning/coverage-boundary.md   # EDIT: models/batcher/dead_letter/healthcheck/gx predicted→real
docs/implementation-log.md           # each PR's evidence (incl. per-§4-item outcomes, Gate 2 record)
tests/
  src/consumer/test_validation.py    # NEW (4A, NOT ch): model + timestamp policy + reason taxonomy
  src/consumer/test_batcher.py       # NEW (4A, NOT ch): 1000/5s triggers, flush shape
  src/consumer/test_resume_dedup.py  # NEW (4A, NOT ch): state IO atomicity, durable-id invariant, ring
  src/consumer/test_healthcheck.py    # NEW (4B, NOT ch): pure probe logic w/o CH + one ch-marked env test
  src/consumer/test_malformed_to_dead_letter.py  # NEW (4A, ch): fixture server → bad event in DL, good events inserted
  src/consumer/test_kill_resume_zero_loss.py     # NEW (4A, ch): restart-with-state replay → no dup/loss
  sse_fixture.py                     # NEW (4A): stdlib SSE fixture server (imported by tests, reused on VM)
  gx/test_gx_suite.py                # NEW (4B, ch): suite green on valid fixture + ≥1 expectation FAILS on bad fixture
  migrations/test_migrations.py      # EDIT: 007 case (DL table + TTL via SHOW CREATE fallback)
docker-compose.yml     # EDIT (4A): consumer /state mount (STATE_DIR-derived); 4B: gx service + 3 healthchecks
.github/workflows/apply.yml          # EDIT (4B): build-push pushes gx image too
.gitignore                           # EDIT (4A): + state/ ($STATE_DIR default resolves here locally)
```

`state/` (local, gitignored — `.gitignore` gains `state/` in 4A) holds
`consumer_state.json`; on the VM the same file lives at
`/mnt/ch-data/state/consumer_state.json` — the compose default
`${STATE_DIR:-${CH_DATA_DIR:-ch-data}/../state}` resolves to the ch-data disk
SIBLING of `clickhouse/` with ZERO startup.sh/.env changes (review fix F4:
nothing sets `STATE_DIR` on the VM, so an `./state` default would silently land
on the boot disk).

## 6. Tasks

Task tracker — pre-populated 2026-08-12 for implementation. Flip each row's
status as the task is worked; task detail below; evidence and numbers land in
`docs/implementation-log.md` Phase 4 (task headings pre-populated there too).

| # | Task | Status |
| --- | --- | --- |
| 4.1.1 | models.py: slim Pydantic model + timestamp policy (Q1/Q2) | ☐ to do |
| 4.1.2 | dead_letter: migration 007 + sync router (Q3) | ☐ to do |
| 4.1.3 | restart-resume: /state mount + atomic consumer_state.json + durable-id invariant + flush-on-exit (Q4) | ☐ to do |
| 4.1.4 | batcher.py: 1000-rows/5s flush, integrated into the consumer pipeline (Q7) | ☐ to do |
| 4.1.5 | dedup ring (Q5) + extended stats log line | ☐ to do |
| 4.1.6 | tests: unit (validation/batcher/resume-dedup) + ch-marked integration (malformed→DL, kill/resume) + sse_fixture (Q1–Q5, Q7) | ☐ to do |
| 4.1.7 | 4A PR → gated deploy → VM checkpoint → log (malformed + kill proofs live) | ☐ to do |
| 4.2.1 | gx/ scaffolding: pyproject, Dockerfile, compose service (Q8) | ☐ to do |
| 4.2.2 | gx/suite.py: 6 expectations + failing-path test + log line/non-zero exit (Q10) | ☐ to do |
| 4.2.3 | systemd wikistream-gx.service/timer + boot.sh install step (Q9) | ☐ to do |
| 4.2.4 | CI: build-push gx image + ch-marked GX tests wired into analytics-tests | ☐ to do |
| 4.2.5 | 4B PR → gated deploy → scheduled green run + log/exit hook visible (plus one failing-path demo) | ☐ to do |
| 4.2.6 | healthchecks on 3 services + consumer freshness probe + HEALTH_STALE_SECONDS (Q12) | ☐ to do |
| 4.2.7 | backup: config.d/backups.xml, backup.sh, TF backups module, systemd timer, grants (Q11) | ☐ to do |
| 4.2.8 | restore + spot-check procedure executed once for the record | ☐ to do |
| 4.2.9 | 4B PR → deploy → verification battery → Gate 2 record → log + coverage boundary | ☐ to do |

---

### 4A — Consumer resilience (PR 1)

The only PR that changes consumer *pipeline logic* (4B adds the healthcheck
module to the same image — a 2-line src addition, not a pipeline change). No
startup.sh edit, no ForceNew: compose + code are git-pulled/built at VM boot
(consumer image rebuilt + pushed by CI job1, pulled at boot).

### 4.1.1 — models.py: the data contract (Q1/Q2)

**`consumer/src/models.py`** — pydantic v2 `BaseModel`:

```python
class EventLength(BaseModel):
    new: int = 0
    old: int = 0

class WikiEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    wiki: str
    title: str
    user: str
    event_type: str  # JSON 'type'
    bot: bool         # coerced
    length: EventLength | None = None
    timestamp: datetime  # parsed; fromisoformat handles trailing Z on py3.13
```

Plus the timestamp policy (Q2): `validate_timestamp(ts) -> str | None` returns
a `reason` (or None) — parse failure → `"timestamp_unparseable"`, empty →
`"timestamp_missing"`, `ts > now + 5 min` → `"timestamp_future"`. Staleness is
NOT here (it's GX's job). Failed `WikiEvent.model_validate(json.loads(data))`
→ validation reason `"validation:...";` the raw `data` is what lands in
dead_letter (Q3) — never log the reason as an exception (never-crash contract).

**Verify:** `tests/consumer/test_validation.py` — good event parses (real
captured payload); missing field / wrong type / unparseable ts / future ts /
empty event each produce the expected reason; `extra="ignore"` tolerated;
bot coercion matrix (true/false/1/0/"yes").

### 4.1.2 — dead_letter: the contract-violation DLQ — migration 007 + sync router (Q3)

**`migrations/007_dead_letter.sql`** (no guard — `CREATE TABLE IF NOT EXISTS`
idempotent):

```sql
CREATE TABLE IF NOT EXISTS default.dead_letter (
    inserted_at DateTime64(3, 'UTC'),
    reason      String,
    wiki        String,
    title       String,
    event       String
) ENGINE = MergeTree
ORDER BY (inserted_at, sipHash64(event))
TTL inserted_at + INTERVAL 90 DAY
```

**`consumer/src/dead_letter.py`** — `write_dead_letter(client, *, reason, wiki, title, event)`:
single sync insert (`inserted_at = now UTC`), `async_insert=0` (must be durable
immediately — the audit path), on exception log `WARNING dead_letter_write_failed
reason=...` and DROP (accepted envelope — never crash, never retry-spin). The
router is called ONLY for validation failures (Q3), and the counter feeds the
stats line.

**Verify:** migration test (edit `tests/migrations` — 007 applies, table exists,
TTL via `SHOW CREATE` fallback); unit test with a faked failing client asserts
log-and-drop; ch-marked integration test in 4.1.6 asserts a malformed event
produces exactly one DL row with reason + raw event.

### 4.1.3 — restart-resume (Q4)

**`docker-compose.yml`:** consumer service gains
`- ${STATE_DIR:-${CH_DATA_DIR:-ch-data}/../state}:/state`. On the VM the
default resolves to `/mnt/ch-data/state` (sibling of `clickhouse/`, same
durable disk — **zero startup.sh/.env wiring needed**, review fix F4);
locally to `./state` (gitignored in 4A). `STATE_DIR` remains as an explicit
override for VM deploys that want a different home.

**`consumer/src/consumer.py`:**
- `load_state()` / `save_state()` — read/write `/state/consumer_state.json`
  (`{"last_event_id": "...", "total": 123, "updated_at": "..."}`); write = tmp
  file + `os.replace` (atomic); `atomic=True`; on read failure → WARNING +
  treat as absent (fresh start) — never crash.
- **Durable-id invariant:** the persisted `last_event_id` is updated ONLY from
  the tail of a successful batch flush (or a dead_letter insert) — never from
  the enqueue-point id. Concretely: the batcher's flush returns the max event
  id it flushed; `consume_forever` advances the persisted id to that value (or
  keeps it if flush failed). An event being buffered-but-unflushed is NOT
  durable → not persisted → the server's replay re-delivers it after a kill.
- `resumed_from` = the persisted `last_event_id` on startup (logged + stat).
  Graceful SIGTERM handler: cancel task as today AND `save_state()` flush
  (the persisted id is already ≤ durable, but flush the counters + updated_at).
- Initial `Last-Event-ID` header = persisted id (or absent on first boot).

**Verify:** `tests/consumer/test_resume_dedup.py` — atomic write (tmp+rename
leaves no partial file), load-missing-file → None, save/load round-trip;
resume-from-id drives the initial header; **invariant test:** simulated batch
with ids [10..20] flushed with a kill mid-batch → persisted id == 10 (the last
durable), never 20.

### 4.1.4 — batcher.py (Q7)

**`consumer/src/batcher.py`** — `EventBatcher(max_rows=1000, max_age_s=5.0)`:

- `add(event_row, event_id)` → appends `(inserted_at, event_json)`; marks the
  id in the dedup ring (see 4.1.5); returns "flush due" when
  `len(rows) >= max_rows` OR `now - first_added_at >= max_age_s`.
- `flush()` → single `client.insert(table="raw_events", column_names=
  ["inserted_at","event"], data=rows, settings={"async_insert":1,
  "wait_for_async_insert":0})`; on success returns the max event id in the
  batch (→ 4.1.3 invariant); on exception returns None + `insert_failed +=
  len(rows)` (existing WARNING `insert_failed event=` contract, batch-sized).
- Time source injected for tests; `first_added_at` semantics make the 5s
  trigger tick from the first item, not page-aligned.
- Per-event insert path REMAINS only for dead_letter (4.1.2).

**Verify:** `tests/consumer/test_batcher.py` — 1000th row triggers flush; age
trigger fires at 5 s (fake clock); flush shape is exactly (inserted_at, event)
pairs; failed flush keeps rows? (no — dropped, at-most-once contract,
counted) ; max-id return math.

### 4.1.5 — dedup ring + extended stats (Q5)

**`consumer.py`:** `collections.deque(maxlen=50_000)` event-id ring +
`set`-style membership via deque + a `set` mirror (deque for eviction order
only, or a `dict` LRU-ish — implementation detail, ~50k ids ≈ 20 min at
~44 ev/s, ceiling comment in-code). Mark-at-enqueue (4.1.4 batcher); a
re-delivered id is skipped at enqueue with `duplicates_skipped += 1`.

Stats line (fire on flush or 60 s — same cadence as today), format stability
(§4): `inserted events=%d total=%d dead_lettered=%d insert_failed=%d
duplicates_skipped=%d resumed_from=%s` — the substrate for Phase 5's DLQ-rate
panel and this phase's "zero dropped-in-window / zero duplicated" hero metric
(reproducible from the log + counts).

**Verify:** unit — duplicate id skipped + counted; ring size bounded (insert
60k → 50k kept, oldest evicted); stat counters advance correctly across a
simulated kill/resume cycle (test_resume_dedup).

### 4.1.6 — tests (Q1–Q5, Q7)

**`tests/sse_fixture.py`** — stdlib-only async HTTP server (no new deps):
serves `Content-Type: text/event-stream`, CRLF frames, `id:` lines,
`retry:` optional, then: N valid single-line JSON `data:` events, one
MALFORMED event (e.g. `data: {broken json`), keep-alive comment lines;
optional `pause`/`resume` handles so a test can kill/reconnect mid-stream.
Imported by tests; reused on the VM for the 4A/4B demos.

- `tests/consumer/test_malformed_to_dead_letter.py` (**ch**): full pipeline
  against the fixture server (`STREAM_URL` override) → assert: the malformed
  event lands as ONE `dead_letter` row with reason + raw event; the valid
  events are inserted; consumer did not crash (loop still connected);
  `dead_lettered` counter == 1.
- `tests/consumer/test_kill_resume_zero_loss.py` (**ch**): fixture server emits
  a known sequence; consume a prefix, persist state, "kill" (restart the
  consume loop with the persisted file) → replay delivered → assert final
  inserted count == total emitted AND `duplicates_skipped` > 0 (replay
  deduped) AND zero loss — the zero-loss claim as a repeatable CI assertion.
  (The real `docker kill` leg is the VM proof in 4.1.7/4.2.9.)
- Unit suites from 4.1.1/4.1.3/4.1.4/4.1.5 are `not ch` → collected by
  `unit-tests` automatically (testpaths=tests); the two ch suites join
  `analytics-tests` automatically.

**Verify:** `uv run --project consumer pytest -m "not ch" -q` green; the two
ch-marked suites green against a local CH container.

### 4.1.7 — 4A PR → deploy → VM checkpoint → log

1. PR (branch note: rides `feature/Data-Model-Depth` per user convention) →
   merge → gated apply (job1 build-push: consumer image) → VM reset (NO
   ForceNew: no startup.sh edit; compose/state dir only; VM pulls new compose
   + code at boot).
2. **VM checkpoint (micro-gate before 4B):**
   - **MALFORMED proof (live):** run `tests/sse_fixture.py` on the VM (host
     python3 or a transient container), point the consumer at it momentarily
     (`STREAM_URL` override in compose env), confirm the bad event lands in
     `default.dead_letter` (count = 1, reason populated) and the consumer
     stayed up; revert STREAM_URL; record. **Networking (review nit N10):** the
     consumer is container-isolated — `localhost` inside it is the container,
     not the VM host — so either run the fixture on the compose network (e.g.
     `docker compose run --rm --service-ports consumer`-adjacent wiring or a
     transient container on the same network) or use the VM's host IP. And if
     the fixture needs a one-off compose edit on the VM, revert it before the
     next boot: startup.sh's `git pull --ff-only || true` silently discards
     local changes to git-tracked files.
   - **KILL proof (live):** `sudo docker kill wikistream-consumer` mid-stream;
     confirm Docker auto-restarts it, resume log line shows
     `resumed_from=<id>`, `count()` grows, and the killed-window events are
     present exactly once (window sums: MV-vs-raw + `duplicates_skipped` in
     the stats line) — zero dropped/duplicated (AC4).
   - `startup done` in the log (never the Actions badge, Phase 3 discipline);
     per-§4 outcomes recorded.
3. Log evidence + numbers; only then does 4B begin.

---

### 4B — GX data quality (PR 2)

### 4.2.1 — gx/ scaffolding (Q8)

**`gx/pyproject.toml`** — `requires-python = ">=3.13,<3.14"`; deps:
`great_expectations[clickhouse]==0.18.22`, `clickhouse-sqlalchemy==0.2.9`,
`clickhouse-driver==0.2.11`, `SQLAlchemy==1.4.54`; dev: `pytest>=8`.

**`gx/Dockerfile`** — python:3.13-slim; `COPY --from=ghcr.io/astral-sh/uv:
latest /uv /bin/uv`; then **deps first**: `COPY gx/pyproject.toml gx/uv.lock
./` + `RUN uv sync --frozen --no-install-project --no-dev`, THEN `COPY gx/
./gx/` (a whole-tree copy before pyproject exists fails the sync — review fix
F8; the committed `gx/uv.lock` is mandatory for `--frozen`, §5 tree); `CMD
["uv","run","python","/app/gx/suite.py"]` (exec-form).

**`docker-compose.yml`** — service `gx`: `build: ./gx`,
`image: us-central1-docker.pkg.dev/wikistream-505003/wikistream-consumer/gx:latest`
(hardcoded AR ref — review fix F1: nothing on the VM sets `GX_IMAGE` and
startup.sh is frozen, so a `wikistream-gx:local` default would make every boot's
`docker compose pull` fail with "manifest unknown"; local dev tags the same ref
via `docker compose build gx`), `depends_on: [clickhouse]`,
`restart: "no"`, env CLICKHOUSE_* (same block as consumer, incl.
CLICKHOUSE_PASSWORD from .env), `GX_TABLE: default.raw_events`,
`GX_WINDOW_HOURS: "1"`. The service does NOT run full-time: the image is built
once; a boot-time `docker compose up -d` runs suite.py once (harmless — the
first run usually exits non-zero because boot.sh's wikistream-user bootstrap
runs AFTER `up`, and `up -d` ignores exit codes anyway with `restart: "no"`;
review nit N7) and the timer drives `docker compose run --rm gx` for
the scheduled runs.

### 4.2.2 — gx/suite.py (Q10)

**`gx/suite.py`** — programmatic GX (exact 0.18 API shape = §4 item; ADR-005's
native-SQL fallback is the named risk if the coupling breaks):

1. Window: `END = start of current UTC hour`, `START = END - 1h`; `GX_TABLE`
   env override (tests point it at fixture tables).
2. Engine: `create_engine("clickhouse://wikistream:${CLICKHOUSE_PASSWORD}@
   clickhouse:8123/default")` (host from CLICKHOUSE_HOST if overridden);
   GX connection via `sources.add_sql(name="wikistream", engine=...,
   connection_string=...)` (or the 0.18-native arrangement found at build).
3. Batch: the window query on the table (`WHERE inserted_at >= START AND
   inserted_at < END`), plus computed freshness columns for expectation (4)
   (lag = `event_timestamp - inserted_at`; skew = future-part only).
4. Six expectations (Q10): row count (50k, 500k); nulls == 0 on the 6
   columns; event_type in-set; median lag < 5 min AND max future skew ≤ 5
   min; wiki cardinality > 100; bot ratio in (0.05, 0.40).
5. `checkpoint.run()` → `result.success`; exit 0 / exit 1; write ONE JSON line
   to stdout: `{"window_start":..., "window_end":..., "success": bool,
   "run_id":..., "expectations_passed": n, "expectations_failed": m,
   "row_count":...}` (parity-style; `docker compose run` + systemd
   `StandardOutput=append:` land it in `/var/log/wikistream-gx.log`).
6. Empty-window tolerance: if the window has 0 rows → log `skipped_empty` +
   exit 0 (a fresh deploy or a downtime hour is not a data-quality failure;
   the timer's `Persistent=true` + row-count threshold catch real degrades).

**Verify:** `tests/gx/test_gx_suite.py` (**ch**) — (a) valid fixture table
(synthetic rows meeting all six) → suite exits 0, all pass; (b) **bad fixture
table** (missing wiki / non-bool bot / future timestamps / empty event_type /
tiny cardinality) → asserts ≥ 1 expectation FAILS and exit != 0 — the
"catches, not just runs" proof (Q10).

### 4.2.3 — systemd timer (Q9)

**`gx/wikistream-gx.service`** — `Type=oneshot`, `ExecStart=/usr/bin/docker
compose -f /opt/wikistream/docker-compose.yml run --rm gx`,
`StandardOutput=append:/var/log/wikistream-gx.log`,
`StandardError=append:/var/log/wikistream-gx.log`.
**`gx/wikistream-gx.timer`** — `OnCalendar=*-*-* *:30:00`, `Persistent=true`.
**`scripts/boot.sh`** (4B step, mirroring the 3C step): `cp gx/wikistream-gx.*
/etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now
wikistream-gx.timer` (absolute paths, idempotent). No startup.sh edit.

**Verify:** `systemctl is-active wikistream-gx.timer` → active; a manual
`systemctl start wikistream-gx.service` → `/var/log/wikistream-gx.log` has a
JSON line; `docker compose run --rm gx` exit 0.

### 4.2.4 — CI (Q8/Q10)

- **`.github/workflows/apply.yml` job1:** extend `docker/build-push-action` to
  ALSO build+push `wikistream-gx` (same AR repo
  `us-central1-docker.pkg.dev/wikistream-505003/wikistream-consumer/gx`, no new
  TF resource) — job1 runs before job2 (apply), so the image exists at the VM
  reset (Q8 note).
- **analytics-tests:** the new ch-marked files carry their own markers
  (`@pytest.mark.ch`) → collected automatically (testpaths=tests). GX deps live
  in `gx/pyproject.toml`, so **`.github/workflows/ci.yml` Install-dependencies
  adds `uv sync --project gx --frozen`** (the seam comment already reserves
  "Phase 4 gx/" — review fix F7); without it `analytics-tests` can't import
  `gx.suite`. Verify `-m ch` picks up
  `test_gx_suite.py` + `test_malformed_to_dead_letter.py` +
  `test_kill_resume_zero_loss.py`.
- Ruff: include `gx/` in the lint paths.

**Verify:** PR shows `analytics-tests` green including the failing-path test
(which asserts a FAILURE — it must be written to fail-loud, i.e. the test's
own assertion is `any_failed == True` while the suite process exits non-zero
toward the fixture table only); `compose-smoke` green (boot-ordered gx run).

### 4.2.5 — 4B PR → deploy → gate

1. PR → merge → gated apply (both images pushed) → VM reset → `startup done`.
2. **Gate:** at least one scheduled :30 run green — `systemctl show
   wikistream-gx.service -p ExecMainStatus` = 0 AND `/var/log/wikistream-gx.log`
   has a matching JSON line (success: true). **Failing-path demo (once):**
   point the service at an empty/bad window (e.g. `GX_TABLE` override or a
   manual `run --rm` with a bad fixture) → non-zero exit + log line
   `success: false` → restore normal config → next scheduled run green again.
   Record evidence + per-§4 outcomes; only then the 4B phase-final battery.

---

### 4B (cont.) — Healthchecks, backup & restore (PR 2)

### 4.2.6 — Healthchecks (Q12)

**`docker-compose.yml`** — add to all three services:

- clickhouse: `test: ["CMD", "clickhouse-client", "--query", "SELECT 1"]`,
  interval 30s, timeout 5s, retries 3, start_period 30s.
- grafana: `test: ["CMD-SHELL", "wget -q --spider http://localhost:3000/api/
  health"]` (or curl per §4), interval 30s, timeout 5s, retries 3,
  start_period 30s.
- consumer: `test: ["CMD", "python", "-m", "src.healthcheck"]`, interval
  30s, timeout 10s, retries 2, start_period 60s (CH bootstrap + first inserts
  at boot). env += `HEALTH_STALE_SECONDS: "${HEALTH_STALE_SECONDS:-300}"`.
  (Module is `src.healthcheck` — the image `COPY`s `src/` as `src/`, same as
  `-m src.consumer`; review fix F2.)

**`consumer/src/healthcheck.py`** — sync; `is_fresh(max_inserted, now,
stale_seconds)` pure helper (unit-testable). Flow: connect via
clickhouse-connect (CLICKHOUSE_* env, same vars as the consumer), `SELECT
max(inserted_at) FROM default.raw_events`; no rows yet or fresh → exit 0; stale
→ log `healthcheck stale max(inserted_at)=... (>{HEALTH_STALE_SECONDS}s)` +
`os.kill(1, signal.SIGTERM)`; connection failure → exit 1 (Docker marks
unhealthy, does NOT restart — only the SIGTERM-exit path restarts, which is the
ADR-004 wedge-detector). The SIGTERM path is the RESUME path: PID 1 = `/bin/uv`
(exec-form CMD → **verify uv forwards SIGTERM to the python handler**; if it
does not, the container still exits and `restart: unless-stopped` + durable-id
replay resume with zero loss — review fix F3), handler saves state + exits,
`restart: unless-stopped` relaunches, consume reconnects from persisted id.

**Verify:** unit — `is_fresh` matrix (under/at/over threshold, None);
ch-marked env test — probe against live CH with `HEALTH_STALE_SECONDS=1` and a
paused consumer, asserting the *stale* branch fires (`os.kill` mocked — never
signal real PID 1 from CI, that would tear down the runner container; the
real `os.kill(1, SIGTERM)` leg is proven only on the VM); on VM: `docker ps`
shows healthy on all three; a temporary
`HEALTH_STALE_SECONDS=5` + paused-consumer run shows `unhealthy` → restart →
`resumed_from=` in logs (recorded).

### 4.2.7 — Backup (Q11)

**`config/clickhouse/config.d/backups.xml`:**

```xml
<clickhouse>
  <storage_configuration>
    <disks>
      <backups>
        <type>local</type>
        <path>/var/lib/clickhouse/backups/</path>
      </backups>
    </disks>
  </storage_configuration>
</clickhouse>
```

(path is INSIDE the existing `${CH_DATA_DIR}:/var/lib/clickhouse` bind →
ch-data disk → survives recreates; no HMAC/S3.)

**`docker-compose.yml`** clickhouse: add
`- ./config/clickhouse/config.d:/etc/clickhouse-server/config.d:ro`.

**Grants** — `scripts/boot.sh` bootstrap SQL += `GRANT BACKUP, RESTORE ON
default.* TO wikistream;` and mirror in `migrations/bootstrap-user.dev.sql`
(keep the two bodies in sync, Phase 3 rule; boot.sh edit ≠ ForceNew).

**`warehouse/backup.sh`** (git-tracked, co-located with export.sh/parity.sh
per the Phase 3C precedent; runs on the host as root, after source
`/opt/wikistream/.env`):
1. `NAME=backup-$(date -u +%Y%m%d-%H%M%S)`.
2. `docker exec -i wikistream-clickhouse clickhouse-client --user wikistream
   --password "$CLICKHOUSE_PASSWORD" --query "BACKUP DATABASE default TO
   Disk('backups','$NAME') COMPRESSION 'zstd'"` (`-i`: piped-stdin rule; syntax
   = §4 item).
3. Lift: `gcloud storage cp -r
   "${CH_DATA_DIR}/backups/$NAME" "gs://wikistream-505003-backups/$NAME"` (host
   side of the bind; VM SA token auth) — `-r` for the dir.
4. Prune local: keep the last 2 `$CH_DATA_DIR/backups/backup-*` (rm older).
5. Non-zero on any failure → the timer shows failed; GCS objects are the
   durable copy (local prune safe AFTER successful lift).

**`warehouse/wikistream-backup.{service,timer}`** — timer
`OnCalendar=*-*-* *:20:00`, `Persistent=true`; service `Type=oneshot`,
`ExecStart=/opt/wikistream/warehouse/backup.sh` (+ a log touch). **boot.sh** (4B
step): extend the 3C cp line (scripts/boot.sh ~line 55) to include the two
backup files, then install + enable, same shape as the export/parity timers.

**TF** — **`infra/main/modules/backups/`** (new, smallest shape: 3 resources):
- `google_storage_bucket wikistream-505003-backups` (location US,
  uniform_bucket_level_access, lifecycle Delete age 2 — mirrors the staging
  bucket pattern);
- `google_storage_bucket_iam_member` objectCreator + objectViewer for the VM
  SA (bucket-scoped only — dataEditor does NOT grant GCS, Phase 3 rule).
`main.tf`: `module "backups"` wiring + `labels.phase → "4"`. No new GCP API
(storage already enabled) → **no bootstrap ceremony**.

**Verify:** `terraform plan` shows bucket + IAM; after apply `gcloud storage ls
gs://wikistream-505003-backups`; `SELECT name FROM system.disks` lists
`backups`; local dir prune keeps 2; timer active; `GRANT` evidence in the
startup log / a `SHOW GRANTS FOR wikistream` line recorded.

### 4.2.8 — Restore + spot-check (once, for the record)

Procedure (recorded verbatim in the log):
1. `gcloud storage cp -r gs://wikistream-505003-backups/<latest> "${CH_DATA_DIR}/
   backups/"` (latest = `gcloud storage ls | sort | tail -1`).
2. `docker exec -i wikistream-clickhouse clickhouse-client --user wikistream
   --password ... --query "RESTORE DATABASE default AS restore_check FROM
   Disk('backups','<name>')"` (syntax = §4 item; on failure per §9's fallback).
3. Verify: `SELECT count() FROM default.raw_events` vs
   `restore_check.raw_events` — equal; one sample row: `SELECT event FROM both
   WHERE inserted_at = <max-inserted-at>` — matching (or the row-count +
   per-table MV counts if sample-key differs); dead_letter/schema_migrations
   present in restore_check.
4. `DROP DATABASE restore_check` (no residue).
This proves ADR-006's "restore works" claim — the same discipline as Gate 2.

**Verify:** log has the four steps' outputs + equality numbers (AC16).

### 4.2.9 — 4B PR → deploy → verification battery → Gate 2

1. PR → merge → gated apply (bucket + config + healthchecks) → VM reset →
   `startup done`.
2. **Verification battery (the 4B gate):**
   - `docker ps` → all 3 containers `(healthy)`; `docker inspect -f
     '{{.State.Health.Status}}' wikistream-consumer` → healthy (AC13).
   - KILL proof re-run against the HEALTHED consumer (restart policy +
     resume): `docker kill` → auto-restart → `resumed_from=` → counts equal
     (AC14, cross-refs the 4A proof — now under healthcheck supervision).
   - Systemd supervision "exercised": `docker service restart`-equivalent —
     stop the docker daemon (`systemctl stop docker`), containers gone,
     `systemctl start docker` → compose stack auto-restarts
     (`restart: unless-stopped` + the Phase 3 carry-forward that the docker
     daemon starting re-runs the compose stack); consumers+gx+timers
     recover; backup runs post-recovery via Persistent=true (AC12/AC15
     adjacent).
   - Backup: ≥1 local backup + ≥1 GCS object + prune behavior + timer active
     (AC15).
   - Restore spot-check outputs recorded (AC16).
3. **Gate 2 record** (master plan §4): "Does resilience actually hold, not just
   exist in config?" — kill-recovery ✅, malformed→dead_letter ✅, backup
   restore ✅, healthchecks exercised ✅; recorded GO-with-caveat (same
   discipline as Gate 1: an explicit record + cross-reference, not
   re-litigation; caveat = GX enforcement deferred to Phase 6 by plan).
4. Log + coverage boundary: models/batcher/dead_letter/healthcheck/gx/suite
   moved predicted → real with their suites; implementation-log Phase 4
   populated.

---

## 7. Acceptance criteria (self-checkable)

| # | Criterion | How an agent verifies it |
| --- | --- | --- |
| AC1 | Validation model catches malformed/schema-drift events | `uv run --project consumer pytest -m "not ch" -q tests/src/consumer/test_validation.py` green: missing field / wrong type / unparseable / future / empty ts → each returns its reason; a real captured event parses |
| AC2 | dead_letter table live + malformed event lands there | ch-marked `test_malformed_to_dead_letter.py` green; on VM: the MALFORMED proof (4.1.7) — `SELECT count() FROM default.dead_letter` == 1 with reason populated, consumer alive |
| AC3 | Validation-only DL semantics (transport failures excluded) | Unit + integration: an insert_failed (CH down) does NOT write dead_letter; `insert_failed` counter advances instead; code review shows the router is called solely from the validation branch |
| AC4 | Restart-resume: kill → unattended recovery → zero loss | ch-marked `test_kill_resume_zero_loss.py` green (no dup, no loss); on VM: `sudo docker kill wikistream-consumer` → auto-restart → `resumed_from=` in logs → window sums (MV-vs-raw) equal → `duplicates_skipped` > 0 on replay |
| AC5 | Dedup bounded + counted | `test_resume_dedup.py` green: duplicate skipped+counted; ring ≤ 50k ids (oldest evicted); counter exposed in the stats line |
| AC6 | Batcher flushes at 1000 rows or 5 s | `test_batcher.py` green: both triggers, flush shape `(inserted_at, event)`, max-id return used by the durable-id invariant |
| AC7 | Extended stats line present with stable prefix | Consumer logs contain `inserted events=%d total=%d dead_lettered=%d insert_failed=%d duplicates_skipped=%d resumed_from=%s`; ci.yml's existing substring greps still match (compose-smoke green) |
| AC8 | 4A integration suites green in CI | `analytics-tests` green (includes the two ch tests); `unit-tests` green; `compose-smoke` green |
| AC9 | gx image runs | VM: `docker compose run --rm gx` exit 0; container starts against CH; `docker images | grep 'wikistream/gx'` shows the AR-pushed image |
| AC10 | GX suite green on a real window | VM: ≥1 scheduled :30 run — `systemctl show wikistream-gx.service -p ExecMainStatus` = 0 AND `/var/log/wikistream-gx.log` has `success: true` + window fields |
| AC11 | GX suite catches (failing path) | `tests/gx/test_gx_suite.py` green — the bad-fixture case asserts ≥1 expectation fails + non-zero exit; recorded once on the VM (4.2.5) |
| AC12 | GX timer + log hook live | `systemctl is-active wikistream-gx.timer` → active; log line format = parity-style JSON; exit code non-zero on failure (the Phase 5 alert hook) |
| AC13 | Healthchecks healthy on all 3 services | `docker ps` shows `(healthy)` × 3; `docker inspect ... .State.Health.Status` = healthy after start_period |
| AC14 | Consumer staleness probe provably restarts | unit `is_fresh` matrix green; VM: `HEALTH_STALE_SECONDS=5` + paused consumer → unhealthy → restart → `resumed_from=` record |
| AC15 | Backup cadence running + lifted + pruned | `systemctl is-active wikistream-backup.timer` → active; `gcloud storage ls gs://wikistream-505003-backups/` non-empty; local `backups/` keeps last 2; `system.disks` lists `backups` |
| AC16 | Restore spot-check proven | Log records RESTORE AS restore_check → equal counts (live vs restore_check) → sample row match → DROP — for the latest backup |
| AC17 | Gate 2 recorded + docs consistent | implementation-log Phase 4: kill/malformed/restore proofs, per-§4-item outcomes, Gate 2 GO-with-caveat record; coverage-boundary corrected |

## 8. Verification gates (master plan wording)

> Phase 4 objective: wire in and *prove* ADR-005's validation, ADR-004's
> resilience details, and ADR-006's in-window backup cadence — "an untested
> healthcheck or an unrestored backup hasn't demonstrated anything."

Phase exit criteria = AC1–AC17. Two checkpoints, in order:

1. **4A micro-gate (HARD CHECKPOINT before 4B):** validation rejects bad events
   without crashing (AC1/AC2/AC3), restart-resume survives a kill with zero
   loss AND zero duplication on a live stream (AC4), batcher both-triggers
   exercised (AC6), dedup counted (AC5), CI green (AC8) — the consumer is now
   a supervised, resumable, validating pipeline. No 4B work starts until the
   log records these.
2. **4B gate (phase-final battery):** a scheduled GX run is green on a real
    hour of data with the JSON log + exit hook (AC9/AC10/AC12) AND the
    failing-path proof shows the suite actually catches (AC11) — so Phase 6's
    enforcement has a tested instrument, not a hand-wave — then the 4B battery:
    healthy×3 (AC13) + stale-probe restart proven (AC14) + backup
    cadence/lift/prune (AC15) + restore spot-check executed (AC16) +
    systemd-supervision-exercised run (4.2.9 battery step).

Then **Go/No-Go for Phase 5 (master plan §4) — an explicit final step, not a
separate doc: Gate 2, "does resilience actually hold, not just exist in
config?"** — recorded GO-with-caveat against AC1–AC17 with evidence pointers
(Gate 1's GO-with-caveat is on record and cross-referenced). No-Go = any AC
fails without a recorded, understood fix.

## 9. Troubleshooting notes

- **Restore fails — `AS restore_check` not accepted on DATABASE-level RESTORE
  (CH 26.3):** the §4 item gates 4.2.8. Fallback ladder: (a) verify actual
  AVAILABLE syntax first (`RESTORE DATABASE default FROM ...`), and if the
  `AS` alias is unavailable, restore INTO the same names is unsafe → (b)
  `BACKUP DATABASE` an empty throwaway DB, `RESTORE` that, and spot-check the
  *backup file* by `RESTORE TABLE` per-table into a `restore_check` DB created
  fresh (table-level RESTORE supports `AS`); document whichever path is proven.
  The Gate-2 claim only needs "restore proven for the data", not a specific
  clause.
- **Healthcheck restart storms:** threshold (5 min) exceeds any legit quiet
  period (Wikimedia outage > 5 min → one harmless restart whose resume works —
  that's the point, Q12). If storms still appear, raise `HEALTH_STALE_SECONDS`
  via compose env; never lower below the longest legit gap.
- **`/state` file missing on first boot:** treated as absent → fresh start
  (no resume); verify the mount: `docker inspect wikistream-consumer` shows
  `/state`; local `./state` gitignored.
- **Permission on `/state`:** image runs as root; if a later user change
  blocks writes, the WARNING + continued running is the contract — state
  persistence silently degrading to fresh-start remains safe (idempotent
  envelope), but fix the perms before relying on resume.
- **Dead-letter backfill:** none needed — dead_letter only starts at 4A;
  pre-007 validation failures didn't exist (no validation before 4A).
- **GX image not on the VM:** after a CI push, `docker compose pull gx` (or a
  boot-cycle); never rely on a local build at boot (`--no-build`).
- **GX expected failures on an empty window:** a fresh deploy or downtime hour
  has < 50k rows → suite skips (exit 0, `skipped_empty` line) — the initial
  tolerance note in suite.py; the row-count threshold is the real degrade
  detector, not first-run noise.
- **BACKUP denied:** `GRANT BACKUP, RESTORE` missing → re-run boot.sh's user
  bootstrap on the VM (`bash scripts/boot.sh` is idempotent) or
  `docker compose exec ... clickhouse-client --multiquery <
  migrations/bootstrap-user.dev.sql`; verify `SHOW GRANTS FOR wikistream`.
- **COMPRESSION keyword rejected (26.3):** drop the clause; zstd is the
  default local-disk compression in 22.x+; log the fallback (§4 item outcome).
- **Backup lift fails (GCS):** the local backup remains (prune only after
  successful lift — order is baked into backup.sh); re-run backup.sh; check
  VM SA token (metadata scopes) + bucket IAM.
- **Restore DB name collision:** `DROP DATABASE restore_check` before
  restoring, or use a unique `restore_check_<run>` suffix (procedure uses one
  name + drop).
- **Stats-line grep drift:** if ci.yml's compose-smoke substring greps break,
  the prefix `<inserted events=` changed — keep the suffix-only extension
  (§4 item); fix the grep, not the line.
- **Grafana healthcheck fails locally:** `/api/health` needs the server up;
  container exits 0 once Grafana listens (start_period covers boot); a failed
  grafana check with `wget/curl` absent → use the §4 fallback binary.
- **Consumer killed while a batch is unflushed:** expected — those events are
  re-delivered by replay (durable-id invariant, 4.1.3); `insert_failed` after
  a pause is the pre-restart contract, not data loss.
- **`test_kill_resume_zero_loss` flaky in CI:** the fixture server serializes
  the sequence; if the "kill" mid-batch races, assert on final totals (durable
  invariant guarantees they converge) rather than intermediate window counts.
- **GX engine host:** inside the gx container CH is `clickhouse`; if
  CLICKHOUSE_HOST is ever set to a non-container host it must stay reachable
  from the container (same trick as the consumer — hostname from env).

## 10. Handoff to Phase 5 (what Phase 5 inherits)

- **A supervised, resumable, validating consumer:** healthchecks that catch a
  wedged-but-alive container (Q12), restart-resume with the durable-id
  invariant (Q4), batcher + dedup (Q5/Q7) — Phase 5's alerts fire on real
  signals, not on a pipeline that "usually works".
- **`dead_letter` table + extended stats line:** the Phase 5 DLQ-rate Grafana
  panel's data source (`dead_lettered / total` per window, live via
  `insert_failed`/`duplicates_skipped` counters) — the panel is a SQL +
  threshold away.
- **GX instrument in production:** hourly :30 runs, JSON log line
  (`/var/log/wikistream-gx.log`), non-zero exit — Phase 5's "data quality"
  alert source (and Phase 6's enforcement input); the failing-path proof means
  the alert isn't a no-op.
- **Backup cadence + restore proof:** hourly File-Path backup lifted to GCS
  (2-day lifecycle), local prune, one executed restore spot-check — Phase 5's
  RTO/RPO story is numbers, not a config file; parity alerting already hooks
  `/var/log/wikistream-parity.log`.
- **Healthcheck + supervision state:** three healthy containers with proven
  stale-probe semantics — Phase 5's Cloud Monitoring/Ops Agent work builds on
  it (VM metadata, iops/disk metrics, uptime checks) rather than inventing it.
- **Remaining Phase 5 surface (from master plan):** 3 Grafana alerts (parity,
  GX, DLQ-rate) + Cloud Monitoring/Ops Agent + IAM review + firewall +
  IP-lockout caution (static IP 34.148.138.220 is the only public face).
- **Phase 6 now has REAL modules to enforce:** models.py/batcher.py/
  dead_letter.py/healthcheck.py/gx suite moved predicted → real in the
  coverage boundary, with their suites already at/near the Phase 6 bar.
- **ADR-005 amendment note:** timestamp policy split (per-event: parse + no
  future; staleness: GX expectation) is recorded in the implementation-log for
  the ADR diff.