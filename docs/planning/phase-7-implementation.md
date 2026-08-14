# Phase 7 Implementation Phase Plan — Performance & Cost Validation

**Status:** LOCKED — 2026-08-14. No edits without a decision record.
**Position in hierarchy:** Research Notes → Vision & ADR → master-plan.md (§5, Phase 7) → this plan.
**Branch:** `feature/Coverage-Bar-&-Cost-Validation` (2 commits ahead of origin).

---

## 1. Objective

Per master plan §5 Phase 7: deliver the three confirmed additions —

1. **7a — Burst/backpressure harness** (vision doc §6): a custom Python asyncio load harness (deliberately NOT k6) that fires synthetic Wikimedia-format events at multiples of the **observed baseline rate**, asserts **zero drops** and **correct dead-letter routing** under burst, and reports the **peak burst rate sustained with zero drops**.
2. **7b — ClickHouse latency benchmark**: raw-table scan vs. MV-precomputed query on the **real accumulated dataset** (50,239,373 rows in `default.raw_events` on the VM), reporting **p50/p99 latency and rows scanned** per query, for the two canonical dashboard query families (edit velocity, top pages).
3. **7b — Cost/FinOps note**: GCP inventory (VM, disks, AR, GCS, SM, BigQuery) × documented public rates, projected 24/7 monthly run-rate, compared against the $300 trial credit, with teardown guidance for Phase 8.

Exit bar: burst test demonstrates zero drops at a tested multiple of baseline rate; benchmark numbers come from the real dataset (not synthetic); the cost note is written from the real inventory with documented rates.

## 2. Baseline facts (measured from the VM, 2026-08-14)

| Fact | Value | Source |
| --- | --- | --- |
| Accumulation window rows | 50,239,373 (raw_events all-time) | `SELECT count()` |
| Events in last 24h | 48,863,175 | `WHERE inserted_at >= now()-24h` |
| **Observed baseline rate (24h avg)** | **565.5 ev/s** (48,863,175 / 86,399 s) | computed |
| Real observed peak minute | 163,125 ev/min = **2,719 ev/s** (2026-08-13 15:16) | `GROUP BY toStartOfMinute ORDER BY count() DESC` |
| MV coverage | mv_edits_per_minute: 248,167 rows, minutes 2026-08-12 14:52 → 2026-08-14 13:35 (≈46h) | min/max(minute) |
| Data cutoff | 2026-08-14 13:38Z (consumer run ended; accumulation complete) | raw max(inserted_at), state file |
| DL histogram | invalid_json 18,344 (legacy 3A junk), timestamp_missing 405, validation:missing 22, timestamp_unparseable 4 | `GROUP BY reason` |

**Burst levels** (multiples of observed baseline 565.5 ev/s, 60 s sustained each):

| Level | Rate (ev/s) | Events fed (60 s) | Note |
| --- | --- | --- | --- |
| 2× | 1,131 | 67,860 | routine headroom |
| 5× | 2,828 | 169,680 | ≈ the real observed peak minute (2,719 ev/s) |
| 10× | 5,655 | 339,300 | 10× baseline sustained |

## 3. Locked decisions

