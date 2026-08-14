# Phase 5 Implementation Plan — Observability & Security Hardening

**Status:** LOCKED 2026-08-13. Decisions ratified in a grilling session (three rounds, Q1–Q12 below). Ground-truth verified against live gcloud state and the running codebase before locking (IAM dump, firewall rules, `bq show`, Grafana 13.1 provisioning schema, Google provider alert-policy schema).
**Reviewed:** 2026-08-13 by a single code-review subagent — 4 BLOCKER, 6 MAJOR, 7 MINOR findings, all fixed in-place below (no finding left open).
**Position in the hierarchy:** Master Plan §5 (Phase 5 — Observability & Security Hardening) → this document → Phase 4's plan is the structural template.
**Branch:** `feature/Observability-&-Security` (single carrier branch for the whole phase, per the established convention).

---

## 1. Objective

**"Prove it, don't configure it" — the same standard Phase 4 set.**

Phase 5 turns WikiStream from a pipeline that *works* into a pipeline that is *provably observable and provably defensible*. The headline is the **chaos battery**: **5 Grafana alerts + 2 Cloud Monitoring alerts, 8 deliberate failure injections, and not one failure mode that went undetected.** Every alert in this plan is demonstrated firing under a deliberate, reversible fault — not assumed working because the YAML looks right.

Two non-overlapping monitoring layers, per ADR-010:

