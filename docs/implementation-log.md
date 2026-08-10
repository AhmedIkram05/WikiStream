# Implementation Log

The running narrative of what was actually done, per phase and task. The
master plan says what *should* happen; this file records what *did* happen —
including every place they diverge. One file, append-only in spirit (entries
are written once; only a task's **Status** line gets updated later).

## Logging rules — read before writing

### Log these
- **Task status transitions** — started, done, blocked (use the Status format below).
- **Deviations from the master plan or ADRs** — what changed, *why*, and the fallback taken. A deviation without its reason is a bug report without a reproduction.
- **Findings later phases depend on** (e.g., "GitHub Environments: YES — no fallback needed"). If Phase N would otherwise re-discover it, it belongs here.
- **One-time manual steps performed** — bootstrap applies, local spikes, anything done by hand that isn't in CI.
- **Verification / gate results** — exit criteria checked, and how you know (a gate that passed without evidence is a hope).
- **Evidence pointers** — paths into `/evidence` or CI run URLs. Reference, don't duplicate.

### Don't log these
- **Raw tool output, screenshots, binaries** — those go in `/evidence/` during Phase 8's single collection pass (master plan §8); the log links to them.
- **Code changes** — git history is the record of what changed; the log is the record of why.
- **Time / effort tracking** — explicitly declined in master plan §1.
- **Speculation about future work** — pending items get a Status line, not a paragraph.
- **Duplicate of the planning docs** — if it's already in the master plan verbatim, point at it instead.

### Entry format

```
### <task id> — <task name>

**Status:** DONE | IN PROGRESS | BLOCKED | SKIPPED (one of)
**2026-MM-DD:** <what was done, why, any deviation from plan>
**Evidence:** `evidence/phase-N/<file>` or <CI run URL>
```

Multiple dated lines per task are fine. Phase sections are created as each
phase starts; empty phases stay as a heading only.

---

## Phase 0 — Foundations & Risk Burn-Down

### 0.1 Repo directory skeleton

**Status:** DONE
**2026-08-09:** Created structure-only skeleton per master plan Phase 0: `infra/bootstrap` (real content), `infra/main/modules/{network,compute,iam,storage}`, `consumer/src`, `migrations`, `grafana`, `gx`, `tests`, `evidence`. No feature code.
**Evidence:** in-repo.

### 0.2 Terraform state-bucket bootstrap (ADR-007)

**Status:** DONE
**2026-08-09:** `infra/bootstrap` written (local state, `prevent_destroy`, versioning, uniform bucket access) and applied manually. Bucket: `wikistream-505003-terraform-state` (project `wikistream-505003`). One-time step documented in `infra/bootstrap/README.md` so teardown never rediscovers it the hard way.
**DEVIATION (2026-08-09, second apply):** Initial apply ran against the wrong GCP project (`project-7f2bbd9a-c5f0-48d2-b08`, default from gcloud config) — bucket created there. Corrected: `gcloud config set project wikistream-505003`, removed the misplaced resource from local state (`terraform state rm` — `prevent_destroy` otherwise blocked the replacement, which is exactly the guard ADR-007 wants), re-applied in the right project, and deleted the orphaned wrong-project bucket via `gcloud storage rm -r` (it was empty). No other Phase 0 artifacts touched the wrong project.
**Evidence:** `infra/bootstrap/` (config + README); bucket verified via `gcloud storage buckets describe`.

### 0.3 GitHub Environments required-reviewers finding (ADR-008)

**Status:** DONE
**2026-08-09:** Repo is public; `production` environment exists with `required_reviewers` (AhmedIkram05, self-review allowed). **Answer: YES — ADR-008's gate is available; `workflow_dispatch` fallback NOT needed.** Phase 2 must use `environment: production` on the apply job. Optional hardening (non-blocking): deployment branch policy pinned to `main`.
**Evidence:** Finding recorded inline above — no `evidence/` file created (per revised policy, 2026-08-09: verification results live in the log; evidence is collected once, in Phase 8).

### 0.4 GX `[clickhouse]` extra spike (ADR-005)

**Status:** DONE
**2026-08-09:** **Answer: YES — GX-via-SQLAlchemy works against ClickHouse. Fallback NOT adopted.** GX 0.18.22 connected via clickhouse-sqlalchemy 0.2.9 + clickhouse-driver 0.2.11 to ClickHouse 26.3.17 (local container) and ran `expect_table_row_count_to_be_between(1, 100)` against a 3-row table — `success: True`, observed 3. Spike was throwaway (temp dir, container removed).
**Findings that Phase 1/4 must respect:**
- GX's pinned pandas (2.1.4) has no Python 3.14 wheel — source build fails. Use Python 3.12/3.13 for the project venv (3.12 verified).
- ClickHouse 26.3 image's `default` user is **localhost-only** — any connection from another container/network fails auth. Phase 1's docker-compose must create a real user: `CREATE USER ... IDENTIFIED WITH plaintext_password BY '...' HOST ANY` + scoped grants (`GRANT SELECT, INSERT, CREATE, ALTER, DROP, TRUNCATE, OPTIMIZE ON default.*`). Blanket `GRANT ALL` is denied in 26.3.
- Versions verified at implementation start (Vision §9 re-check): ClickHouse 26.3.17, GX 0.18.22, clickhouse-sqlalchemy 0.2.9, SQLAlchemy 1.4.54.

### 0.5 Business-critical modules boundary (ADR-009)

**Status:** DONE
**2026-08-09:** Boundary written and locked: `docs/planning/coverage-boundary.md` — six modules (SSE parser, Pydantic models, batcher, dead-letter routing, migrations/MVs, GX suite) with per-module rationale. Paths are predicted, corrected when Phase 1 lands. Phase 6 references this doc directly for the 100% gate.

---

## Phase 1 — Walking Skeleton, Local

Tasks defined in `docs/planning/phase-1-implementation.md` (LOCKED 2026-08-09).
Status lines are filled as each task is worked, per the logging rules.

**DEVIATION (2026-08-09):** Per Ahmed, no evidence is collected during phases —
the Phase 1 plan carries no `/evidence` capture, screenshots, or README work;
verification numbers land in this log only. All evidence (including the
README) is assembled once, at the end of the project (Phase 8).

### 1.1 — Consumer package (SSE parser + main loop + Dockerfile)

### 1.2 — ClickHouse service + initdb.d DDL

### 1.3 — Grafana provisioning + panel

### 1.4 — Root docker-compose.yml

### 1.5 — Parser unit tests (tests/test_sse.py)

### 1.6 — Bring-up + smoke verification

### 1.7 — Sustained run

### 1.8 — Reproducibility gate (down -v && up)

### 1.9 — Wrap-up (boundary correction, log population)

## Phase 2 — GCP Deployment of the Skeleton

## Phase 3 — Data Model Depth (3A Schema / 3B Analytics)

## Phase 4 — Data Quality & Resilience

## Phase 5 — Observability & Security Hardening

## Phase 6 — Coverage Bar Enforcement

## Phase 7 — Performance & Cost Validation

## Phase 8 — Evidence Capture & Teardown