| # | Decision | Rationale |
| --- | --- | --- |
| Q1 | 7a runs **locally** against the local compose ClickHouse; the VM consumer is not disturbed | master-plan §8: burst test is a re-runnable demonstration; local run is acceptable evidence. VM holds the real dataset for 7b only |
| Q2 | Harness is a standalone stdlib+asyncio script `scripts/burst_test.py` with its own high-rate SSE feeder (bulk writes, lazy generation) — not a reuse of `tests/sse_fixture.py` | Vision doc §6 mandates a custom harness; SSEFixture's per-event `drain()` bottlenecks above ~400 ev/s; a static events list for 339k events is wasteful. The feeder mirrors the fixture's wire framing (`id:`/`data:`/blank line) so the harness exercises the real consumer code path unchanged |
| Q3 | Burst assertions (per level, after graceful SIGTERM + final flush + async-insert settle): `raw_events_delta + dead_letter_delta == events_sent` (zero drops), `dead_letter delta per reason == injected per reason` (correct DL routing), `consumer_state.json total == events_sent` (durable cursor), feeder achieved rate ≥ 0.95 × target (harness is not the bottleneck) | At-most-once batcher means any insert failure shows as a drop; per-reason DL counts prove routing, not just volume |
| Q4 | Malformed injection: 1% of fed events at every level — 0.4% truncated JSON → `validation:invalid_json`, 0.3% bad timestamp string → `timestamp_unparseable`, 0.3% missing required field (no `title`) → `validation:missing` | Reasons match consumer.py lines 220/235-244/252-258 exactly (verified in code); 1% keeps DL writes non-trivial without skewing the zero-drop math |
| Q5 | 7b runs against the **VM** ClickHouse (real 50.2M-row dataset) over the HTTP API with the Secret Manager password; 24h window (fully covered: raw rows 48.86M, MV minutes back 46h) | Benchmark contract: real dataset only |
| Q6 | Benchmark query pairs = the canonical dashboard families: (Q1) edit velocity per minute, (Q2) top-10 pages — each raw-scan vs MV form; 1 warmup + 20 timed runs each; per-run server-side `query_duration_ms` + `read_rows`/`read_bytes` from `system.query_log` (matched by a unique trailing comment marker); report p50/p99 + rows scanned | query_log is the ground truth (server-side, includes rows scanned); dashboard queries are the thing Phase 5 promised to accelerate |
| Q7 | Cost note = real inventory (gcloud list for instances/disks/AR/GCS/SM/BigQuery) × documented public rates, itemized monthly run-rate, vs $300 trial credit, teardown list for Phase 8; lives as a section in this doc (7.3) | FinOps note is a deliverable, not a new doc tree |
| Q8 | Findings honesty: any measured ceiling below the hoped multiple is recorded as a finding (master-plan gate), not fixed by tuning the harness | The gate says "if burst finds a real ceiling lower than hoped, document it" |

## 4. Prerequisites

- [x] Accumulation window complete: 50,239,373 real rows (verified 2026-08-14).
- [x] Baseline measured: 565.5 ev/s avg; real peak 2,719 ev/s (above, §2).
- [x] VM CH reachable from this host: `curl -G -u wikistream:$CH_PW http://34.148.138.220:8123/` (firewall allows 209.35.91.152/32).
- [x] Local compose CH + Grafana up; local consumer **stopped** (Phase 6 contamination discipline).
- [ ] Local CH migrated: `./migrations/apply.sh` (CH_PASSWORD=wikistream_dev_password) — local database was dropped by test resets.
- [ ] VM consumer idle state recorded (accumulation complete) — do not restart it during 7a/7b runs.

## 5. Target file structure

```
scripts/burst_test.py            # NEW — 7a asyncio burst harness (stdlib + HTTP API)
scripts/benchmark.py             # NEW — 7b raw-vs-MV benchmark (stdlib + HTTP API)
docs/planning/phase-7-implementation.md  # THIS plan (+ 7.3 cost note section)
docs/implementation-log.md       # EDIT — Phase 7 entries 7.1–7.4 under the existing header
```

No production code changes: the harness/benchmark exercise the shipped consumer and migrations exactly as deployed.

## 6. Tasks