- **Application / pipeline health (Grafana built-in alerting).** Five rules fed by a new `pipeline_health` ClickHouse table that the running system writes to continuously — the consumer's heartbeat (15 s cadence), the hourly parity check, and the hourly GX data-quality suite. Deliberately **not** Prometheus: Prometheus is already demonstrated on Ahmed's W3C ETL (ADR-010's own rationale), and a pull scraper cannot observe one-shot timer processes like parity/GX anyway. A push-based health table in the pipeline's own database is both simpler and a stronger story.
- **Infrastructure health (Google Cloud Monitoring).** VM hypervisor-level metrics (uptime — agentless) plus memory/disk via the **Ops Agent** (agentless does not cover disk — and disk is the metric that matters: the 30 GB ch-data volume is written continuously and backed up in-window; it filling up is the project's #1 silent-kill risk per vision §7). Two policies: **disk-almost-full** and **VM-unreachable**.

And the security half:

- **Least-privilege IAM**: a documented before/after review of the VM's service account against actual usage — every binding justified or removed.
- **Firewall lockdown**: the review already surfaced a real finding — the GCP **default-allow-ssh rule is still open to 0.0.0.0/0** (SSH to the VM is currently world-accessible). Phase 5 closes the default rules and proves rejection of non-whitelisted IPs.

Exit bar: **all 7 alerts demonstrated firing (5 Grafana → Slack, 2 Cloud Monitoring → email), least-privilege documented, firewall restriction proven, and the dashboards still updating after the whole battery ran.**

---

## 2. Scope

| In | Deliberately out (later / not this phase) |
| --- | --- |
| `pipeline_health` ClickHouse table (migration 008) as the single alerting telemetry source | Prometheus (deliberately excluded by ADR-010; W3C ETL already carries it) |
| Consumer heartbeat task (15 s, deltas vs previous tick, cumulative counters) | Agent-based tracing / OpenTelemetry |
| Parity + GX status rows written to `pipeline_health` from the existing timer wrappers | Alerting on the BigQuery dashboard panel state (panel 6 is visual-only; parity alert covers drift) |
| 5 Grafana alert rules (consumer-down, DLQ-rate, CH-insert-failure, parity-drift, gx-fail) | A Grafana alert per expectation (alert on the suite verdict, not per-expectation noise) |
| Slack contact point + notification policy (Grafana → Slack, the only channel Grafana speaks natively) | Cloud Monitoring → Slack (platform constraint, see Q9 — needs a relay function; noted as stretch) |
| Ops Agent install (idempotent, via `boot.sh` — **no VM replacement**) + `monitoring.metricWriter` grant | Additional Cloud Monitoring dashboards (2 policies only; dashboards are Grafana's job) |
| 2 Cloud Monitoring alert policies (disk-almost-full, VM-unreachable) → email | Instance-group / autoscaling alerts (single-VM architecture) |
| IAM review doc (before/after matrix, every binding justified) | Removing grants that the review cannot yet prove unused (risk of breaking a live timer) |
| Firewall lockdown: delete the four open `default-allow-*` rules; keep custom allow rules IP-scoped | Reverse proxy / TLS / custom domain (explicitly out by ADR-010) |
| Chaos battery: 8 deliberate injections, one demo window, evidence captured | A permanent "chaos test" CI job (manual, scheduled demo window only) |
| Optional 7th dashboard panel "Pipeline health" (reuses the alert queries) | New dashboard layout / alert list panel |
| Docs: `phase-5-implementation.md`, `iam-review.md`, implementation-log Phase 5 section | ADR changes beyond the two amendments already recorded (ADR-010 telemetry decision noted in §3) |

---

## 3. Locked decisions (from the grilling session)

| # | Decision |
| --- | --- |
| Q1 | **Telemetry source: a `pipeline_health` table in ClickHouse, written by the system itself.** Not Prometheus (already on the CV via W3C ETL; cannot observe one-shot timers). Pushed by: consumer heartbeat (15 s), parity (hourly, after export), GX (hourly, after run). Grafana queries it via the existing `wikistream-clickhouse` datasource. |
| Q2 | **Alert set: ALL FIVE.** consumer-down, DLQ-rate, ClickHouse-insert-failure (the three named in master plan §5) **plus** parity-drift and gx-fail (the two live failure sources Phase 4's handoff named; GX is now instrumented in production, so a failing suite must page). |
| Q3 | **Channel: Slack webhook.** Ahmed creates a workspace + incoming webhook + `#wikistream` channel (hard prerequisite, §4). URL lives in Secret Manager (`slack-webhook-url`), fetched by `boot.sh` at boot, never in startup.sh, never in the repo. |
| Q4 | **Ops Agent install goes in `boot.sh`** (idempotent `command -v` guard), **not** startup.sh — **this supersedes what was agreed during the grilling session** (a startup.sh edit + VM replacement was accepted then; the boot.sh route is strictly better and is what this plan does). Rationale: `boot.sh` is the repo-managed extension point (Phase 3C established it, it survives recreates, and it already installs systemd units); startup.sh stays frozen → **zero ForceNew, zero VM replacement in the entire phase.** |
| Q5 | **Disruptive testing: OK, in one dedicated demo window** (~90 min, immediately after the 5C deploy — Ahmed confirmed 2026-08-13 it runs the moment Phase 5 completes, no separate booking). The chaos battery is the phase's proof. |
| Q6 | **Firewall proof: manual Cloud Shell probe** (Ahmed's choice). The plan's agent verifies rule *state* via `gcloud compute firewall-rules describe`; Ahmed runs the live probes from Cloud Shell (a Google egress IP, provably not his) and captures screenshots as evidence. Positive control: same probes from home IP succeed. |
| Q7 | **CV headline: the chaos battery story** — "7 alerts, 8 deliberate injections, zero undetected failure modes." The plan's objective leads with it, and §11 gives the interview talking points. |
| Q8 | **Consumer-down detection = absence of heartbeat rows.** The consumer cannot observe its own death; the absence of its push is the signal. Co-firing during a ClickHouse outage is intended semantics (the health-signal channel is down — that IS a failure), documented in §9. |
| Q9 | **Cloud Monitoring → email, not Slack — a refinement of the grilling decision, flagged.** GCP's CM "slack" channel type is deprecated and requires a legacy Slack app token (modern incoming webhooks cannot be used); CM webhook channels POST GCP's JSON envelope, which Slack rejects at the API. Grafana, in contrast, renders the Slack message format natively — so the split is: **5 Grafana alerts → Slack, 2 CM alerts → email to Ahmed's Gmail.** If CM→Slack is insisted on, it needs a tiny Cloud Function relay (listed as a stretch, §10). |
| Q10 | **DLQ-rate formula guards the demo's exact condition.** A naive `dead/inserted` returns 0 when the stream is 100% malformed (inserted=0) — precisely what the chaos battery injects. Correct: `dead / (inserted + dead)` over the window. Locked at §6.5A.2. |
| Q11 | **Secret provisioning split:** Terraform creates the `slack-webhook-url` secret and grants the VM SA `secretAccessor`; Ahmed sets the *value* via `gcloud secrets versions add` (never in TF state). The Cloud Monitoring notification channel takes the alert email as a plain `tfvars` value (non-sensitive). |
| Q12 | **`boot.sh` reloads Grafana provisioning + the Slack env at the root.** Phase 3B's finding (3B-8: provisioning changes need a Grafana reload; deploy alone leaves stale provisioning) is fixed by `boot.sh` fetching the Slack secret into `.env` (if absent) and running **`docker compose up -d grafana`** — not `restart` (which does not recreate the container, so a new env var would never apply). The compose edit adds `SLACK_WEBHOOK_URL: ${SLACK_WEBHOOK_URL:-}` to the grafana service `environment:` (compose `.env` is interpolation-only — the var must reach the container at create time). Every boot recreates Grafana with fresh provisioning + the secret. |

**Handoffs that are constraints, not choices:**

- `startup.sh` **must not be edited in this phase** (metadata_startup_script is ForceNew). Every VM-side change routes through `boot.sh` (git-pulled from the repo) or Terraform. The 3C precedent (one edit) is not repeated.
- Grafana datasource UIDs are fixed: `wikistream-clickhouse` (default) and `wikistream-bigquery` (GCE auth). Alert rules target `wikistream-clickhouse` only.
- Alert rule UIDs: ≤ 40 chars, `[A-Za-z0-9-_]` only (Grafana provisioning constraint).
- ClickHouse 26.3 constraints stand: TTL spells `INTERVAL 7 DAY` (never `7d`). The migrations `-- guard:` convention is **optional** and the text after `-- guard:` is evaluated as a SQL predicate — prose there aborts (or silently skips) the migration. Guard-free migrations (mirroring 007) rely on statement idempotency (`CREATE TABLE IF NOT EXISTS`).
- `.env` is rendered only by startup.sh; `boot.sh` appends, never rewrites (grep-guard `SLACK_WEBHOOK_URL` before appending).
- Coverage boundary: new consumer heartbeat code is business-critical → 100% line coverage, and a correction entry in `coverage-boundary.md` (mirroring the 4B healthcheck entry).

---

## 4. Prerequisites

- **Slack workspace + incoming webhook + `#wikistream` channel — Ahmed, first action of the phase.** Nothing in 5A's proof works without it. (Q3) **DONE 2026-08-13 — webhook URL received from Ahmed (not recorded in this repo — it is a secret). Value loads via `gcloud secrets versions add slack-webhook-url` after the 5A.5 apply creates the secret.**
- **Gmail address for Cloud Monitoring notifications** (Ahmed's — the two CM alerts email it). Provide at 5B apply time as `alert_email` tfvar. **DONE 2026-08-13 — `ahmedikram30@gmail.com` (goes into `terraform.tfvars`, non-sensitive).**
- **Demo window** (~90 min, immediately after the 5C deploy, before writing the Phase 5 log section). Chaos battery at §6.DEMO. **DONE 2026-08-13 — Ahmed: "we'll just run all that right after we complete phase 5" — no separate booking; runs the moment 5C's Go/No-Go is recorded, same session.**
- **`ch-data` disk label verified** for the disk-almost-full filter: confirm via `df -hT /mnt/ch-data` that the device is `/dev/disk/by-id/google-ch-data` (expected from Phase 3A) and note the exact device name shown in Cloud Monitoring Metrics Explorer at build time (the plan's filter uses `metric.labels.device = "ch-data"` OR `metric.labels.mount_point = "/mnt/ch-data"` — pick whichever the agent emits; verified at §6.5B.3).
- **Current GX row counts noted** (the gx-fail demo injects a false `GX_ROW_MIN`; the alert must not trip on the *real* hourly run during the battery — `GX_ROW_MIN` default 50000 is far below live volumes, so this is a sanity check, not a change).
- **CI access to the two new GitHub secrets** (optional, for `-var` flows): none required this phase — secret value is set via gcloud, alert email via tfvars. Simpler than 4B.

### Build-time re-verification checklist

| Item | Status |
| --- | --- |
| Slack webhook URL exists and posts to `#wikistream` (send a test message) | ☑ URL provided + test message delivered 2026-08-13 (not stored in repo) |
| VM SA currently holds exactly: `bigquery.jobUser` (project), `bigquery.dataEditor` (dataset WRITER), `secretAccessor`×2, AR reader, storage object roles ×2 buckets — confirmed via `gcloud projects get-iam-policy` + `bq show` | ☐ |
| `default-allow-ssh/rdp/icmp/internal` still present (they are — verified 2026-08-13) — this is the 5C finding, not a mistake | ☐ |
| Grafana provisioning dir bind-mounted (`./grafana/provisioning:/etc/grafana/provisioning:ro` — confirmed in compose) | ☐ |
| `docker compose restart grafana` is the required provisioning reload (3B-8) | ☐ |
| `gcloud` CLI on the VM (startup.sh installs it — confirmed) | ☐ |
| `command -v google-ops-agent` currently empty on the VM (expected) | ☐ |
| Consumer image builds green before 5A PR (`uv run --project consumer pytest -m "not ch"` local + CI) | ☐ |
| ClickHouse `wikistream` user has INSERT on `default.*` (boot.sh grants — confirmed) | ☐ |

---

## 5. Target file structure

```
WikiStream/
├── # NEW  docs/planning/phase-5-implementation.md          (this document)
├── # NEW  docs/planning/iam-review.md                      (5C — before/after IAM matrix)
├── # EDIT docs/planning/coverage-boundary.md               (heartbeat.py + gx status-report corrections)
├── # EDIT docs/implementation-log.md                       (Phase 5 section, filled at §6.DEMO)
├── # EDIT docker-compose.yml                   (grafana service env: `SLACK_WEBHOOK_URL: ${SLACK_WEBHOOK_URL:-}`)
├── infra/
│   ├── main/
│   │   ├── # NEW  modules/monitoring/                     (5B)
│   │   │   ├── main.tf        (notification channel + 2 alert policies)
│   │   │   ├── variables.tf   (project_id, alert_email, ch-data filter)
│   │   │   └── outputs.tf     (policy ids)
│   │   ├── # EDIT main.tf                (module calls; extend the `secret_ids` list — passes slack-webhook-url to modules/iam)
│   │   ├── # EDIT secrets.tf             (slack-webhook-url secret; value set via gcloud, never in TF state)
│   │   ├── # EDIT variables.tf           (alert_email; slack secret id if referenced)
│   │   ├── # EDIT terraform.tfvars       (alert_email)
│   │   ├── # EDIT modules/iam/iam.tf     (5B: VM SA + `monitoring.metricWriter`; slack secret accessor alongside the existing two)
│   │   ├── # EDIT modules/network/network.tf (5C: null_resource default-rule deletions)
│   │   ├── # UNCHANGED modules/compute/…   (no IAM changes needed — verify at build)
│   │   └── # UNCHANGED templates/startup.sh (frozen — do not touch)
│   └── … (other modules untouched)
├── migrations/
│   ├── # NEW  008_pipeline_health.sql    (CREATE TABLE IF NOT EXISTS default.pipeline_health)
│   └── … (000–007 unchanged)
├── scripts/
│   └── # EDIT boot.sh                    (Ops Agent install; Slack secret fetch + .env append + grafana restart)
├── consumer/
│   └── src/
│       ├── # NEW  heartbeat.py           (build_row + heartbeat_loop)
│       └── # EDIT consumer.py            (spawn/await heartbeat task in main())
├── gx/
│   └── # EDIT suite.py                   (report_status() — write pipeline_health row before return)
├── warehouse/
│   └── # EDIT parity.sh                  (pipeline_health INSERT after emit_log)
├── grafana/
│   └── provisioning/
│       └── alerting/
│           └── # NEW  alerts.yml         (apiVersion 1: contactPoints + groups(5 rules) + policies)
└── tests/
    ├── src/consumer/
    │   └── # NEW  test_heartbeat.py      (build_row pure + heartbeat_loop ch-marked)
    ├── migrations/
    │   └── # EDIT test_migrations.py     (008 schema + TTL via SHOW CREATE)
    └── gx/
        └── # NEW  test_status_report.py  (row-builder pure + ch-marked insert)
```

Startup script edit count this phase: **zero**. VM replacement count: **zero**.

---

## 6. Tasks

| # | Task | Status |
| --- | --- | --- |
| 5.0.1 | Ahmed: create Slack workspace, incoming webhook, `#wikistream`; post test message | ☑ done 2026-08-13 — test message delivered (URL kept out of repo) |
| 5A.1 | Migration 008 — `pipeline_health` table | ☐ to do |
| 5A.2 | Consumer heartbeat — `heartbeat.py` + wiring in `consumer.py` | ☐ to do |
| 5A.3 | Parity + GX status writers (`parity.sh`, `gx/suite.py`) | ☐ to do |
| 5A.4 | Grafana alerting provisioning — `alerts.yml` (5 rules + contact point + policy) | ☐ to do |
| 5A.5 | Slack secret + boot.sh wiring (Ops Agent install included in 5B.1) | ☐ to do |
| 5A.6 | 5A PR → deploy → VM checkpoint → log | ☐ to do |
| 5B.1 | Ops Agent install via boot.sh | ☐ to do |
| 5B.2 | VM SA `monitoring.metricWriter` + TF `modules/monitoring` (channel + 2 policies) | ☐ to do |
| 5B.3 | Metrics-flow verification (disk/percent_used visible in Cloud Monitoring) | ☐ to do |
| 5B.4 | 5B PR → deploy → checkpoint | ☐ to do |
| 5C.1 | IAM review doc (`iam-review.md`) — enumerate, justify, tighten | ☐ to do |
| 5C.2 | Firewall lockdown — delete `default-allow-*` (TF null_resource) | ☐ to do |
| 5C.3 | 5C PR → deploy → `gcloud` rule-state verification | ☐ to do |
| DEMO | Chaos battery — 8 injections, evidence, Phase 5 log section | ☐ to do |
| GATE | Go/No-Go review + acceptance criteria pass | ☐ to do |

---

### 5.0.1 — Slack workspace + webhook (Ahmed, first action)

**Ahmed, not an agent.** Create a Slack workspace (any name; suggestion: `wikistream`), enable **Incoming Webhooks**, create a webhook to a new channel `#wikistream`, and post a test message. The resulting URL (format `https://hooks.slack.com/services/T…/B…/…`) is required for 5A.4–5A.6, 5B.2, and DEMO.

**Verify:** the test message is visible in `#wikistream`.

---

### 5A — Grafana alerting (PR 1, code-only)

**5A.1 — Migration 008: `pipeline_health`**

`migrations/008_pipeline_health.sql`:

```sql
-- 008_pipeline_health.sql — plain-comment first line (mirrors 007): see note below
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
```

Notes: idempotent — `CREATE TABLE IF NOT EXISTS` re-runs safely every boot via `migrations/apply.sh`, so the **guard-free plain-comment first line is correct**. **Do NOT put a `-- guard:` line here**: apply.sh evaluates the text after `-- guard:` as a SQL predicate, and prose would abort the migration under `set -e` (or silently mark it skipped, leaving the table missing — all five rules would target nothing). `7 DAY` spelled out (26.3 rejects `7d`); `ORDER BY (source, ts)` serves the alert queries (filter by source, order by ts) directly.

**Verify:** `uv run --project consumer pytest -m ch -q tests/migrations/` green (existing migrations suite picks up 008); on VM after deploy: `docker compose exec -T clickhouse clickhouse-client --query "SHOW CREATE TABLE default.pipeline_health"` returns the schema.

**5A.2 — Consumer heartbeat (`consumer/src/heartbeat.py` + `consumer.py`)**

New file `consumer/src/heartbeat.py`:

- `build_row(counters, previous, ts)` → pure function returning the 5-tuple `(ts, "consumer", "heartbeat", 1.0, detail_json)` for one insert row. `detail_json` carries **deltas vs the previous tick** (not cumulative totals — the alert queries sum deltas over a window): `inserted_delta`, `dead_lettered_delta`, `insert_failed_delta`, `duplicates_skipped_delta`, plus `total`, `dead_lettered`, `insert_failed`, `duplicates_skipped`, `resumed_from` (cumulative context). `previous=None` → all deltas 0 (first tick).
- `heartbeat_loop(client, counters, stop, interval=15.0)` → async: `while not stop.is_set(): await asyncio.sleep(interval); insert one row`. Uses the consumer's existing client (`clickhouse_connect` async, already at `consumer.py:21/363`) and insert path: `await client.insert("default.pipeline_health", data=[row], column_names=["ts","source","metric","value","detail"], settings={"async_insert": 1, "wait_for_async_insert": 0})`. **Insert errors are logged and swallowed — alerting must never take the consumer down.** On `stop`, one final flush of the current tick before returning.

`consumer.py` edits (surgical):

1. `from src.heartbeat import heartbeat_loop` (module already importable — package layout is `src`).
2. In `main()` **seed the `counters` dict with the resume context before task creation** — the dict built at `consumer.py:351–356` has keys `{total, dead_lettered, insert_failed, duplicates_skipped}` only, and the heartbeat reads `resumed_from`; a bare `counters["resumed_from"]` would `KeyError` on the first tick and kill the heartbeat task (R1 would fire permanently). Seed: `counters["resumed_from"] = resumed_from or "none"` (the `resumed_from` local at `consumer.py:348`).
3. Beside the `consume_forever` task (`consumer.py:376`): `heartbeat_task = asyncio.create_task(heartbeat_loop(client, counters, stop))`.
4. On shutdown, await both tasks in the existing ≤10 s join (heartbeat's final tick flushes).

Design invariants: the `counters` dict is mutated in place by `consume_forever` (Phase 4A), so the heartbeat reads live values with zero locking (GIL + asyncio single thread); heartbeat writes are fire-and-forget async inserts (consistent with batcher semantics); the heartbeat row volume is ~5 760 rows/day (15 s cadence) — negligible against the TTL.

**Verify:** `uv run --project consumer pytest -m "not ch" -q tests/src/consumer/test_heartbeat.py` green (build_row deltas, first-tick zero, resumed_from passthrough, detail JSON shape); `uv run --project consumer pytest -m ch -q tests/src/consumer/test_heartbeat.py` green (live insert + loop exits on stop); after deploy, on VM: `docker compose exec -T clickhouse clickhouse-client --query "SELECT count(), min(ts), max(ts) FROM default.pipeline_health WHERE source='consumer'"` returns rows spanning the last minutes with ~15 s gaps.

**5A.3 — Parity + GX status writers**

`warehouse/parity.sh` — after the final `emit_log`, before exit:

```bash
# Phase 5: push the verdict into pipeline_health (alerting telemetry)
_P5_VALUE=0.0; [ "$status" = "ok" ] && _P5_VALUE=1.0
_P5_DETAIL=$(printf '{"status":"%s","window_start":"%s","window_end":"%s"}' "$status" "$WINDOW_START" "$WINDOW_END" | sed "s/'/\\\\'/g")
docker exec -i "$CLICKHOUSE_CONTAINER" clickhouse-client --user wikistream --password "$CLICKHOUSE_PASSWORD" \
  --query "INSERT INTO default.pipeline_health (ts, source, metric, value, detail) VALUES (now(), 'parity', 'result', $_P5_VALUE, '$_P5_DETAIL')" \
  || { echo "[$(date -u)] parity: pipeline_health write failed" >&2; }
```

Same `docker exec` pattern parity.sh already uses for CH-side queries — no new dependency. The row's `ts` is the run time (UTC `now()`); the detail carries the window so a stale-window drift is distinguishable from a missing run.

`gx/suite.py` — extract a pure `report_status(verdict)` → builds the `(ts, "gx", "result", 1.0|0.0, detail)` row and inserts it via the clickhouse-connect client gx already has (its `CLICKHOUSE_*` envs; `CLICKHOUSE_HOST` default `clickhouse`). **Call it at EVERY exit path — not just the final verdict — or R5 can be silently suppressed by an early return** (the alert then sees the last good hourly 1.0 row):

- the CH-connection-failure path (`suite.py:82`) — write `value=0.0`; a failed run IS a failure;
- the row-count guard (`suite.py:115`, `row_min <= row_count <= row_max` fails) — write `value=0.0`; **this is exactly what DEMO #5 injects, and without this write the demo would emit nothing**;
- the final verdict path before `return 0 if result.success else 1` (`suite.py:194`).

Cleanest as a `finally:`/`atexit` wrapper so a future exit path cannot forget it.

- `value = 1.0 if verdict["success"] else 0.0` (empty-window skip counts as success — it already exits 0).
- detail = `json.dumps({k: verdict[k] for k in ("window_start","window_end","run_id","expectations_passed","expectations_failed","row_count")})` — single-quote-escaped.
- **The status write NEVER masks the GX verdict**: a write failure is logged + `warn` only, and the suite's exit code still reflects GX.

**Verify:** `uv run --project gx pytest -m ch -q tests/gx/test_status_report.py` green (row-builder pure; live insert against the VM's CH in the ch-marked test — note the gx venv: `--project gx`, NOT `consumer`); on VM: run `bash /opt/wikistream/warehouse/parity.sh` → `SELECT count() FROM default.pipeline_health WHERE source='parity' AND ts > now()-INTERVAL 1 HOUR` = 1, value 1.0; run `docker compose run --rm -e GX_ROW_MIN=999999999 gx` → exits 1 **AND** `SELECT count() FROM default.pipeline_health WHERE source='gx' AND ts > now()-INTERVAL 1 HOUR` = 1, value 0.0.

**5A.4 — Grafana alerting provisioning (`grafana/provisioning/alerting/alerts.yml`)**

`apiVersion: 1`, three top-level keys: `contactPoints`, `groups`, `policies` (Grafana 13.1 file-provisioning supports all three).

- **contactPoints:** one contact point `slack-alerts` with `receivers: [{uid: slack-alerts, type: slack, settings: {url: '${SLACK_WEBHOOK_URL}'}}]` (env-var expansion works in provisioning files; the env is set from compose at 5A.5). **Build-time check covers the `uid` field**: if Grafana 13.1 rejects `uid` on a receiver, drop it — the `/api/v1/provisioning/contact-points` check below catches it before the PR.
- **groups:** one group `wikistream` (folder `wikistream`, created by provisioning), `interval: 30s`, five rules — all `datasourceUid: wikistream-clickhouse`, each `data[0]` = the raw-SQL query model exported from Grafana Explore at build time (embed the API-shaped `model`, don't hand-write it):

| Rule | UID | Query (against `default.pipeline_health`) | Condition | `for` | noDataState | execErrState |
| --- | --- | --- | --- | --- | --- | --- |
| R1 Consumer down | `consumer-down` | `SELECT count() FROM default.pipeline_health WHERE source='consumer' AND ts > now() - INTERVAL 90 SECOND` | `A < 1` (0 rows in window → fires) | 45 s | **Alerting** | Alerting |
| R2 DLQ rate high | `dlq-rate-high` | `SELECT sum(JSONExtractFloat(detail,'dead_lettered_delta')) / (sum(JSONExtractFloat(detail,'inserted_delta')) + sum(JSONExtractFloat(detail,'dead_lettered_delta'))) FROM default.pipeline_health WHERE source='consumer' AND ts > now() - INTERVAL 5 MINUTE` | `A > 0.05` | 2 m | NoData | Error |
| R3 CH insert failures | `ch-insert-failure` | `SELECT sum(JSONExtractFloat(detail,'insert_failed_delta')) FROM default.pipeline_health WHERE source='consumer' AND ts > now() - INTERVAL 5 MINUTE` | `A > 0` | 2 m | NoData | Error |
| R4 Parity drift | `parity-drift` | `SELECT if(count()=0, 0, argMax(value, ts)) FROM default.pipeline_health WHERE source='parity' AND ts > now() - INTERVAL 150 MINUTE` | `A < 1` | 1 m | **Alerting** | Error |
| R5 GX suite failed | `gx-fail` | `SELECT if(count()=0, 0, argMax(value, ts)) FROM default.pipeline_health WHERE source='gx' AND ts > now() - INTERVAL 90 MINUTE` | `A < 1` | 1 m | **Alerting** | Error |

Semantics worth locking (documented in §9 too):

- **R1** alert-on-absence: `SELECT count()` always returns **one row with value 0** when nothing matches — `noDataState` never triggers for a scalar count — so the threshold is `A < 1` (0 rows in the last 90 s → fires). Co-fires with R3 when ClickHouse itself is down — intended.
- **R2** denominator is `inserted + dead` so a 100%-malformed stream (the demo) still shows a 1.0 rate. Threshold 0.05 (5%) tunable; the live stream's organic DLQ rate is ~0 (Phase 4A proofs only ever produced DL rows under injection).
- **R3** fires on the *accumulated* delta after a recovery window (the consumer's `insert_failed` increments only when flushes fail; rows are at-most-once-dropped) — documented ordering, not a bug.
- **R4** fires on **absence** (no parity row in 2.5 h — the hourly run plus a miss) OR **value 0** (drift/error verdict). Same for **R5** (90 min window).

- **policies:** one policy — `receiver: slack-alerts`, `group_by: [grafana_folder]`, `group_wait: 30s`, `group_interval: 5m`, `repeat_interval: 4h`, `object_matchers: [{key: "grafana_folder", operator: "=", value: "wikistream"}]`. (No routing splits this phase — all five rules → Slack.)

**Verify (build time, before PR):** after `docker compose up -d` locally + `docker compose restart grafana`, `curl -u admin:$GRAFANA_PASSWORD http://localhost:3000/api/v1/provisioning/alert-rules | jq length` = 5; each rule's `data[0].model` parses; contact point listed at `/api/v1/provisioning/contact-points`. On VM after deploy: same two API calls against `http://localhost:3000` (basic auth admin — Phase 2 proved this works on 13.1).

**5A.5 — Slack secret + boot.sh wiring**

Terraform (`secrets.tf` + `modules/iam/iam.tf`):

- `google_secret_manager_secret slack-webhook-url` in `secrets.tf`. **The accessor binding goes in `modules/iam/iam.tf`** where the existing two secret accessors live — extend the `secret_ids` list passed from `main.tf` (the established pattern) rather than inventing a root-level `iam.tf` file.
- Ahmed sets the value: `echo -n '<webhook url>' | gcloud secrets versions add slack-webhook-url --data-file=-` (project `wikistream-505003`).

`scripts/boot.sh` (after the existing systemd-unit step — **boot.sh is `set -euo pipefail`; every Phase 5 addition must be non-fatal**):

1. Ops Agent install (5B.1 — same PR is fine, or split; see 5B).
2. Slack env wiring — **wrapped so a fetch failure cannot abort boot** (missing secret or missing accessor is a 5A.5 ordering issue, not a boot-killer):

   ```bash
   grep -q '^SLACK_WEBHOOK_URL=' /opt/wikistream/.env || {
     SLACK_WEBHOOK_URL=$(gcloud secrets versions access latest --secret=slack-webhook-url 2>/dev/null) \
       && echo "SLACK_WEBHOOK_URL=${SLACK_WEBHOOK_URL}" >> /opt/wikistream/.env \
       || echo "[boot] slack-webhook-url unavailable — alerting works, Slack delivery missing" >&2
   }
   ```

3. `docker compose up -d grafana` — recreates the grafana container, picking up the new env (`SLACK_WEBHOOK_URL` from the compose edit) AND the bind-mounted provisioning. This is the root fix for the 3B-8 stale-provisioning footgun (Q12). (`restart` would not apply a new env var — `up -d` is required.)

**Verify:** on VM after deploy: `grep -c SLACK_WEBHOOK_URL /opt/wikistream/.env` = 1; `docker compose exec -T grafana sh -c 'echo -n "$SLACK_WEBHOOK_URL" | wc -c'` > 0; `curl -u admin:$PASS http://localhost:3000/api/v1/provisioning/contact-points` shows `slack-alerts`; **test notification**: from the Grafana UI → Alerting → Contact points → `slack-alerts` → "Test" → message lands in `#wikistream` (screenshot = evidence).

**5A.6 — 5A PR → deploy → VM checkpoint → log**

Branch `feature/Observability-&-Security`. PR contains: migration 008 (+ `tests/migrations/test_migrations.py` edit), heartbeat.py + consumer.py edit, parity.sh + gx/suite.py edits, `alerts.yml`, boot.sh edits, docker-compose.yml (grafana env), tests, coverage-boundary.md + this plan doc. Merge → CI apply → VM reset → boot. On boot, `boot.sh` runs migration 008, installs Ops Agent, wires Slack, recreates Grafana.

**Checkpoint (before any 5B work):** `startup done` in `/var/log/wikistream-startup.log`; `docker compose ps` all healthy; 4 rows/min in `pipeline_health` (consumer heartbeat, 15 s cadence); parity + gx rows appearing hourly; 5 rules + contact point in the provisioning API; test notification in Slack; `uv run --project consumer pytest` fully green (unit + ch). Log the checkpoint in the Phase 5 section (with evidence lines).

---

### 5B — Cloud Monitoring + Ops Agent (PR 2)

**5B.1 — Ops Agent install (in `boot.sh`, idempotent)**

```bash
# Phase 5: Ops Agent (disk/memory metrics — agentless CM cannot see them)
# Non-fatal by design (boot.sh is set -e): a transient network failure must
# not abort the boot — the pipeline keeps running, disk alerting is delayed.
if ! systemctl is-active --quiet google-cloud-ops-agent.service; then
  curl -fsSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh \
    && sudo bash add-google-cloud-ops-agent-repo.sh --also-install \
    && rm -f add-google-cloud-ops-agent-repo.sh \
    || { echo "[boot] Ops Agent install failed — boot continues, no disk/VM-metrics alerting" >&2; }
fi
```

Rationale (Q4): `boot.sh` is the repo-managed extension point — the install survives VM recreates via git pull, and startup.sh stays frozen (zero ForceNew). No VM replacement; the superseded grilling decision is recorded at §3 Q4.

**Verify:** on VM: `systemctl is-active google-cloud-ops-agent.service` → `active` (the 2.x agent ships no `google-ops-agent` binary, so the guard and this check key on the unit — review-corrected 2026-08-14); Cloud Monitoring → Metrics Explorer shows `agent.googleapis.com/disk/percent_used` for the instance (5B.3).

**5B.2 — VM SA `monitoring.metricWriter` + `modules/monitoring`**

- `modules/iam/iam.tf`: `google_project_iam_member` VM SA + `roles/monitoring.metricWriter` (Ops Agent's write path; least-privilege — writer, not admin), alongside the VM SA's existing project-scoped grants. *Spec addition (2026-08-14, 5B.3): also `roles/logging.logWriter` — the agent's own startup self-check FAILs without it (`logEntries.create` PermissionDenied on its ping log); it is the documented second half of Ops Agent IAM. Deliberately a second resource, not a for_each — a for_each would rename/replace the live binding.*
- `infra/main/modules/monitoring/`:
  - `google_monitoring_notification_channel` — `type: "email"`, `labels: {email_address: var.alert_email}` (Q9: CM speaks email, not Slack).
  - `google_monitoring_alert_policy` **disk-almost-full** — `condition_threshold`: filter `metric.type="agent.googleapis.com/disk/percent_used" AND resource.type="gce_instance" AND metric.labels.device="/dev/sdb"` (5B.3-verified 2026-08-14: the descriptor has NO `mount_point` label and `device` is the kernel name with the `/dev/` prefix — `/dev/sdb` is ch-data; the originally planned `device="ch-data" OR mount_point="/mnt/ch-data"` could never match any series, and the policy API does not validate unknown labels at create time), `comparison: COMPARISON_GT`, `threshold_value: 80`, `duration: 300s`, `aggregations: {alignment_period: 300s, per_series_aligner: ALIGN_MEAN}`, `trigger: {count: 1}`, `combiner: OR`, `notification_channels: [monitoring_channel.name]`, `enabled: true`, `documentation` noting the ch-data volume is the pipeline's durability surface (vision §7).
  - `google_monitoring_alert_policy` **vm-unreachable** — `condition_absent` (provider 7.43 name for the spec's `condition_missing`): filter `metric.type="compute.googleapis.com/instance/uptime" AND resource.type="gce_instance"`, `duration: 120s`, `trigger: {count: 1}`, `aggregations: {alignment_period: 120s, per_series_aligner: ALIGN_MEAN}` (fires when the metric stops being reported — the agentless path), same channel. *Spec deviation (2026-08-14): `instance/uptime` is DELTA-kind; the API 400s without a perSeriesAligner even on an absence condition — aggregations added, semantics unchanged.*

**Verify (apply-time):** `terraform plan` shows +1 channel, +2 policies, +1 IAM binding; `terraform apply` green; policies visible in Cloud Monitoring UI (screenshot).

**5B.3 — Metrics-flow verification**

Confirm the filter before finalizing the policy, and re-confirm after:

1. Cloud Monitoring → Metrics Explorer → `agent.googleapis.com/disk/percent_used`, filter to the VM, note the **actual device/mount label** the agent emits (`ch-data` vs `/mnt/ch-data`) — align the policy filter to it.
2. After apply: the VM row for `agent.googleapis.com/disk/percent_used` and `agent.googleapis.com/memory/percent_used` populates (uptime data was already flowing agentless).

**Verify:** `gcloud monitoring time-series list --filter='metric.type="agent.googleapis.com/disk/percent_used"' --format='table(metric.labels.device, points[0].value.doubleValue)'` returns rows for the ch-data device.

**5B.4 — 5B PR → deploy → checkpoint**

PR: `modules/monitoring/`, `modules/iam/iam.tf`, `main.tf`, `variables.tf`, `terraform.tfvars`, boot.sh edit (if 5B.1 wasn't in the 5A PR). Merge → apply (gated, `production` env) → the pipeline's unconditional "Reset VM" step runs (harmless — boot.sh is idempotent; don't rely on a skipped reset). **Checkpoint:** both policies live; Ops Agent streaming disk+memory; alert email verified by firing the policies manually once (Metrics Explorer can't fake it — see DEMO injection 6/7 for the real proof; the manual fire is optional).

---

### 5C — Security (PR 3, LAST — the IP-lockout risk is why)

**5C.1 — IAM review (`docs/planning/iam-review.md`)**

A before/after matrix, one row per principal × binding (data already verified 2026-08-13):

| Principal | Binding | Purpose (justification) | Verdict |
| --- | --- | --- | --- |
| `wikistream-deploy@…` (CI) | 8 project roles: artifactregistry.admin, bigquery.admin, compute.admin, iam.serviceAccountAdmin, iam.serviceAccountUser, resourcemanager.projectIamAdmin, secretmanager.admin, storage.admin | Drives all IaC + CI (4B established this set) | Retain with rationale |
| `wikistream-vm@…` | `bigquery.jobUser` (project) | jobs.create is project-scoped (3C blocker) | Retain |
| `wikistream-vm@…` | `bigquery.dataEditor` (dataset WRITER) | export.sh loads kpi tables | Retain |
| `wikistream-vm@…` | `secretAccessor` ×2 (clickhouse-password, grafana-admin-password) | boot.sh + startup.sh secret fetch | Retain |
| `wikistream-vm@…` | `artifactregistry.reader` (AR repo) | `docker compose pull` at boot | Retain |
| `wikistream-vm@…` | storage objectCreator/objectViewer/legacyBucketReader ×2 buckets | backup.sh + export staging + storage.buckets.get | Retain |
| `wikistream-vm@…` | **`monitoring.metricWriter` (NEW in 5B)** | Ops Agent write path | Retain |
| `984854414993-compute@…` (default SA) | none (verified) | — | Retain (clean) |
| Dataset `wikistream` | WRITER/OWNER/READER rows (verified via `bq show`) | 3C export + CI ownership | Retain |

The review's conclusion is **"reviewed, retained with rationale"** — the genuinely exposed surface this phase is the firewall, not the SA grants (every VM grant maps to a boot/timer path). The doc records the reasoning per row so an interviewer can see the discipline. The firewall finding (§5C.2) is the review's headline.

**Verify:** `docs/planning/iam-review.md` committed; every row in the matrix cross-checks against a fresh `gcloud projects get-iam-policy` + `bq show` (no phantom bindings).

**5C.2 — Firewall lockdown**

`modules/network/network.tf`: a `null_resource` with `local-exec` (alongside the existing custom rules):

```hcl
resource "null_resource" "disable_default_firewall_rules" {
  triggers = { rules = "default-allow-ssh,default-allow-rdp,default-allow-icmp,default-allow-internal" }
  provisioner "local-exec" {
    command = "gcloud compute firewall-rules delete default-allow-ssh default-allow-rdp default-allow-icmp default-allow-internal --quiet --project=${var.project_id} 2>/dev/null || true"
  }
}
```

Why these four: `default-allow-ssh` (tcp:22, **0.0.0.0/0 — the VM's SSH is currently world-open**; the custom `allow-ssh` rule scoped to `209.35.91.152/32` then becomes the *only* SSH path), `default-allow-rdp` (3389, open, unused), `default-allow-icmp` (open, unused), `default-allow-internal` (10.128.0.0/9 — broader than the custom `allow-internal` 10.0.0.0/24 and otherwise redundant). `|| true` for idempotency (already-deleted on a re-apply). OS Login/metadata access is not firewall-governed, so the deletion cannot lock Ahmed out of the console — the worst case is losing direct TCP SSH, recoverable from Cloud Shell (§9).

The custom rules stay exactly as-is (allow-ssh/grafana/clickhouse from `209.35.91.152/32`, allow-internal 10.0.0.0/24) — verified correct at Phase 2, tightened implicitly by removing the default overlays.

**Verify (agent, rule state):** `gcloud compute firewall-rules list --format='table(name,disabled,sourceRanges.list(),allowed[].ports.list())'` shows **no** `default-allow-*` rows and exactly 4 custom rows; `gcloud compute firewall-rules describe default-allow-ssh` → `NOT_FOUND`.

**5C.3 — 5C PR → deploy → rule-state verification**

PR: `iam-review.md`, `network.tf` null_resource. Merge → apply → confirm deletion (5C.2 Verify). **Then the live probe is Ahmed's (Q6):**

- From **Cloud Shell** (egresses from a Google IP — provably not his): `curl -v --connect-timeout 5 http://34.148.138.220:3000/` and `http://34.148.138.220:8123/` → **connection refused/timeout** (screenshot).
- **Positive control** from home: same curls succeed (`:8123` returns CH HTTP 200 on `/`; `:3000` serves Grafana) — proves the block is IP-scoped, not a dead VM.
- Optional: `ssh` from Cloud Shell → refused (timeout).

**Verify:** screenshots of both the Cloud Shell refusals and the home-IP successes land in the implementation-log Phase 5 section alongside the firewall rule list output.

---

### DEMO — Chaos battery (one ~90 min window, after 5C deploy)

Sequential, each injection followed by: observe alert in Slack (`#wikistream`) or email (CM), capture evidence, restore, confirm clear. Record every step with timestamps in the Phase 5 log section.

| # | Injection | Method | Expected | Restore |
| --- | --- | --- | --- | --- |
| 1 | Consumer down | `docker stop wikistream-consumer` (graceful stop — restart policy does NOT auto-restart; Phase 4A proved this) | R1 fires (~2 min) → Slack | `docker start wikistream-consumer`; R1 clears |
| 2 | DLQ-rate spike | sse-fixture transient container (Phase 4A tool) on the compose network feeding ~100% malformed events for ~3 min, reachable by the consumer via a one-off `docker compose run --rm` consumer override with `STREAM_URL=http://<sse-fixture-container>:<port>/stream` (4A N10 pattern: override file outside the repo dir) | R2 fires (rate ≈ 1.0 ≫ 0.05) → Slack | stop sse-fixture; rate decays below threshold; R2 clears |
| 3 | ClickHouse insert failures | `docker stop wikistream-clickhouse` for ~90 s | R1 co-fires (health-signal down — intended), then on restart R3 fires on the accumulated `insert_failed_delta` → Slack | `docker start wikistream-clickhouse`; both clear |
| 4 | Parity drift | `bq query --use_legacy_sql=false "DELETE FROM wikistream.kpi_edits_hourly WHERE DATE(window_start) = CURRENT_DATE()"` — **scoped to what the restore can rebuild** (the export window is the last completed hour; a whole-table DELETE would leave a permanent hole the single re-run can't fill), then `bash /opt/wikistream/warehouse/parity.sh` manually | R4 fires (value 0) → Slack | re-run the export for the deleted window (`.service` once); next parity run clears |
| 5 | GX suite failure | `docker compose run --rm -e GX_ROW_MIN=999999999 gx` (row-count guard trips — **writes `value=0.0` via the guard exit path, so R5 sees it**, see 5A.3) | R5 fires → Slack | nothing (verdict-only alert); next real hourly run clears |
| 6 | Disk almost full | `dd if=/dev/zero of=/mnt/ch-data/dummy bs=1M count=<from df to cross 80% of 30 GB>` | CM disk-almost-full email (after 300 s sustained) | `rm /mnt/ch-data/dummy`; policy clears |
| 7 | VM unreachable | `gcloud compute instances stop wikistream-vm --zone us-east1-b` | CM vm-unreachable email (after 120 s missing uptime) | `gcloud compute instances start …`; clears |
| 8 | Firewall rejects non-whitelisted IP | Cloud Shell `curl` to `:3000`/`:8123` (Google egress IP) + positive control from home | Refused from Cloud Shell; works from home | none (permanent state) |

Evidence pack: Slack screenshots (per alert, with timestamp), alert-rule API states before/after, Cloud Monitoring policy screenshots, firewall rule list, and the positive controls. **Dashboards must keep updating throughout** (AC18) — Grafana's own healthcheck + the panel data prove the battery didn't destabilize the stack.

**Verify:** every row of the battery table has its evidence; `docs/implementation-log.md` Phase 5 section written in the established format (Status, dated lines, Evidence: lines).

---

## 7. Acceptance criteria (self-checkable)

| # | Criterion | How an agent verifies it |
| --- | --- | --- |
| AC1 | Migration 008 creates `pipeline_health`; migrations suite green | `uv run --project consumer pytest -m ch -q tests/migrations/` green; VM: `SHOW CREATE TABLE default.pipeline_health` returns the Phase 5 schema |
| AC2 | Consumer heartbeat writes rows at ~15 s cadence; unit tests green | `uv run --project consumer pytest -m "not ch" -q tests/src/consumer/test_heartbeat.py` green; VM: `SELECT count() FROM pipeline_health WHERE source='consumer' AND ts > now()-INTERVAL 5 MINUTE` ≥ 15 rows |
| AC3 | Parity run writes its verdict to `pipeline_health` | VM: `bash /opt/wikistream/warehouse/parity.sh` then `SELECT value FROM pipeline_health WHERE source='parity' ORDER BY ts DESC LIMIT 1` = 1 |
| AC4 | GX run writes its verdict at every exit path; status write never masks the result | `uv run --project gx pytest -m ch -q tests/gx/test_status_report.py` green (gx venv); VM: forced-fail run (`GX_ROW_MIN=999999999`) exits 1 **AND** writes value 0.0 |
| AC5 | Slack contact point provisioned; test notification delivered | `GET /api/v1/provisioning/contact-points` (basic auth admin) lists `slack-alerts`; Grafana "Test" from that contact point → message in `#wikistream` (screenshot) |
| AC6 | Exactly 5 alert rules provisioned, folder `wikistream` | `curl -u admin:$PASS http://localhost:3000/api/v1/provisioning/alert-rules \| jq length` = 5; each uid in the locked set |
| AC7 | Consumer-down demonstrated | DEMO #1: R1 fires in Slack within ~2 min of `docker stop`, clears on start (evidence) |
| AC8 | DLQ-rate demonstrated | DEMO #2: R2 fires during malformed injection, clears after (evidence) |
| AC9 | CH-insert-failure demonstrated | DEMO #3: R1 co-fires, R3 fires post-restart, both clear (evidence) |
| AC10 | Parity-drift demonstrated | DEMO #4: R4 fires on manual drift, clears after restore (evidence) |
| AC11 | GX-fail demonstrated | DEMO #5: R5 fires on forced row-count failure (evidence) |
| AC12 | Ops Agent active; disk metrics visible | `systemctl is-active google-cloud-ops-agent*`; `gcloud monitoring time-series list --filter='metric.type="agent.googleapis.com/disk/percent_used"'` returns ch-data rows |
| AC13 | Disk-almost-full fires and clears | DEMO #6: email at >80% sustained, clears on `rm` (evidence) |
| AC14 | VM-unreachable fires and clears | DEMO #7: email after stop, clears on start (evidence) |
| AC15 | IAM review complete and accurate | `docs/planning/iam-review.md` committed; matrix rows cross-check a fresh `gcloud projects get-iam-policy` + `bq show` (no phantom bindings) |
| AC16 | Default firewall rules gone; non-whitelisted IP rejected | `gcloud compute firewall-rules list` shows only the 4 custom rules; Cloud Shell `curl` to `:3000`/`:8123` refused (screenshot) while home-IP control succeeds |
| AC17 | Chaos battery 8/8 with evidence | Implementation-log Phase 5 section: all 8 rows have timestamps + screenshots + restore + clear confirmations |
| AC18 | Dashboards still updating after the battery | Grafana dashboard `wikistream-live` renders fresh data (panel timestamps ≤ 1 min old) immediately after DEMO #8; `docker compose ps` all healthy |

---

## 8. Verification gates (master plan wording)

> Phase 5's proof is a deliberate battery of failures, not a configuration listing. Each alert fires under a fault it was designed for, and every alarm clears after its cause is removed.

1. **5A micro-gate (HARD CHECKPOINT before 5B):** migration 008 + heartbeat live in `pipeline_health` (heartbeat rows ≥ 15 per 5 min), parity + gx verdicts landing hourly, 5 rules + `slack-alerts` contact point in the provisioning API, test notification in Slack, and the full pytest suite green (unit + ch). No 5B work starts until the log records these.
2. **5B micro-gate (before 5C):** Ops Agent active and streaming disk/memory; both policies in the CM UI; `terraform apply` clean. 5C starts only after — **the firewall lockdown is deliberately the last step** (master plan §7 IP-lockout risk: once default rules are gone, TCP access to the VM is IP-scoped, so all hands-on work is done first).
3. **DEMO battery:** all 8 injections executed in one window with evidence (AC7–AC14, AC17).
4. **IAM + firewall final verification:** AC15–AC16.
5. **Full acceptance pass:** AC1–AC18 against the evidence in the Phase 5 log section.

Then **Go/No-Go for Phase 5 (master plan §5) — an explicit final step, not a separate doc: "does the system prove observable and defensible, not just configured?"** — to be recorded GO-with-caveat against AC1–AC18 with evidence pointers, or No-Go with a recorded, understood fix per failed AC. Phase 5 is not on the Gate-2 critical path (Gate 2 was Phase 4's); its Go/No-Go is its own gate before Phase 6.

---

## 9. Troubleshooting notes

- **SSH/grafana/clickhouse unreachable after 5C — IP changed:** the allow rules are scoped to `209.35.91.152/32`. From Cloud Shell (console access is not firewall-governed): `gcloud compute firewall-rules update allow-ssh --source-ranges=<new-ip>/32` (and `allow-grafana`, `allow-clickhouse` likewise). This is the documented recovery path, not an emergency.
- **Alert rule does not fire on the VM but fired locally:** provisioning is bind-mounted from the repo — `git pull` + `docker compose restart grafana` (boot.sh now does this every boot; if the VM wasn't rebooted, run it manually). Confirm via `GET /api/v1/provisioning/alert-rules`.
- **`pipeline_health` empty / consumer rows missing:** heartbeat swallows insert errors by design — check the consumer log for the heartbeat's logged write failure, and confirm `wikistream` still has INSERT on `default.*` (boot.sh re-grants every boot).
- **DLQ-rate alert shows 0 during a 100%-malformed demo:** you are running the naive `dead/inserted` formula — this plan's R2 uses `dead/(inserted+dead)` (Q10). Re-check the query model was exported from Explore, not hand-typed.
- **R3 fires after a recovery, not during the outage:** expected — `insert_failed` accumulates only on flush failures and the rule sums the 5-minute delta. Co-firing with R1 during the outage is also expected (the health-signal channel is down = a real failure). Documented semantics, not a bug.
- **CM→Slack not possible:** GCP's "slack" channel type is deprecated (legacy app token) and webhook channels POST an envelope Slack rejects. The plan's split (Grafana→Slack, CM→email) is the zero-function choice; a Cloud Function relay is the stretch if email is unacceptable.
- **`google-ops-agent` install fails on the VM:** the repo script requires `curl` and the Google apt repo reachable — both guaranteed by startup.sh's apt setup. Re-run `bash add-google-cloud-ops-agent-repo.sh --also-install` manually and check `/var/log/google-cloud-ops-agent/`.
- **disk-almost-full filter matches nothing:** the agent may label the device differently (`metric.labels.device` vs `mount_point`). Metrics Explorer at 5B.3 shows the real label — align the policy filter to it before applying.
- **`gcloud secrets versions access latest` fails in boot.sh:** VM SA lacks `secretAccessor` on `slack-webhook-url` (TF binding not applied yet) or the secret has no versions (Ahmed hasn't added the value). Both are 5A.5 steps; the wrapped command logs and continues — alerting still works, only Slack delivery is missing.
- **R4 doesn't fire on a one-off parity error:** parity.sh's freshness-gate and CH/bq-query failure paths `emit_log && exit 1` *before* the pipeline_health write — a transient error is suppressed by the last good 1.0 row; only persistent absence (2.5 h) or a written 0.0 trips R4. Acceptable (absence semantics), but the error remains visible in `/var/log/wikistream/wikistream-parity.log`.

---

## 10. Handoff to Phase 6 (what Phase 6 inherits)

- **Live, proven alerting.** GX failures now page (R5) — the Phase 4 handoff's "Phase 6 enforcement input" is already operational; Phase 6 inherits a pipeline whose quality signal is both instrumented *and* alerted, not just logged to `/var/log/wikistream-gx.log`.
- **`pipeline_health` as the project's health table.** Any future phase (or an interviewer's "how do you know it's working?") queries one table for consumer liveness, DLQ rate, insert failures, parity drift, and data-quality verdicts.
- **A documented IAM baseline** (`iam-review.md`) — every binding justified; Phase 6+ additions are diffed against it.
- **A tightened network surface** — 4 custom rules, no GCP defaults; the IP-lockout recovery path is documented, not feared.
- **Two CM policies + Ops Agent** — disk/VM infra health covered for the rest of the project's life.
- **Open items / known ceilings:**
  - CM→Slack relay (Cloud Function) — only if email is rejected.
  - Alert thresholds (R2 0.05, disk 80%, windows 90 s/5 m/150 m/90 m) are initial values; tune after sustained observation.
  - `pipeline_health` TTL 7 days — raise if historical alerting forensics is wanted.
  - The 5A.4 rule models were exported from Explore — a later Grafana upgrade may need re-export (model drift).
  - Chaos battery is a manual demo window, not a CI job (deliberate).

---

## 11. Interview talking points (why this phase is a resume story)

- **"I didn't trust my alert config — I broke the system on purpose."** The battery: 8 deliberate injections (stop the consumer, poison the stream, kill the database, corrupt the warehouse, force a data-quality failure, fill the disk, pull the VM, probe from a foreign IP). Every alert fired; every alarm cleared; dashboards stayed live throughout. That is the difference between "configured" and "proven."
- **"No Prometheus, on purpose."** Most candidates reach for Prometheus+Grafana as a default pair. The decision here was deliberate and defensible: a push-based `pipeline_health` table in the pipeline's own OLAP store, because pull-scraping cannot observe one-shot timer processes (hourly parity, hourly GX) — and because the "second Prometheus" would be CV noise, not signal.
- **Two non-overlapping monitoring layers** (ADR-010): application health in Grafana (5 rules → Slack), infrastructure health in Cloud Monitoring via the Ops Agent (disk/VM — 2 policies → email). Each layer answers a different question; neither can be substituted for the other.
- **A real security finding, found and closed.** The default GCP firewall rule left SSH open to 0.0.0.0/0 — the review caught it, the lockdown closed it, and the rejection was proven from a foreign IP while the legitimate IP kept working.
- **The observability payload is generated by the system itself** — heartbeat, parity, and GX verdicts written to `pipeline_health` — so alerting has no separate agent deployment to drift out of sync with reality.
- **Every VM change went through the repo** (`boot.sh`), keeping the frozen startup script untouched: zero infrastructure replacement, zero config drift, across an entire observability + security phase.