| # | Task | Status |
| --- | --- | --- |
| 7.1 | `scripts/burst_test.py`: asyncio SSE feeder (bulk-write framing, lazy event generation from a real-shape template, per-level unique ids), spawns the real consumer (`uv run --project consumer python -m src.consumer`) with `STREAM_URL` pointing at the feeder and a fresh temp `STATE_DIR`, feeds `rate × 60 s` with 1% malformed injection, SIGTERM, waits for final flush + async-insert settle (poll until stable 3×), queries CH deltas (raw + DL per reason) via HTTP API, asserts Q3 invariants, prints PASS/FAIL table | |
| 7.2 | Run the burst matrix locally: apply local migrations, run levels 2× / 5× / 10×, record per-level numbers + peak-sustained-zero-drop finding | |
| 7.3 | `scripts/benchmark.py`: Q1/Q2 raw vs MV, 1 warmup + 20 timed runs against the VM, per-run query_log match (marker comment), p50/p99 duration + mean rows/bytes scanned, MV-vs-raw speedup + scan-reduction summary | |
| 7.4 | Cost/FinOps note: gcloud inventory (instances, disks, AR, GCS, SM, BigQuery) × public rates → monthly run-rate vs $300 credit; Phase 8 teardown list | |
| 7.5 | Log entries 7.1–7.4 (Status/date/Evidence per format), final Go/No-Go with evidence pointers | |

## 7. Acceptance criteria

| # | Criterion | How an agent verifies it |
| --- | --- | --- |
| AC1 | Burst harness exists, runs locally, exercises the real consumer binary path (no consumer code edits) | `uv run --project consumer python scripts/burst_test.py --baseline 565.5 --multiples 2,5,10 --duration 60` against local CH → exit 0; script prints per-level sent/stored/DL/state numbers |
| AC2 | Zero drops at every tested multiple | For each level: `raw_delta + dl_delta == sent` and `state.total == sent`, printed PASS |
| AC3 | Correct DL routing under burst | Per-reason DL deltas equal injected counts exactly (`invalid_json`/`timestamp_unparseable`/`validation:missing`) |
| AC4 | Peak burst rate sustained with zero drops recorded | 10× level (5,655 ev/s × 60 s = 339,300 events, zero drops) in log; feeder achieved ≥ 0.95 × target |
| AC5 | Benchmark against the real dataset | `scripts/benchmark.py` (env CH_HOST=34.148.138.220) prints p50/p99 + rows scanned for raw and MV forms of Q1/Q2, MV speedup + scan reduction; numbers recorded in log |
| AC6 | Cost note written from real inventory with documented rates | §7.3: inventory table + itemized monthly total + vs $300 credit + teardown list |
| AC7 | Log written | implementation-log.md Phase 7 entries 7.1–7.4 with Status/date/Evidence, Go/No-Go recorded |

## 8. Verification gates

- Gate A: AC1–AC3 (burst run green locally).
- Gate B: AC4 (10× zero-drop recorded).
- Gate C: AC5 (benchmark on VM, numbers recorded).
- Gate D: AC6–AC7 (cost note + log).

**Go/No-Go (final step):** recorded in the log against AC1–AC7 with evidence pointers. Any ceiling finding (e.g. drop above some level) is documented as a finding per master-plan §5, not papered over.

## 9. Troubleshooting notes

- If the feeder can't sustain target rate (achieved < 0.95 × target): widen bulk-write chunking (events per tick, tick interval), not the consumer — the harness must not be the bottleneck.
- Async inserts settle asynchronously: after consumer exit, poll CH counts until 3 consecutive reads are equal (30 s cap); `async_insert_busy_timeout_ms` (200 ms default) bounds the delay.
- CH HTTP API + `urllib` keeps both scripts stdlib-only; Basic auth header `Authorization: Basic base64(user:pass)`; never print the password (Secret Manager discipline — env var only, unset after).
- Per-level fresh `STATE_DIR` isolates cursors; unique SSE ids per level avoid dedup-ring cross-talk (ring capacity 50,000 < level sizes — irrelevant with unique ids, but ids must never repeat).
- Consumer exit code must be 0 (graceful SIGTERM → final flush → save_state); non-zero exit = failed run, not a drop result.
- Benchmark window must stay inside MV coverage (max 46h back, ends 13:35Z) — 24h window is safe; re-verify `max(minute)` before running.
- Marker comments inside SQL text are required for query_log matching (`/* BENCH-... */` trailing) — they appear verbatim in `system.query_log.query`.

## 10. Handoff to Phase 8 (Evidence Capture & Teardown)

Phase 8 inherits: this plan's results table, the implementation-log entries 7.1–7.4, the cost note teardown list (what to delete on the VM/GCP to stop spend), and the benchmark numbers as the baseline evidence set. The VM consumer stays idle (accumulation complete) — Phase 8 decides VM lifecycle.

---

## 11. Cost/FinOps note (task 7.3)

Written 2026-08-14 from live inventory (`gcloud` / `gsutil` / `bq` against project `wikistream-505003`) × public list prices (us-east1). Billing account: `0161E9-ED239E-CB2278` ("My Billing Account"). No billing export exists — the note is a projected run-rate, not a spend statement.

### 11.1 Inventory and itemized monthly run-rate

| Resource | Detail (from inventory) | Unit rate | Monthly (730 h) |
| --- | --- | --- | --- |
| Compute | `wikistream-vm` e2-medium, RUNNING since 2026-08-13, us-east1-b | $0.0359/h | $26.21 |
| Disk | `wikistream-vm` 50 GB pd-standard (boot) | $0.10/GB/mo | $5.00 |
| Disk | `ch-data` 50 GB pd-standard | $0.10/GB/mo | $5.00 |
| Static IP | `wikistream-ip` 34.148.138.220, regional us-east1 | $0.005/h | $3.65 |
| GCS | `wikistream-505003-backups` 46.4 GB STANDARD (lifecycle: delete age 2 d) | $0.020/GB/mo | $0.93 |
| GCS | `wikistream-505003-bq-staging` 8.8 GB STANDARD (lifecycle: delete age 7 d) | $0.020/GB/mo | $0.18 |
| GCS | `wikistream-505003-terraform-state` 104 KB | $0.020/GB/mo | ~$0.00 |
| Artifact Registry | `wikistream-consumer` (DOCKER, no size reported) | storage+network | ~$0.00 |
| Secret Manager | 3 secrets (clickhouse-password, grafana-admin-password, slack-webhook-url) | $0.06/secret/mo | $0.18 |
| BigQuery | dataset `wikistream` (5 tables, 0.0 GB reported; hourly KPI exports + parity queries) | storage ~0, query fractions of a cent/day | <$0.50 |
| Cloud Monitoring | alert policies, uptime checks (free tier covers this scale) | $0 | $0.00 |
| **Total** | | | **≈ $41.65** |

### 11.2 vs the $300 trial credit

- At the projected $41.65/mo steady-state run-rate, the $300 trial credit covers ≈ **7.2 months** of continuous operation — the 90-day trial window is comfortably inside that (~$125 worst case for the full 3 months).
- Actual consumption is far lower than projected: the VM has existed since 2026-08-13 and Phase 8 tears the environment down. The two disks, the VM, and the static IP are 93% of the run-rate ($39.86); every one of them is on the Phase 8 teardown list.
- After teardown the residual footprint is ~$1.79/mo (GCS backup/staging buckets until their lifecycles clear, Secret Manager, BigQuery dataset, Artifact Registry) — consistent with the vision doc's "no ongoing spend expected after teardown" constraint.

### 11.3 Phase 8 teardown list (what stops the spend)

1. `terraform destroy` on `infra/main` — removes VM, both disks, static IP, firewall rules, monitoring policy, AR repo (bootstrap bucket `wikistream-505003-terraform-state` excluded by design, ADR-007).
2. `wikistream-505003-backups`: take the final Phase 8 backup first, then let the 2-day lifecycle clear it (or delete outright).
3. `wikistream-505003-bq-staging`: 7-day lifecycle clears it; delete outright if desired.
4. Secret Manager: delete the 3 secrets (or keep at $0.18/mo).
5. BigQuery dataset `wikistream`: delete after Phase 8 evidence is captured.
6. `wikistream-505003-terraform-state`: keep (holds bootstrap state); delete manually only after no rebuild will ever happen.
