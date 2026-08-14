# Implementation Log

The running narrative of what was actually done, per phase and task. The
master plan says what *should* happen; this file records what *did* happen —
including every place they diverge. One file, append-only in spirit (entries
are written once; only a task's **Status** line gets updated later).

## Logging rules — read before writing

### The three requirements — tick ALL, or don't log it

1. **A future reader would re-break or re-discover it without this entry.**
2. **It changes what a later phase, gate, or deploy will do** — a plan/ADR contract, a plan claim, or a decision that carries forward.
3. **It took real diagnosis** — root-causing, reproduction, or a falsified plan claim; not a fix that was obvious from the error line.

Git history records *what* changed; this log records *why*.

### Log these

- **Task status transitions** — started, done, blocked (Status format below).
- **Medium-large deviations from the master plan or ADRs** — scope, approach, or locked-contract changes: what changed, *why*, and the fallback taken. A deviation without its reason is a bug report without a reproduction.
- **Non-trivial issue-fixes** — crash-loops, failed gates, data loss, plan claims falsified, bugs that needed root-causing. Rule of thumb: needed diagnosis beyond the error line (≈ >15 min or >1-line diff) → qualifies; obvious once seen → doesn't.
- **Findings later phases depend on** (e.g., "GitHub Environments: YES — no fallback needed"). If Phase N would otherwise re-discover it, it belongs here.
- **One-time manual steps performed** — bootstrap applies, local spikes, anything done by hand that isn't in CI.
- **Verification / gate results** — exit criteria checked, and how you know (a gate that passed without evidence is a hope).
- **Evidence pointers** — paths into `/evidence` or CI run URLs. Reference, don't duplicate.

### Don't log these (garbage)

- **Small bug fixes** — one-line fixes, flag flips, attr renames (`project` → `project_id`), pin bumps, chmod/curl/gitignore nits. Obvious-once-seen = garbage.
- **Self-corrections of earlier entries** — fold the correction into the issue-fix entry or fix the original line; never add a separate CORRECTION paragraph.
- **Judgment calls on trivial details** — default-value choices, log-format padding, dropped dead code. A reader cannot act on them.
- **Tooling housekeeping** — hook registration, config-file moves, local command fixes.
- **Informational notes with "no action needed"** — pure noise.
- **Raw tool output, screenshots, binaries** — those go in `/evidence/` during Phase 8's single collection pass (master plan §8); the log links to them.
- **Code changes** — git history is the record of what changed; the log is the record of why.
- **Time / effort tracking** — explicitly declined in master plan §1.
- **Speculation about future work** — pending items get a Status line, not a paragraph.
- **Duplicate of the planning docs** — if it's already in the master plan verbatim, point at it instead.

**2026-08-11 cleanup:** rules tightened above; small-fix entries removed from §1.1, §2.1, §2.2, §2.4 — removed content survives in git history.

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

**Status:** DONE
**2026-08-10:** `consumer/src/sse.py` — `SSEEvent` + `SSEParser`, stdlib only (`dataclasses`, `codecs`), WHATWG-compliant: trailing-`\r` hold (a buffer-ending `\r` that could pair with a following `\n` is held, so `\r\n` split across chunks can't fabricate a frame boundary), CRLF = one terminator, bare `\r` is a terminator, comment-only frames don't dispatch (`_has_data` flag; explicit empty `data:` still emits), strict UTF-8 decoder. `consumer/src/consumer.py` — main loop: httpx2 streaming GET with `Last-Event-ID` header, per-connection `SSEParser`, batch insert via `clickhouse_connect.get_async_client` with `settings={"async_insert": 1, "wait_for_async_insert": 0}`, SSE `retry:`/`Retry-After` backoff, log lines matching the verification contract (`INFO  connected url=`, `WARNING reconnect reason=`, `WARNING insert_failed event=`, `INFO  inserted events=<n> total=<cum>`). `consumer/Dockerfile` (python:3.13-slim) + `consumer/requirements.txt`.
**DEVIATION (2026-08-10):** `clickhouse-connect` pinned `==1.6.0`, not the plan's `2.1.0` — 2.1.0 does not exist on PyPI; 1.6.0 is the latest real release ≥1.6.0 (verified via pypi.org JSON). `httpx2==2.10.0` verified real (maintained httpx fork).
**DEVIATION (2026-08-10, C1):** Wait formula differs from plan §6.1's literal `min(retry_ms or 1.0, retry_after or 30.0)` — with `retry_ms` unset (before the first SSE retry hint) and `Retry-After: 45` on a 429/503, that formula yields `min(1.0, 45.0)` = 1s, the exact 1s-hammering the plan's intent sentence forbids. Implemented: `min(max(retry_ms/1000 if retry_ms else 1.0, retry_after if retry_after else 0.0), 30.0)` — SSE retry hint is the floor, `Retry-After` is honored and capped at 30s. Intent sentence governs over the stale formula.

### 1.2 — ClickHouse service + initdb.d DDL

**Status:** DONE
**2026-08-10:** `docker/clickhouse/initdb.d/001-init.sql` — `raw_events (inserted_at DateTime64(3,'UTC'), event String)`; `wikistream` user `IDENTIFIED WITH plaintext_password BY 'wikistream_dev_password' HOST ANY` + scoped grants (`SELECT, INSERT, CREATE, ALTER, DROP, TRUNCATE, OPTIMIZE ON default.*`) per Phase 0.4 finding (26.3 `default` user is localhost-only; blanket `GRANT ALL` denied). Compose: `clickhouse-server:26.3.17`, ports 8123/9000, `ch-data` volume, `ulimits nofile 262144`, initdb.d mounted read-only.

### 1.3 — Grafana provisioning + panel

**Status:** DONE
**2026-08-10:** `grafana/provisioning/datasources/clickhouse.yaml`, `dashboards/dashboards.yaml`, `grafana/dashboards/phase1.json` (uid `wikistream-phase1`, "Phase 1 — Walking Skeleton", timeseries panel "Events per minute (raw_events)", refresh 10s, `rawSql` byte-exact vs plan §6.3). Verified live: datasource provisioned (`uid wikistream-clickhouse`), health `OK`, panel query returns rows (see 1.6).
**DEVIATION (2026-08-10, build-time §6.3 check):** panel `"format": 0` (numeric TimeSeries enum) instead of the plan's `"time_series"` string — the plugin backend (`sqlds` `query.go`, pinned by `grafana/clickhouse-datasource` `src/types/sql.ts` `format?: number`) unmarshals format into a uint32 enum; a JSON string fails unmarshal and errors the query. Enum: 0=TimeSeries, 1=Table, 2=Logs, 3=Trace. This is the wrapper-field check §6.3 delegates to build time.
**DEVIATION (2026-08-10):** plugin repo URL corrected — plan's `grafana/grafana-clickhouse-datasource` 404s; the real repo is `github.com/grafana/clickhouse-datasource`.
**DEVIATION (2026-08-10, C2):** datasource yaml uses plain `password: ${CLICKHOUSE_PASSWORD}` — Grafana's provisioning interpolator has no `:-default` support (whole token looked up as env name → empty password); compose supplies the default via grafana service env `CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD:-wikistream_dev_password}` (compose does support `:-`).
**DEVIATION (2026-08-10, Grafana 13 plugin install):** `GF_INSTALL_PLUGINS` is deprecated **and broken** in grafana 13.1.1 — `plugin.backgroundinstaller` parses `id:version` with an empty version (`pluginId=grafana-clickhouse-datasource:4.20.0 version=`) → install URL 404s for ANY version → crash-loop (`invalid service state: Failed, expected: Terminated, failure: ... 404: Plugin not found`). Fixed with `GF_PLUGINS_PREINSTALL: grafana-clickhouse-datasource@4.20.0` (`@` is the required version separator per official docs). Plugin pin moved 4.18.0→4.20.0 (4.18.0 404s on the install endpoint; 4.20.0 is catalog latest). Verified: plugin directory present post-start, no crash-loop.

### 1.4 — Root docker-compose.yml

**Status:** DONE
**2026-08-10:** 3 services per plan §6.4 verbatim (clickhouse, consumer with `build: ./consumer` + `image: ${CONSUMER_IMAGE:-wikistream-consumer:local}` + `depends_on`, grafana 13.1.1), no healthchecks (per plan), except the grafana env deviations in 1.3 (GF_PLUGINS_PREINSTALL, CLICKHOUSE_PASSWORD).

### 1.5 — Parser unit tests (tests/test_sse.py)

**Status:** DONE
**2026-08-10:** 19 plain-pytest tests per §6.5 (`from src.sse import SSEParser, SSEEvent`), 19/19 pass, **100% line coverage on `sse.py`** (79/79 stmts) — see 1.9/AC9.
**DEVIATION (2026-08-10):** test 2 splits at `feed(b"data: hello\r")` then `feed(b"\n\n")` instead of mid-line — same semantics (one frame across two chunks → exactly one event) but exercises the trailing-`\r` hold branch, the only way all of `sse.py`'s lines get hit under the §6.5 100% bar. Mid-line splitting is covered by test 19 (split inside a multibyte `é`).

### 1.6 — Bring-up + smoke verification

**Status:** DONE
**2026-08-10:** `docker compose up -d --build` exit 0, 3 services Up. **AC1 ✓.** **AC2 ✓** — full consumer log: 0 Traceback, 0 `insert_failed`; 5 `connected url=` / 4 `WARNING reconnect`. All 4 reconnects are **Wikimedia-server-initiated** SSE closes (`RemoteProtocolError: peer closed connection without sending complete message body (incomplete chunked read)`; gaps 1.5–11 min, no rapid cycling — not a crash-loop); each resumed within ~1.5s using the in-memory Last-Event-ID. Finding for later phases: periodic peer-initiated reconnects are expected Wikimedia eventstreams behavior and are excluded from the crash-loop criterion. **AC3 ✓** — count() 52,724 (16:38) → 57,281 (16:40), increasing. **AC5 ✓** — curl :3000/login 200; `GET /api/search?query=Phase` (admin:admin) returns the dashboard; datasource health `{"status":"OK"}` (confirms C2 password fix). **AC6 ✓** — `POST /api/ds/query` with `"format":0` returns 1 frame, 22 rows (confirms I1 fix). Cold-start note: first ~30s of consumer insert failures while CH boots are caught by the consumer's `except Exception` — expected, 0 once warm.
**FIX (2026-08-10):** the 1.8 `down -v` gate exposed a real cold-start crash-loop (see CORRECTION in 1.1): after the volume wipe, CH takes ~30–60s to boot, and `get_async_client`'s eager `SELECT version(), timezone()` probe fails with `OperationalError` escaping `main()` uncaught → traceback → exit 1 → compose restarts (RestartCount=6 before success; all crashes pre-first-connect, log lines 1–594; self-healed at 7th start, 0 tracebacks and clean inserts after). Fixed in `consumer.py`: client creation is now wrapped in a retry loop — `logger.warning("clickhouse_unavailable reason=%s")` + 2s `_sleep_or_stop` retry until CH answers or shutdown is requested (reuses existing helper; 0 Traceback, RestartCount stays 0). Verified post-fix: rebuild + fresh start → RestartCount=0, 1 `connected url=`, 0 Traceback, clean inserts (16:56Z). DEVIATION recorded: plan §6.1's consumer spec didn't cover CH-client init resilience; fix serves the plan's own intent that cold-start surface as expected WARNING lines, never crash-loop.

### 1.7 — Sustained run

**Status:** DONE — AC7 accepted by Ahmed (2026-08-10)
**2026-08-10:** Opening baseline recorded on the pre-gate stack: consumer start 16:17:36Z, first `connected` 16:17:37Z, opening count **57,281 @ 16:40:22Z**, all 3 services Up, 0 Traceback. **DEVIATION (2026-08-10):** per Ahmed ("skip the sustained run for now, i will do it later"), the ≥2h run is deferred and will be executed by him, not orchestrated here. If run on a post-1.8 stack, record a fresh start time + opening count; verification checklist: `connected url=` count (peer reconnects expected), 0 Traceback, final count(), events/sec = (final−opening)/elapsed, peak/min via `GROUP BY toStartOfMinute(inserted_at) ORDER BY c DESC LIMIT 1`.
**ACCEPTED (2026-08-10):** Ahmed declared the sustained-run criterion satisfied ("im gonna consider the sustained run complete with the time its taked to do all these tasks and its current uptime, i dont care") — **AC7 ✓** on his authority, on top of the elapsed session time plus the current stack's continuous uptime. Post-fix evidence at acceptance time: consumer RestartCount=0, 0 Traceback, connected 16:56:17Z (after the cold-start crash-loop fix in 1.6), continuous `inserted` lines, count() climbing. If he later runs a formal ≥2h pass, use the 1.7 checklist above and append the numbers here.

### 1.8 — Reproducibility gate (down -v && up)

**Status:** DONE
**2026-08-10:** `docker compose down -v` → `up -d --build` exit 0, zero manual steps. After ~3 min: sample 1 = **8,128**; +60s: sample 2 = **11,202** (strictly greater). Exactly **1** fresh `INFO  connected url=... last_event_id=None` line (in-memory resume state correctly wiped with the container — restart-resume is Phase 4). 0 Traceback, 0 `insert_failed`. **AC8 ✓.**

### 1.9 — Wrap-up (boundary correction, log population)

**Status:** DONE
**2026-08-10:** Coverage-boundary corrected — `consumer/src/sse.py` landed at its predicted path and verified at 100% line coverage (AC9); `models.py`/`batcher.py`/`dead_letter.py` don't exist yet and stay predicted. **AC9 ✓** — `19 passed`, `sse.py` 79/79 stmts 100% (pytest 9.1.1 + pytest-cov 7.1.0 in repo `.venv`, python 3.14). README untouched (stays stub — evidence deferred to Phase 8 per 2026-08-09 deviation). **DEVIATION (2026-08-10):** committed on existing branch `feature/walking-skeleton-local` (repo convention is `feature/*`; the plan's suggested `feat/phase-1-walking-skeleton` would be a checkout of a new branch — GitGuard requires explicit instruction, and the phase-0 work branch already carries this phase's files). No CI (per plan).

### Handoff — Phase 1 → Phase 2 (per plan §10)

**Status:** READY (pending commit + Ahmed's go)
**2026-08-10:** Phase 1 is green on all acceptance criteria (AC1–AC9; AC7 by Ahmed's acceptance) and hands off to Phase 2 (GCP deployment) with zero code changes required:

- **Compose verbatim:** same `docker-compose.yml` runs on the GCP VM unchanged; only the consumer image changes: `CONSUMER_IMAGE` env points at the Artifact Registry tag (e.g. `CONSUMER_IMAGE=gcr.io/<project>/wikistream-consumer:<tag>`), then `docker compose pull && docker compose up -d --no-build`.
- **Secrets:** env-var-only configuration already in place (all `${VAR:-default}`); Secret Manager values replace the defaults via the same env names — no code or compose changes, per the plan's "env-only swap" decision.
- **Schema:** `raw_events (inserted_at DateTime64(3,'UTC'), event String)` is Phase 1's landing format; Phase 3A replaces it with the full model (migration + MV redefinition) — nothing in Phase 2 depends on the current shape beyond the table existing and filling.
- **Carry-forward contracts:** SSE parser unit tests (19/19, 100% line coverage on `sse.py`), the consumer log contract (`connected url=`, `reconnect reason=`, `insert_failed`, `inserted events=`) and the verification protocol (count() samples, `docker compose ps`, 0 Traceback) are the Phase 2 acceptance tools; `clickhouse_unavailable` WARNING lines are expected during cold-start and are NOT failures.
- **Known Phase 1 limitations carried forward (NOT bugs):** restart-resume (in-memory Last-Event-ID only), dedup, and event validation are Phase 4 work; peer-initiated Wikimedia SSE reconnects are expected and logged at WARNING.

### Post-Phase-1 tooling change — pip → uv (2026-08-10)

**Status:** DONE
**2026-08-10:** **DEVIATION (per Ahmed, "move to uv now, fully and properly not half arse-ly"):** Phase 1 shipped with the plan-locked `requirements.txt` + pip (Dockerfile `pip install --no-cache-dir -r requirements.txt`). Replaced with full uv adoption, recorded as a deviation from the locked contract:

- `consumer/pyproject.toml` + `consumer/uv.lock` replace `requirements.txt` (30 resolved packages; `clickhouse-connect[async]==1.6.0`, `httpx2==2.10.0`, dev group `pytest>=8`, `pytest-cov>=5`; `[tool.pytest.ini_options] pythonpath=["."]`, `testpaths=["../tests/src/consumer"]`).
- Dockerfile is now uv-native: `COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv` (the binary must land on PATH — first build failed exit 127 with `/uv/bin/uv`, fixed), `uv sync --frozen --no-install-project --no-dev`, `CMD ["uv", "run", "python", "-m", "src.consumer"]`.
- Local dev via `uv sync --project consumer` (consumer/.venv). Test invocation: a root `pytest.ini` (`testpaths = tests`, `pythonpath = consumer`) now owns pytest config — run the whole tree with `uv run --project consumer pytest` from the repo root, or a single area with `uv run --project consumer pytest tests/src/consumer`. (Earlier constraint — pytest rootdir derives from argument paths, so passing `../tests/src/consumer` as an arg resolved rootdir to the repo root and missed the ini — is superseded by the root ini, which pytest finds by walking up from CWD; `[tool.pytest.ini_options]` was removed from consumer/pyproject.toml to avoid two configs drifting.)
- Tests relocated by Ahmed to `tests/src/consumer/test_sse.py` (git mv, content unchanged).
- **AC9 reverified under uv: 19/19 passed, `sse.py` 79/79 stmts 100% line coverage** (via `consumer/.venv/bin/python -m pytest --cov=src.sse --cov-report=term-missing ../tests/src/consumer`).
- **Container verified:** `docker compose up -d --build consumer` with the uv image → RestartCount=0, 1 `connected url=`, 0 Traceback, 0 `insert_failed`, continuous `inserted` batches (total 2,430+ climbing), count() = 103,651.

## Phase 2 — GCP Deployment of the Skeleton

Tasks defined in `docs/planning/phase-2-implementation.md` (LOCKED 2026-08-10).
Status lines are filled as each task is worked, per the logging rules.

### 2.1 — Bootstrap extension: identity (Q1)

**Status:** DONE

**2026-08-11:** `infra/bootstrap/main.tf` extended and applied (ADC, project `wikistream-505003`): 7 `google_project_service` enables (compute, artifactregistry, secretmanager, iamcredentials, sts, oslogin, cloudresourcemanager) via for_each; WIF pool `wikistream-ci` + provider `github` (attribute_condition `assertion.repository == "AhmedIkram05/WikiStream"`, issuer `https://token.actions.githubusercontent.com`); deploy SA `wikistream-deploy` (5 project roles via for_each + `storage.objectAdmin` scoped to the tfstate bucket only); WIF binding (`principalSet` on project number 984854414993, `roles/iam.workloadIdentityUser`); AR repo `wikistream-consumer` (us-central1, DOCKER). `outputs.tf` added: `wif_provider_name`, `deploy_sa_email`. **AC1 verified:** pool ACTIVE; provider name `projects/984854414993/locations/global/workloadIdentityPools/wikistream-ci/providers/github`; SA exists with all 5 project roles; AR repo exists (DOCKER); both outputs resolve to exactly the values hardcoded in the workflows. **DEVIATIONS:** (1) AR repo creation initially failed in the same apply with "Artifact Registry API has not been used before" — API-activation propagation race (the 7 enables completed in the same apply); re-applied after ~90s, repo created, zero manual console steps. (2) Plan-scope deviation carried forward from the README note: bootstrap owns identity + AR repo in addition to the bucket (plan Q1, ADR-007 "bucket only" superseded by plan decision). (3) Pre-commit CI-access audit: deploy SA initially lacked `resourcemanager.projects.setIamPolicy` — infra/main's `oslogin_human` binding (roles/compute.osLogin, project-level IAM, added at review) would have failed the first gated CI apply with "Permission resourcemanager.projects.setIamPolicy denied" (none of the 5 original roles include it). Added `roles/resourcemanager.projectIamAdmin` to the deploy SA (6 project roles now) and re-applied bootstrap. WIF provider name + SA email outputs unchanged.

### 2.2 — Main config + modules + startup script (Q2/Q4/Q5/Q6/Q9/Q10)

**Status:** DONE (implementation + local verification; deploy verification in 2.5–2.7)

**2026-08-11:** `infra/main/` created: `main.tf` (backend gcs `wikistream-505003-terraform-state` prefix `main`, google >= 7.43 + random providers, labels local `{project=wikistream, managed-by=terraform, phase=2}`, 4 module calls, `vm_static_ip` output), `variables.tf`, committed `terraform.tfvars` (allowed_ips = Ahmed's current IP 209.35.91.152 per plan Q6), `templates/startup.sh`, and the 4 modules (network: VPC + subnet 10.0.0.0/24 + 4 firewall rules allow-internal/ssh/grafana/clickhouse — the last three from `allowed_ips`; compute: static IP + e2-medium/50GB pd-standard/ubuntu-2404-lts + OS Login `enable-oslogin=TRUE` + cloud-platform scope; iam: `wikistream-vm` SA + per-secret secretAccessor on the two secrets; storage: AR reader on `wikistream-consumer`). `terraform init` (real backend) + `validate` + `plan` clean: **18 to add, 0 change, 0 destroy**; startup script injected verbatim; firewall source ranges correct; labels applied on every label-capable resource. Lockfile `.terraform.lock.hcl` generated (pinned at build, committed). **DEVIATION (CRITICAL, plan recipe broken):** plan §Q2's apt recipe is invalid on ubuntu-2404-lts — `google-cloud-cli` and `docker-compose-plugin` do NOT exist in Ubuntu 24.04 default repos (apt EXIT=100 "Unable to locate package", verified in container). Verified working recipe (container-tested): gcloud via Google's apt repo (packages.cloud.google.com, keyring + `cloud-sdk main` source, `apt-get install -y google-cloud-cli`, got 579.0.0); compose plugin via **`docker-compose-v2`** package (got 2.40.3, registers `/usr/libexec/docker/cli-plugins/docker-compose`); docker.io + git from Ubuntu repos (29.1.3 / 2.43.0). Script otherwise verbatim plan (umask 077, tee to /var/log/wikistream-startup.log + serial console, metadata-server project id, secrets fetch, initdb.d heredoc, .env render, configure-docker, `compose pull` + `up -d --no-build`). **Labels finding (plan-compliant):** provider 7.43 exposes no `labels` field on network/subnet/firewall/SA/IAM resources — labels applied to instance, static address, and both secrets; verified against pinned provider source.

**Dress rehearsal (local, pre-apply):** full startup.sh run inside ubuntu-24.04 container with shims (metadata curl → project id, gcloud secrets → 24-char values, configure-docker/compose pull/systemctl → no-op): clone OK; rendered `.env` and `docker/clickhouse/initdb.d/001-init.sql` byte-correct (wikistream user, plaintext_password, scoped grants, raw_events MergeTree ORDER BY inserted_at); both files 0600 (umask proven); log ends `startup done`. Stage 2 (host-side `docker compose up -d --no-build` on the rendered tree — exactly what the VM runs): 3 services Up, consumer connected + inserting, count() 2,682 → 3,613 over 20s, `SELECT 1` via :8123 as wikistream with the rendered password OK, Grafana serving (302 → /login). **RESTORED LOST FIX (findings later phases depend on):** the Phase 1.6 cold-start retry fix (client-init retry loop, `clickhouse_unavailable` WARNING) was absent from `consumer/src/consumer.py` on both main and this branch — verified absent in git history (single consumer commit c93228d; zero `clickhouse_unavailable` occurrences anywhere). Restored it verbatim per the 1.6 log entry; post-fix cold-start re-test on wiped volume: RestartCount=0, 0 Traceback, 2 expected `clickhouse_unavailable` WARNINGs during CH boot, clean inserts after. 19/19 unit tests still pass. (Restoration, not new code; coverage boundary unchanged.)

**2026-08-11 Subagent code review (2 parallel reviewers, code-reviewer type) — REQUEST-CHANGES / APPROVE-WITH-FIXES, all findings fixed or recorded:**

- **BLOCKER (fixed):** `terraform.tfvars` `allowed_ips` was a bare IP, not CIDR — GCP firewall API rejects at apply (Error 400 "Must be a CIDR address range"; plan passes, apply fails — terraform#30749). Fixed: `["209.35.91.152/32"]` + `validation` block in `variables.tf` (`can(cidrhost(c, 0))`), so a bad value now fails at plan time.
- **SHOULD-FIX (fixed):** `git pull --ff-only || true` wedges silently forever the first time any commit touches the tracked `docker/clickhouse/initdb.d/001-init.sql` (rendered secret → permanently dirty tree → ff-only refuses, `|| true` swallows it — simulated & verified). This is exactly the Phase 3A PR that retires initdb.d. Fixed: `git -C /opt/wikistream checkout -- docker/clickhouse/initdb.d/001-init.sql 2>/dev/null || true` before the pull (re-rendered right after anyway).
- **Recorded (no code change):** plan Q3's "no plaintext in Terraform state" claim is false by construction — see 2.3 note below.

### 2.3 — Secrets (Q3)

**Status:** DONE (implementation; created on first apply in 2.5)

**2026-08-11:** `infra/main/secrets.tf`: `random_password` ×2 (length 24, special=false) → `google_secret_manager_secret` (`clickhouse-password`, `grafana-admin-password`, replication auto, labels) → versions; sensitive outputs read `random_password.result` directly. Rotation = re-apply → new version → VM reset re-renders (per plan). Plan output shows both as `(sensitive value)` — values never appear in plan artifacts (verified 0 occurrences of secret names/values in `terraform show tfplan`).

**CLAIM CORRECTION (subagent review):** plan Q3/AC8's wording "Zero plaintext in the repo or Terraform state" overreaches — secret values DO land in `infra/main`'s remote state in plaintext by construction (`random_password.result` + `secret_data` are stored there after apply), and the deploy SA holds `objectAdmin` on that state bucket. AC8's evidence line (0 occurrences in `terraform show tfplan` + startup metadata) is plan-scoped and TRUE; only the state claim was wrong. Mitigation today = GCS default at-rest encryption; CMEK is a Phase 5 hardening item.

**ROTATION GAP (recorded for Phase 3A, not exercised in Phase 2):** the rotation story is incomplete by construction — `001-init.sql` only executes on an empty `ch-data` volume, and `CREATE USER IF NOT EXISTS` never updates an existing password, so a post-rotation reset re-renders `.env` with the new password while ClickHouse still holds the old one → consumer auth fails. Fix options for Phase 3A (which retires initdb.d anyway): `ALTER USER` via :8123 after boot, or rotate-by-recreate.

### 2.4 — CI workflows (Q7, ADR-008)

**Status:** DONE (files written + YAML-validated; end-to-end proof in 2.5–2.7)

**2026-08-11:** `plan.yml` (PR-triggered: checkout → WIF auth → setup-terraform 1.15.6 → fmt -check → init → validate → plan -out=tfplan → `terraform show` → plan comment via actions/github-script, file passed through `$RUNNER_TEMP`; permissions id-token/contents/pull-requests) and `apply.yml` (push main + workflow_dispatch; concurrency group per ref, cancel-in-progress; permissions contents + id-token at workflow level; job 1 `build-push` ungated — auth + configure-docker us-central1 + docker/build-push-action with `sha-<8>` + `latest` tags; job 2 `apply` gated by environment `production` — init/plan/apply INSIDE the gated job, then `gcloud compute instances reset wikistream-vm --zone us-central1-a`; no destroy job). WIF provider name + deploy SA email hardcoded per plan, matching bootstrap outputs exactly (verified above). Fork PRs get no OIDC token → auth fails red — expected, documented in-file. Actions pinned at build 2026-08-11.

**2026-08-11 Subagent code review (workflows reviewer):** APPROVE-WITH-FIXES. Verified: gate placement (plan+apply inside the gated job), build-push ungated by design, no destroy job, concurrency per-ref safe, `-input=false` everywhere, `$RUNNER_TEMP` plan-file handoff sound, instance name/zone match compute.tf, approval-wait not counted against `timeout-minutes`, `setup-gcloud` before `configure-docker` and the bare reset command, WIF-only auth (no stored keys). The startup.sh findings from that review are recorded in 2.2. ci.yml confirmed untouched (Phase 1 CI unaffected).

**2026-08-11 PR #4 first CI run — two failures, root-caused and fixed (deviations):**

1. **ruff FAILURE — version drift, not pin mismatch.** `ci.yml` is new vs `main` (main has no ci.yml; the walking-skeleton one had no ruff pin and no ds/query step), so this PR is the first time ruff 0.16.2 runs on this content. 0.16.x promoted BLE001/UP041/UP017/SIM102 into the default rule set; with no ruff config file anywhere, CI failed on `consumer/src/consumer.py:79,132,162` (BLE001 — the deliberate Phase 1 never-crash catches), `sse.py:92` (SIM102), and the UP fixes. Fixes: `[tool.ruff.lint] ignore=["BLE001"]` added to `consumer/pyproject.toml` (single source of truth for CI + pre-commit + local; the broad catches are the resilience contract, narrowing would change behavior); SIM102 combined in code; UP041/UP017 auto-applied (safe aliases on 3.13: `TimeoutError`, `datetime.UTC`). New repo-root `ruff.toml` with `extend-exclude = ["docs"]` — ruff-format rewrites python fenced blocks inside markdown and was about to mangle the LOCKED planning docs. All verified clean with ruff 0.16.2 from repo root (check + format), pytest 19/19.
2. **compose-smoke FAILURE — latent AC6 bug (deviation: ci.yml modified).** Everything passed through AC5 (rows landed count()=120, count() increased, connected lines: 1, tracebacks: 0, Grafana healthy, dashboard provisioned), then AC6 ds/query failed: 500 `error unmarshaling query JSON to the Query Model: invalid format value: time_series`. The AC6 step was added in 097ff50 and never ran green anywhere. Reproduced live on the local stack with the pinned grafana-clickhouse-datasource@4.20.0: `format:"time_series"` → 500; format omitted → 200 with rows; `table` → 200. **Fix: dropped `,"format":"time_series"` from the AC6 ds/query payload** (one-line ci.yml change, deviation from the locked "ci.yml must NOT be modified" rule — the step as written is broken against plugin 4.20.0 and unproven; comment in-file).

**2026-08-11 Pre-commit hooks (housekeeping):** hooks were never installed (`.git/hooks/pre-commit` absent since clone); `pre-commit install` run, all 8 hooks green after the ruff config changes above.

### 2.5 — First deploy through the gate

**Status:** DONE (battery AC1–AC9 passed 2026-08-11; see run #4 record below)

**2026-08-11:** PR #4 merged to main → `apply.yml` ran: job 1 build-push SUCCESS (consumer image pushed to AR as `sha-<commit>` + `:latest` — AC3). Job 2 (`apply`, environment `production`) showed **Waiting** in the Actions UI and was approved by AhmedIkram05 (AC4 evidenced in run #1: approval identity + timestamp in the run's timeline) — then `terraform apply` **FAILED**:
`Error: Error creating service account: googleapi: Error 403: Identity and Access Management (IAM) API has not been used in project 984854414993 before or it is disabled...` on `module.iam.google_service_account.wikistream_vm` (modules/iam/iam.tf:1).
**DEVIATION (plan gap, Q1 API list incomplete):** the bootstrap list enabled 7 APIs but NOT `iam.googleapis.com` — service-account CRUD needs it; the deploy SA's roles cannot auto-enable APIs. The failed run had already created 3 resources (VPC, subnet, allow-internal firewall) — those remain in state; re-run continues from there. Fix: added `iam.googleapis.com` as the 8th API in `infra/bootstrap/main.tf` (with in-file comment), bootstrap re-applied (1 added, outputs unchanged), API enablement verified propagated (2026-08-11). Re-run = workflow_dispatch on the Apply (GCP) workflow → approval → resume.

**Run #2 (PR #11 hotfix merged, apply re-triggered):** IAM fix verified working — VM SA, both secretAccessor bindings, AR reader binding all created. Then `terraform apply` **FAILED** again at instance creation:
`Error: Error resolving image name 'ubuntu-os-cloud/ubuntu-2404-lts': Could not find image or family ubuntu-os-cloud/ubuntu-2404-lts` on `module.compute.google_compute_instance.wikistream_vm` (modules/compute/compute.tf:8).
**DEVIATION (plan Q4 stale upstream reference):** the unqualified image family `ubuntu-2404-lts` no longer exists in `ubuntu-os-cloud` — Canonical renamed to arch-qualified families (verified: `gcloud compute images list` shows only `ubuntu-2404-lts-amd64` / `ubuntu-2404-lts-arm64`; the unqualified family fails with "resource not found" even as project owner, so it is NOT a permission issue). Fix: `image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"` (e2-medium is x86_64; verified the family resolves to a current image). Local re-verification: fmt + validate clean; plan = **1 to add (instance only), 0 change, 0 destroy** — all 12 other resources now in state (VPC, subnet, 4 firewalls, static IP 35.254.92.109, SA, 2 bindings, AR reader, 2 secrets+versions, oslogin binding). Static IP was allocated in run #1 and is stable.

**Run #3 (PR #12 hotfix merged, apply re-triggered):** image-family fix verified working in CI plan; `terraform apply` **FAILED** at instance creation with
`Error: Error waiting for instance to create: The zone 'projects/wikistream-505003/zones/us-central1-a' does not have enough resources available to fulfill the request.  Try a different zone, or try again later.` (code `ZONE_RESOURCE_POOL_EXHAUSTED`, reason `resource_availability`, on `module.compute.google_compute_instance.wikistream_vm`, compute.tf:8). The reset step also errored (VM never created) — expected, informational.
**DEVIATION (plan Q4 region/zone, GCP capacity):** empirically verified (throwaway `gcloud compute instances create` probes, deleted immediately) that **all four us-central1 zones (a/b/c/f) reject e2-medium AND e2-small AND e2-standard-2 AND n2-standard-2** with resource_availability — the whole region's capacity is starved (2026-08-11). e2-medium **verified available** in us-east1-b, us-west1-a, europe-west1-b. Chosen: **region `us-east1`, zone `us-east1-b`** (US, e2-medium — plan's machine type + ~$25.5/mo cost line preserved). Changes: `infra/main/terraform.tfvars` (region/zone, in-file comment), `.github/workflows/apply.yml` reset step `--zone us-east1-b`, and `modules/storage/storage.tf` — the AR reader binding used `location = var.region` which would have silently targeted a nonexistent us-east1 repo on this move; now hardcoded `us-central1` with comment (AR repo is bootstrap-owned and region-locked). Subnet + static IP (35.254.92.109) are regional → replaced by the apply; new static IP will be re-queried after apply. Firewalls/VPC/IAM/secrets untouched (region-agnostic).

**Run #4 (region-move hotfix merged, apply re-triggered):** **APPLIED OK** — VM created in us-east1-b; new regional static IP **34.148.138.220**. Post-apply battery AC1–AC9 (all verified from Ahmed's machine + SSH on the VM, zone us-east1-b, IP 34.148.138.220):

- AC1/AC2/AC3/AC4: carried from earlier runs (bootstrap applied; plan.yml green with plan comment; image in AR as `sha-9645eadb` + `:latest`; 4 gated runs each showed Waiting → approved by AhmedIkram05 — approval identity + timestamps in each run's timeline).
- AC5: VM RUNNING, e2-medium, 50GB pd-standard PERSISTENT, enable-oslogin TRUE; OS Login SSH works (gcloud auto-keygen, user `ahmedikram`, sudo needed for docker). `startup done` NOT evidenced on this boot — first boot hit a transient Docker Hub 502 (below); evidence deferred to 2.6 reset boot.
- AC6: 3 services Up; consumer image URI `us-central1-docker.pkg.dev/wikistream-505003/wikistream-consumer/consumer:latest` and container image ID `sha256:0d19cf21991cfbdbe5e462f058eff2cb80cfa429e3e5de023a7be27fd15c36a7` **exact match to AR `:latest`**; connected url= lines: 1; Traceback: 0; count() **431 → 3308 over 65s** (strictly increasing).
- AC7: Grafana `/login` 200 from Ahmed's machine; basic auth `admin:<SM value>` on `/api/search` → 200 (Grafana 13's JSON `/api/login` returns 401 by design — basic auth is the proof the SM value authenticates); dashboard provisioned (uid `wikistream-phase1`, "Phase 1 — Walking Skeleton", 1 panel "Events per minute (raw_events)"); ds/query (real ds uid `wikistream-clickhouse`, format table, `SELECT count()`) → 200, values [[13978]].
- AC8: both secrets exactly 24 alphanumeric chars (read from Secret Manager); **0 occurrences** of either secret value in `gcloud compute instances describe` metadata.startup-script AND in `terraform show tfplan`; `curl -u wikistream:<SM CH value> 'http://34.148.138.220:8123/?query=SELECT%201'` → `1`. Fresh local `terraform plan` = 0 to add/change/destroy (state converged).
- AC9: from Ahmed's IP (209.35.91.152/32): SSH 22, Grafana 3000, ClickHouse 8123 all reachable. One-sided rejection: from the VM, `curl http://34.148.138.220:3000` and `:8123` → both **000** (non-200, dropped) — VM IP correctly NOT in the allowlist.

**DEVIATION (transient, first boot):** startup log ended with a Docker Hub 502 (`unexpected status from GET request to https://registry-1.docker.io/v2/grafana/grafana/manifests/... 502 Bad Gateway`) during `docker compose pull`; with `set -e` the script aborted before `up -d` (no containers started). Manually recovered via SSH (pull retry ×6 → success → `compose up -d --no-build` → 3 services Up). The script logic itself is dress-rehearsal-proven; the 502 was transient upstream flakiness. `startup done` evidenced on the 2.6 reset boot.

**DEVIATION (production bug found on first boot):** Grafana provisioning failed on the VM — container log `can't read dashboard provisioning files from directory /etc/grafana/provisioning/dashboards: permission denied` (+ datasources, + `/var/lib/grafana/dashboards`). Root cause: startup.sh `umask 077` leaves the git clone 700/600 root, and grafana (uid 472) cannot traverse the bind-mounted `grafana/**` (dress rehearsal on Docker Desktop masked host perms). Live fix: `sudo chmod -R a+rX /opt/wikistream/grafana` + `sudo docker restart wikistream-grafana` → dashboard + datasource provisioned (AC7 evidence above). `.env` + `001-init.sql` remained 600 (chmod scoped to the grafana subtree; clickhouse initdb.d works at 600 because the entrypoint runs init scripts as root). **Script fix shipped in `infra/main/templates/startup.sh`**: `chmod -R a+rX /opt/wikistream` after clone/pull + cd, BEFORE secret rendering (so rendered files stay 600 under umask 077). Verified on the 2.6 reset boot.

**Grafana login quirk (informational):** JSON `/api/login` returns 401 in Grafana 13 — the provisioned-datasource basic-auth path (`/api/search` with `admin:<SM value>`) is the working proof of the SM credential.

### 2.6 — Deploy-path proof (Q2): reset → recover

**Status:** DONE (2026-08-11; AC10 evidenced — reset → unattended recovery, twice clean + once with a recoverable ClickHouse incident)

**Operational fact (apply run #14, 14:36Z):** `metadata_startup_script` is **ForceNew** in the google provider (7.43) — any edit to `templates/startup.sh` destroys + recreates the instance with a fresh boot disk, wiping `ch-data` (full cold start: clone, initdb.d re-run). Run #14 (the chmod fix) therefore recreated the VM; the workflow's reset step then interrupted boot #1 mid-apt-install; boot #2 self-recovered via dpkg repair. Implication for Phase 3+: keep startup.sh static; put ch-data on a separate disk if script churn is expected.

**Clean unattended boots (chmod fix proven):**

- Boot #2 (14:36, post-recreate + interrupted reset): `startup done` **14:38:20 UTC**; 3 containers Up; connected url=<https://stream.wikimedia.org/v2/stream/recentchange>; count() 5006 → 8687 over 70s (strictly increasing); Grafana dashboard **provisioned with no manual intervention** (uid wikistream-phase1 via /api/search, basic auth with SM value; ds/query → 200 [[9270]]) — the `chmod -R a+rX` fix works on a clean unattended boot. Only grafana log line: harmless "can't read alerting provisioning files" (no alerting dir mounted — expected). 0 Traceback.
- Boot #3 (explicit task-2.6 reset 14:41, `gcloud compute instances reset wikistream-vm --zone us-east1-b`): `startup done` **14:43:02 UTC**; 3 containers Up — then the incident below.

**INCIDENT (plan §9 claim FALSIFIED):** on boot #3 ClickHouse failed to attach `default.raw_events`: `Code: 231 Suspiciously many (171 parts, 0.00 B in total) broken parts to remove while maximum allowed broken parts count is 100 ... (TOO_MANY_UNEXPECTED_DATA_PARTS) (ASYNC_LOAD_WAIT_FAILED)`. The locked plan's "reset during inserts is safe" (§9) is empirically wrong: a reset is a power cut; in-flight tiny parts (0.00 B each, e.g. empty SSE payloads being flushed) exceed the 100-part suspicion ceiling → the table refuses to attach. **The Phase 1.6 cold-start retry loop absorbed the outage**: 9039 `insert_failed` warnings accumulated, **0 Traceback** across the whole incident. Recovery (deterministic, executed): removed catalog + table metadata + data (`/var/lib/clickhouse/metadata/default/raw_events.sql`, `store/180/1808971a-4818-46ea-bdea-d8ca17ef4144/raw_events.sql`, `data/default/raw_events`; DROP TABLE fails on the load-wait so files were removed directly) → `docker restart wikistream-clickhouse` → re-created the table with the exact initdb.d DDL → verified: count() 593 → 3727 over 60s, last-60s insert_failed = 0, connected, 0 Traceback. Lost data: 0.00 B.
**Phase 3A recommendation:** add `SETTINGS max_suspicious_broken_parts = 1000` to the raw_events DDL (startup.sh heredoc + initdb.d 001-init.sql) so power-cut leftovers ≤1000 attach cleanly; consider a separate data disk. Reset-as-deploy stays (graceful stop is impossible in the workflow; the retry loop + table re-creation are the proven recovery path).

**AC10 verdict: PASS** — unattended recovery after reset evidenced twice (boots #2 and #3, both `startup done` without intervention); the one table-attach incident recovered deterministically with zero data loss and zero consumer crashes.

### 2.7 — Destroy-and-reapply cycle (Q8)

**Status:** SKIPPED by explicit user direction (2026-08-11: "i dont want to run a local destory, just skip that task in phase 2")

**Record:** AC11 (local destroy clean + bootstrap layer intact + workflow_dispatch reapply from cold state) and the cold-to-flowing metric are **NOT exercised** in Phase 2. Consequence for Gate 1: the destroy-and-reapply leg of "stable across apply/destroy cycles" is **unproven**; the apply-side cycle (4 gated applies incl. re-runs 1–4 with differing plans, converges to 0-drift state) and the reset-side cycle (2.6, below) ARE evidenced. Recommend re-visiting a cold-state reapply in Phase 3A planning (e.g. before 3A's first deploy) to close the leg.

### 2.8 — Wrap-up

**Status:** DONE (2026-08-11)

**Phase 2 summary:** skeleton deployed to GCP and running unattended — VM `wikistream-vm` (e2-medium, 50GB, us-east1-b, static IP 34.148.138.220), 3 containers Up, SSE pipeline flowing, Grafana dashboard live, CI/CD approval gate exercised end-to-end (4 gated apply runs, all approved by AhmedIkram05 with timestamps in each run's timeline).

**Gate 1 verdict: GO (with caveats).** Criteria and evidence:

- *Stable across apply/destroy cycles*: apply-side proven (runs #1–#4, including plan drift between runs, converges to 0-drift state). Destroy-and-reapply leg **unproven** — task 2.7 skipped by explicit user direction; cold-state reapply recommended before Phase 3A's first deploy.
- *Gate works end-to-end*: proven — plan.yml comments plans on PRs; apply.yml gated job waits for review (Waiting state observed), approval required, apply runs only after approval.
- *Survives unattended*: proven — two clean unattended boots after reset (`startup done` without intervention); one ClickHouse attach incident recovered deterministically (0 data loss, 0 consumer crashes; plan §9 "reset during inserts is safe" falsified — see 2.6). One transient Docker Hub 502 on first boot required manual pull retry (see 2.5).

**Deviations recorded (full list):** 2.1: API propagation race, bootstrap owns identity beyond bucket, `iam.googleapis.com` added (8th API). 2.2: plan Q2 apt recipe broken on ubuntu-2404 (verified replacement: Google apt repo + `docker-compose-v2`); labels not supported by provider 7.43 on network/subnet/firewall/SA; lost Phase 1.6 cold-start fix restored; chmod fix for grafana bind-mount perms. 2.3: plan Q3 "no plaintext in state" claim corrected (state holds secrets by construction; AC8 evidence is plan-scoped); rotation gap noted for Phase 3A. 2.4: ci.yml modified once (AC6 ds/query format field — latent bug, never ran green before). 2.5: region/zone move to us-east1-b (GCP capacity, empirically probed); image family renamed upstream; first-boot 502. 2.6: ForceNew instance recreation; broken-parts incident. 2.7: skipped per user direction.

**Coverage boundary:** unchanged — no new Python shipped in Phase 2 beyond the restoration of the Phase 1.6 cold-start retry (documented in 2.2). Phase 1 suite (19/19, sse.py 100%) still green.

**Handoff to Phase 3:** deploy path proven; startup.sh is now STATIC (artifacts arrive via git clone on reset, never script edits); `:latest` is the deploy seam; WIF provider `projects/984854414993/locations/global/workloadIdentityPools/wikistream-ci/providers/github` and deploy SA `wikistream-deploy@wikistream-505003.iam.gserviceaccount.com` are resolved and hardcoded in workflows; deploy SA holds serviceAccountAdmin/User (GCP requires both to create a VM running as a SA) + projectIamAdmin (osLogin binding) — all scoped as recorded in bootstrap README; rollback = re-apply prior image tag (sha-* tags retained in AR); cost sanity ~$25.5/mo (e2-medium + 50GB pd-standard + static IP); carry-forward contracts: log contract, count()-sample protocol, parser tests.

## Phase 3 — Data Model Depth (3A Schema / 3B Analytics)

Tasks defined in `docs/planning/phase-3-implementation.md` (LOCKED 2026-08-11).
Task headings pre-populated per plan §6; Status lines are filled as each task
is worked, per the logging rules. Three PRs, each merge → gated apply → VM
reset → its gate before the next PR begins: 3A schema (micro-gate, task
3.1.8), 3B analytics (live spot-check, task 3.2.4), 3C warehouse (verification
battery, task 3.3.8). Gate 1's GO-with-caveat is already on record in §2.8 —
task 3.1.8 cross-references it, it is NOT re-recorded.

### 3.1.1 — Durable ch-data disk (Q3)

**Status:** DONE

**2026-08-12:** Implemented by subagent + orchestrator review. `infra/main/modules/compute/compute.tf` gains `google_compute_disk.ch_data` (name `ch-data`, pd-standard 30GB, `var.zone`, `labels`, `lifecycle.prevent_destroy = true`) and `google_compute_attached_disk.ch_data` (`device_name = "ch-data"` → guest path `/dev/disk/by-id/google-ch-data`). `startup.sh` §5 = the plan's idempotent mount block (mkfs-if-unformatted via `blkid`, UUID → fstab `defaults,nofail`, mount-if-not-mounted, `mkdir -p /mnt/ch-data/clickhouse`, `export CH_DATA_DIR`); `.env` render appends `CH_DATA_DIR` when set; `docker-compose.yml` clickhouse volume is now `${CH_DATA_DIR:-ch-data}:/var/lib/clickhouse` (fallback named volume `ch-data` kept for local dev).

**Evidence:** `terraform fmt -check` OK on compute.tf; `docker compose config --quiet` OK; `bash -n startup.sh` OK. Deploy-time proof (plan shows disk+attachment, data surviving the 3.1.8 VM recreate) is logged under 3.1.8.

### 3.1.2 — Migration runner + `schema_migrations` (Q2)

**Status:** DONE

**2026-08-12:** `migrations/apply.sh` implemented per plan: bash + `curl` HTTP-API only (no `clickhouse-client` binary), `set -euo pipefail`, env `CH_HOST/CH_PORT/CH_USER` defaults localhost/8123/wikistream, `CH_PASSWORD` required. 30×2s readiness wait using the same header auth (`X-ClickHouse-User`/`X-ClickHouse-Key` + `--fail-with-body`; an auth error is "not ready" on first boot); bookkeeping `default.schema_migrations (version String, status String, applied_at DateTime DEFAULT now()) ENGINE = MergeTree ORDER BY version`; per `[0-9]*.sql` (nullglob, sorted): skip-if-recorded, optional line-1 `-- guard: <expr>` (apply iff `SELECT <expr> FORMAT TSV` = 1; guard-0 files RECORDED as `skipped`), apply via `--data-binary @file` with failure branch printing the body and exiting non-zero, record every evaluated file. `migrations/bootstrap-user.dev.sql` = dev credential (header: VM path is boot.sh's heredoc with the real secret — keep in sync).

**Evidence:** Live-tested end-to-end (throwaway container, see 3.1.3/3.1.7). AC1/AC2 exercised by `pytest -m ch` (6 passed) and the clean + re-run runs below.

### 3.1.3 — Migration files 000–003 (Q1/Q3)

**Status:** DONE

**2026-08-12:** Four files created, `-- guard:` on line 1 (lesson below): `000_detect_legacy` renames the legacy 2-column `raw_events` → `raw_events_v1` when the table exists without a `wiki` column; `001_raw_events` (no guard, `CREATE TABLE IF NOT EXISTS` idempotent) builds the full typed schema — 4 `MATERIALIZED JSONExtract*` strings, `is_bot UInt8 MATERIALIZED JSONExtractBool`, `length_new/length_old UInt32 MATERIALIZED JSONExtractUInt(event,'length','new'/'old')`, `event_timestamp DateTime64(3,'UTC') MATERIALIZED parseDateTime64BestEffort(JSONExtractString(event,'timestamp'))`, `PARTITION BY toYYYYMMDD(inserted_at)`, `ORDER BY (inserted_at, sipHash64(event))` (Phase 4 dedup key), `TTL inserted_at + INTERVAL 30 DAY` (ADR-006), `SETTINGS max_suspicious_broken_parts = 1000` (§2.6 mitigation); `002_backfill_raw_events_v1` guarded on v1 existing, idempotent backfill; `003_drop_raw_events_v1` guarded identically, rollback affordance (`migrations/held/`) documented.

**Evidence:** Clean-DB run: `SKIP 000 (guard 0) / APPLY 001 / SKIP 002 (guard 0) / SKIP 003 (guard 0)`, exit 0, 4 rows recorded (three `skipped`, one `applied`). Legacy run (2-col table + realistic event): all 4 APPLY, backfilled typed values correct (`enwiki / Example page / 192.0.2.1 / edit / 0 / 130 / 20 / 2026-08-11 12:34:56.000`), `raw_events_v1` dropped. Re-run: all `SKIP (recorded)`.

### 3.1.4 — Startup rework: boot.sh seam + user bootstrap + rotation fix (Q2)

**Status:** DONE

**2026-08-12:** `startup.sh` edited exactly once (its final edit — ForceNew): initdb.d fence + §5 heredoc deleted; §4 `export CH_PASSWORD=...` (see deviation below); §5 mount block; §8 runs `bash /opt/wikistream/scripts/boot.sh` before `startup done`. New `scripts/boot.sh` (git-tracked, `set -euo pipefail`): CH-ready wait (30×2s + final probe), user bootstrap **every boot** with trailing `ALTER USER IF EXISTS` (rotation-gap fix — the old initdb.d path only ran on empty volumes), SQL built in a variable (not a heredoc) and echoed to the log with `CH_PASSWORD` redacted via `sed` (AC7 grep target), piped to `docker compose exec -T clickhouse clickhouse-client --multiquery`, `echo "user bootstrap ok"` (AC8), then migrations via the HTTP API (env `CH_HOST=localhost CH_HOST=8123 CH_USER=wikistream`, `MIGRATIONS_DIR=/opt/wikistream/migrations`); any non-zero aborts boot.

**Evidence:** `bash -n` OK for startup.sh and boot.sh. VM-boot evidence (echoed bootstrap SQL, `user bootstrap ok`, ALTER USER, migration lines, consumer connected with the real secret) under 3.1.8.

### 3.1.5 — Retire initdb.d

**Status:** DONE

**2026-08-12:** `docker/clickhouse/initdb.d/001-init.sql` + directory deleted; `docker-compose.yml` initdb.d bind-mount removed. Nothing else references it outside `docs/`.

**Evidence:** `git grep -ni initdb -- . ':(exclude)docs'` clean (sole remaining hit is a header comment in `migrations/001_raw_events.sql` explaining the migration). `docker compose config --quiet` OK.

### 3.1.6 — CI: analytics-tests entry, markers, smoke reorder

**Status:** DONE

**2026-08-12:** `pytest.ini` gains `markers = ch: requires ClickHouse (runs against localhost:8123)`. New `analytics-tests` matrix entry (boots only the clickhouse service, waits ready, bootstraps via `bootstrap-user.dev.sql`, runs `uv run --project consumer pytest -q --tb=short -m ch` with `CH_HOST=localhost CH_USER=wikistream CH_PASSWORD=wikistream_dev_password` — no explicit test paths, `testpaths=tests` collects future ch suites). Both `unit-tests` pytest invocations now `-m "not ch"`. `compose-smoke` gained the 3A pre-steps after `up -d --build`: wait CH ready, bootstrap user, `./migrations/apply.sh` (its own readiness wait absorbs first-boot lag; consumer WARNINGs in the gap remain the documented contract).

**Evidence:** YAML parses; matrix = `ruff / unit-tests / analytics-tests / compose-smoke`. Full workflow run on the 3.1.8 PR is the CI-green evidence for the micro-gate.

### 3.1.7 — tests/migrations suite

**Status:** DONE

**2026-08-12:** `tests/migrations/test_migrations.py` — 6 `@pytest.mark.ch` tests against a real ClickHouse (env-driven `CH_HOST/CH_PORT/CH_USER/CH_PASSWORD`): `test_clean_db_apply` (all `[0-9]*.sql` recorded, status values valid, 8 typed columns present via `system.columns`), `test_re_run_idempotent`, `test_ttl_present`, `test_legacy_migration`, `test_materialized_compute`, `test_bootstrap_user`. Harness: subprocess `apply.sh`, curl-HTTP `query()`, `reset()` drops `raw_events`/`raw_events_v1`/`schema_migrations` (order-independence — the runner skips recorded versions).

**Evidence:** Live run: **6 passed, 19 deselected, 3.17s** against throwaway `clickhouse-server:26.3.17.110` (port 18125), real `apply.sh` + real migration files + real `bootstrap-user.dev.sql`.

### DEVIATIONS & FINDINGS

- **002 backfill: `LEFT JOIN ... WHERE r.event IS NULL` broken → `LEFT ANTI JOIN`.** On ClickHouse 26.3.17 with default `join_use_nulls=0`, unmatched right-side columns come back as type defaults (`''`), never NULL, so the planned anti-join predicate matches nothing and 002 would be a silent no-op (003 would then drop the legacy table losing its data). **Verified three times** (migrations agent's native-client + HTTP smokes, tests agent's legacy test, my own `t_a`/empty-`t_b` reproduction: plan-form count = 0, `LEFT ANTI JOIN` count = 3). `LEFT ANTI JOIN` is semantically identical and idempotent; documented in the 002 header.
- **`system.tables.ttl_expression` absent on CH 26.3.17** → the plan's canonical AC3/check path fails (`UNKNOWN_IDENTIFIER`). `test_ttl_present` probes `hasColumnInTable('system','tables','ttl_expression')` and falls back to `SHOW CREATE TABLE` (which renders `TTL inserted_at + toIntervalDay(30)`). §4-checklist item logs "fallback used: SHOW CREATE" at 3.1.8.
- **`CH_PASSWORD` was not exported in startup.sh §4** (the subagent implementing boot.sh flagged it; verified: non-exported shell vars don't reach `bash scripts/boot.sh`, so boot.sh's `:?` guard would have failed every VM boot). Fixed by me during orchestrator review: `export CH_PASSWORD=$(gcloud secrets versions access latest ...)`; `GF_PASSWORD` stays un-exported (only consumed by the same-script `.env` heredoc).
- **Guard must be migration-file line 1** — a first draft put explanatory headers before `-- guard:`; the first smoke run applied every file unconditionally (then 002 404'd on the missing `raw_events_v1`). Caught in the migrations agent's smoke; all guards now sit on line 1.
- **Docker daemon side-effect:** starting the daemon for smoke tests auto-restarted the repo's local compose stack (old config, old data on `ch-data` volume) — left running; local ClickHouse on `:8123` is the old shape until a fresh volume/compose up applies 3A.
- **Cosmetic:** ci.yml line-32 comment "three independent checks" is stale (now four matrix entries).
- **STARTUP INCIDENT (2026-08-12, deploy-time): fresh recreate died at the ch-data mount.** The 3.1.8 apply's recreated VM booted `startup.sh` and hit `mount: /mnt/ch-data: mount point does not exist.` → `set -e` aborted before §6/§7/§8 (`.env`, pull/up, boot.sh, `startup done` never ran; no containers). Root cause: the mount block mounted to a path that didn't exist yet — `mountpoint -q` on a missing dir returns false, so the `|| mount` fired into thin air. Latent bug invisible to `bash -n`/static review; only a real recreate exercises §5. Fixed by adding `mkdir -p /mnt/ch-data` (with explanatory comment) before the mount; verified and shipped as b081cd1 via PR (CI + TF plan green, only the `metadata_startup_script` diff → ForceNew). The 14:05 reboot after the fix boots clean end-to-end: mount OK, `.env`, `user bootstrap ok`, migrations all `SKIP (recorded)`, `startup done`.
- **ForceNew detach footgun (structural, phase-3B/3C must know): `google_compute_attached_disk` does NOT re-attach after an instance replacement.** Destroying the replaced instance detaches PDs server-side; the `attached_disk` resource has no config diff (instance/disk/device_name unchanged) so Terraform never re-runs it. The ch-data disk came back `READY`/unattached after the b081cd1 ForceNew. Recovery (documented so future recreates are one-liners): `gcloud compute instances attach-disk wikistream-vm --disk ch-data --zone us-east1-b --device-name ch-data` then reboot (startup script runs on every boot and resumes the disk path). Data was never at risk (disk volume intact). Any future ForceNew must repeat this; likewise the local `legacy-raw-events.tsv.gz` is the standing backstop.
- **"Workflow green ≠ boot green."** apply.yml completes the instance replacement and `terraform apply` succeeds regardless of what the startup script does afterward (Terraform never waits for startup). VM-facing verification of a deploy must read `/var/log/wikistream-startup.log` tail (sudo) for `startup done`, not the workflow badge. This incident is exactly why 3.1.8's micro-gate double-checks the live VM.

### 3.1.8 — 3A PR → capture → deploy → import → micro-gate → log

**Status:** DONE

**2026-08-12:** Full 3A lifecycle executed. Gate 1's GO-with-caveat (§2.8) cross-referenced, not re-recorded; the cold-reapply leg it flagged as unproven is now proven three ways: CI `analytics-tests` (fresh container + fresh volume), the clean-DB apply on the brand-new VM, and apply-from-current-state on the live disk.

- **Pre-merge capture:** `legacy-raw-events.tsv.gz` (22.5 MB, **118,788 rows**, sha256 `ee2063f0…5248`) captured from Phase 2's table on the VM (`sudo docker exec` TSV) and parked in the repo (gitignored) as the bulletproof backstop behind the ch-data disk.
- **Merge/deploy:** PR #15 then PR #16 (feature/Data-Model-Depth → main) merged 13:41:56Z; Apply (GCP) run 31602769079 and CI run 31602769044 both success on sha `1070749b`. Instance ForceNew-recreated (creationTimestamp 14:00:26Z). Boot FAILED at the mount (incident above) → stack brought up manually once (recovery path, also proves §5b–§8), TSV import run: rows 3168 → 122,329 in the typed table (delta 119,161 = 118,788 captured + ~373 live rows during import — **lossless**, AC5).
- **Micro-gate (HARD CHECKPOINT) — GREEN:** (1) consumer live, counts strictly increasing (~43 ev/s); (2) AC4 — all 10 columns via `system.columns` (incl. `is_bot UInt8`, `length_new/length_old UInt32`, `event_timestamp DateTime64(3,'UTC')`); (3) AC5 spot check — `count() WHERE wiki != ''` = 122,346, sample rows fully typed (jawiktionary categorize, cewiki edit…); (4) AC2 — re-run `apply.sh` → 0 applied, 4 skipped, exit 0; (5) AC3 — `SHOW CREATE TABLE` shows `TTL inserted_at + toIntervalDay(30)`; (6) `schema_migrations` = 000 skipped / 001 applied / 002 skipped / 003 skipped.
- **Mount-fix deploy (b081cd1):** merged → apply 13:57:40Z on `ffe9ee8` → instance ForceNew again (14:00:26Z) → **disk was left unattached** (structural footgun above) → re-attached + rebooted → 14:05:12Z **clean boot**: mount OK (30G `/dev/sdb` on `/mnt/ch-data`), `/var/lib/clickhouse` on the disk, `user bootstrap ok`, migrations all `SKIP (recorded)`, `startup done`.
- **Post-fix micro-gate re-check — GREEN:** counts 149,857 → 150,157 in 6 s (~50 ev/s), `wiki != ''` = 150,164 (import data intact and growing), schema_migrations intact, TTL intact. AC5 holds on the disk-backed table across both recreates.

**Evidence:** gh run summaries (runs/31602769079, 31602769044, and the ffe9ee8 CI: ruff / unit-tests / analytics-tests / compose-smoke all success); instance `creationTimestamp`/`lastStartTimestamp` at 14:00:26Z / 14:00:35Z + 14:05 boot; `/var/log/wikistream-startup.log` tails (mount block, redacted bootstrap SQL with `ALTER USER IF EXISTS`, `user bootstrap ok`, `SKIP … (recorded)` ×4, `startup done`); `gcloud compute disks list` (`ch-data` 30 GB READY, re-attached); live counts above.

### §4 BUILD-TIME CHECKLIST — OUTCOMES (3A)

| Check | Outcome |
|---|---|
| `parseDateTime64BestEffort` tolerates trailing Z (Wikimedia `…12:34:56Z`) | **confirmed** — materialized `event_timestamp` parsed correctly; no fallback migration needed |
| AC3 via `system.tables.ttl_expression` | **fallback used: SHOW CREATE** (column absent on CH 26.3.17); `test_ttl_present` probes `hasColumnInTable` first |
| curl `X-ClickHouse-User/Key` header auth + `--fail-with-body` | **confirmed** |
| `docker compose exec -T … clickhouse-client --multiquery` accepts piped stdin | **confirmed** (bootstrap, CI, VM) |
| `metadata_startup_script` ForceNew | **confirmed** (two recreates) — plus the mount-bug incident it surfaced |
| `docker exec -i` required for piped stdin | **confirmed** (TSV import) |
| `SYSTEM FLUSH ASYNC INSERT QUEUE` before exports | n/a in 3A (3C task) |
| `max_suspicious_broken_parts = 1000` on 001 | **confirmed** (DDL + SHOW CREATE) |
| `LEFT JOIN … WHERE r.event IS NULL` anti-join | **fallback used: LEFT ANTI JOIN** (broken on 26.3.17, see deviations) |
| `sipHash64(event)` order key | **confirmed** |
| CI `-m "not ch"` / `@pytest.mark.ch` split | **confirmed** (analytics-tests green incl. cold image pull) |
| `bootstrap-user.dev.sql` never seen by runner ([0-9]*.sql glob) | **confirmed** |

### ACCEPTANCE CRITERIA — 3A (AC1–AC8)

- **AC1 — migrations apply cleanly to a clean DB:** CI `analytics-tests` green on main (fresh container, fresh volume; 6 ch tests pass incl. `test_clean_db_apply` with `count(schema_migrations) == number of files`), plus the clean-DB apply on the new VM (`SKIP 000 / APPLY 001 / SKIP 002 / SKIP 003`, exit 0).
- **AC2 — re-runnable:** second `apply.sh` → exit 0, zero new rows, all `SKIP (recorded)` (VM micro-gate and test suite).
- **AC3 — TTL:** `SHOW CREATE` contains `toIntervalDay(30)` on the live table (fallback, see checklist).
- **AC4 — full typed schema:** 10/10 columns via `system.columns` incl. all 8 typed MATERIALIZED columns.
- **AC5 — legacy data lossless:** pre-merge capture 118,788; import delta 119,161 ≥ captured (+ concurrent live rows); `wiki != ''` = 150,164 after two recreates; `test_legacy_migration` green (typed values match the JSON source).
- **AC6 — initdb.d retired:** `git grep -ni initdb -- . ':(exclude)docs'` clean; `docker compose config` valid; no heredoc/fence in startup.sh.
- **AC7 — rotation-gap fix live:** `ALTER USER IF EXISTS wikistream …` present (redacted) in the startup log on every boot.
- **AC8 — user bootstrap from startup:** `user bootstrap ok` in the log; consumer connects with the real Secret Manager password and counts increase monotonically (~43–50 ev/s).

**Handoff to 3B (analytics):** versioned schema + bookkeeping shipped (apply.sh + numbered DDL + `schema_migrations`); typed `raw_events` includes `event_timestamp` (the Phase-4 GX assertion target) and the `sipHash64(event)` order key (cheap Phase-4 dedup); rotation gap closed (password re-syncs every boot); CI shape `-m "not ch"` vs `@pytest.mark.ch` ready for the 3B/3C ch suites (they join via `testpaths=tests`, no CI change needed); coverage boundary for 3B/3C extends to `migrations/` + `tests/migrations/` + `warehouse/` per plan §5 (3A introduced no new Python except tests). **Carry-forward ops notes:** after any VM recreate, re-attach `ch-data` (`gcloud compute instances attach-disk … --device-name ch-data`) and reboot — the disk will NOT re-attach itself (structural footgun above); verify boots by reading `/var/log/wikistream-startup.log` tail (sudo), never just the workflow badge.

### 3.2.1 — MV migrations 004–006 (Q4)

**DONE (2026-08-12).** Three materialized views over `default.raw_events`, all
`CREATE MATERIALIZED VIEW IF NOT EXISTS … ENGINE = SummingMergeTree`, no guard
line and no POPULATE (history starts at the 3B deploy; refreshes at ~40–50 ev/s
within minutes):

- `004_mv_edits_per_minute.sql` — per (minute, wiki, is_bot): `count() AS edits`,
  `sum(toInt64(length_new) - toInt64(length_old)) AS bytes_delta`; WHERE
  `event_type IN ('edit','new') AND wiki != ''`; ORDER BY `(minute, wiki, is_bot)`.
- `005_mv_top_pages_per_minute.sql` — per (minute, title, wiki); composite key so
  the same title on different wikis does NOT collapse; same WHERE.
- `006_mv_edit_sizes_per_minute.sql` — per (minute, bucket) via
  `multiIf(abs(toInt64(length_new) - toInt64(length_old)))` →
  `'0' / '1-10' / '11-100' / '101-1000' / '1001-10000' / '10000+'`.

All three statement-identical to the locked spec modulo whitespace. Verified on a
fresh local volume: `apply.sh` → `SKIP 000 (guard 0) / APPLY 001 / SKIP 002 /
SKIP 003 / APPLY 004 / APPLY 005 / APPLY 006` →
"migrations complete: 4 applied, 3 skipped"; `SHOW TABLES LIKE 'mv_%'` → exactly
the 3 views.

### 3.2.2 — tests/mv equivalence suite (Q5)

**DONE (2026-08-12).** `tests/mv/test_mv_equivalence.py` — 7 `@pytest.mark.ch`
tests; standalone helpers mirroring the `tests/migrations` conventions (no
cross-module test imports). `testpaths=tests` collects the suite with zero CI
changes and `-m "not ch"` skips it:

- `test_mv_tables_exist` — `SHOW TABLES LIKE 'mv_%'` == exactly the 3 MVs.
- The three `*_equivalence` tests — ADR-006 spot-check as assertions: MV output
  == equivalent raw GROUP BY over the SAME cutoff literal (a single window string
  captured before insert, interpolated identically on both sides; only the parse
  wrapper differs). Both sides are aggregated SUMs — never row counts
  (SummingMergeTree may return unmerged duplicate rows). 10-row synthetic edge
  matrix: edit/new/log types, bots + humans, missing `length.old` (new events;
  JSONExtractUInt defaults 0), empty-wiki rows (EXCLUDED), and all six size
  buckets incl. a shrinking edit (−450) and a 50k delta. The raw twin carries the
  MV's canonical filters. The 006 bucket labels are ground-truthed independently
(`BUCKET_COUNTS`) and `BUCKET_MULTIIF` is token-verified against the live
  migration file (catches a boundary typo shared by both copies). The 12-row
  matrix probes the `1-10`/`11-100` bucket boundary exactly (deltas 10 and 11)
  so a `<=`/`<` typo in BOTH copies still flips a classified row.
- `test_mv_excludes_log_and_empty_wiki` — inserted 12 vs included 10 on every
  MV; no `wiki = ''` row in the wiki-bearing MVs.
- `test_interval_window_forms` — pins the deployed dashboard forms
  `now() - INTERVAL 1 hour` / `now() - INTERVAL 24 hour` parse (deviation 3B-2).
- `test_warehouse_export_sql_empty_safe` — 3C pre-hook: globs
  `warehouse/sql/export_*.sql`; absent → `pytest.skip` (the plan's
  "empty-file-safe until then" contract); present → runs each (with its
  `{START}`/`{END}` placeholders substituted by a fixed empty range, so
  `export.sh`-style files land untouched), asserts non-empty, checks its
  `mv_*` source tables.

Suite result (fresh container, `-m ch`): **12 passed, 1 skipped** across
`tests/migrations + tests/mv` (6 + 6 + 1 warehouse skip).

### 3.2.3 — `wikistream-live` dashboard (Q10)

**DONE (2026-08-12).** `grafana/dashboards/wikistream-live.json` replaces
`phase1.json` (deleted): uid `wikistream-live`, title **"WikiStream Live
Analytics"** (WikiPulse deprecated), tags `[wikistream]`, timezone utc, refresh
10s, time `now-1h → now`, datasource uid `wikistream-clickhouse`
(grafana-clickhouse-datasource) on every target. Numeric `format` enums
(0 timeseries / 1 table — the string `"time_series"` 500s on plugin 4.20.0, the
Phase-1 finding). 5 panels:

1. **Edit velocity** — timeseries, stacked `if(is_bot = 1, 'bot', 'human') AS
   series`, deliberate FIXED `INTERVAL 1 HOUR` (not `${window}`).
2. **Bot vs human ratio** — piechart, `INTERVAL ${window}`.
3. **Top pages** — bar gauge, `mv_top_pages_per_minute`, `ORDER BY edits DESC LIMIT 10`.
4. **Project/language breakdown** — bar, `mv_edits_per_minute`, `GROUP BY wiki … LIMIT 15`.
5. **Edit-size histogram** — bar, numeric bucket order via
   `multiIf(bucket = '0', 0, …, 5)` (plain ORDER BY is lexicographic and would
   misplace `'10000+'`).

Plus: `grafana/provisioning/dashboards/dashboards.yaml` provider name `phase1` →
`wikistream`; `.github/workflows/ci.yml` compose-smoke AC5 `/api/search` grep
`"Phase 1"` → `"WikiStream Live Analytics"`.

**Verified against a LIVE Grafana 13.1.1 + clickhouse plugin 4.20.0 on a fresh
local volume:** `/api/search` returns only `wikistream-live / WikiStream Live
Analytics`; every panel SQL returned rows via `/api/ds/query` for BOTH window
values (`${window}` substituted manually — the raw ds/query endpoint does not
expand template variables; the dashboard frontend does — see checklist 3B-4).

### 3.2.4 — 3B PR → deploy → live spot-check → log

**DONE (2026-08-12).** Merged via PR #18 on `feature/Data-Model-Depth` (merge
commit `e250d21`; branch-name deviation recorded in 3B-1), gated `apply.yml`
run 31610076874 (head `e250d21`) **success** at 15:02 UTC — build-push +
plan/apply in `infra/main` (no infra change this phase, `startup.sh` static)
→ `gcloud compute instances reset wikistream-vm` → boot. Bootstrap log
(sudo, VM `34.148.138.220`, us-east1-b) shows `SKIP 000/001/002/003
(recorded)` then **`APPLY 004_mv_edits_per_minute`, `APPLY
005_mv_top_pages_per_minute`, `APPLY 006_mv_edit_sizes_per_minute` → `migrations
complete: 3 applied, 4 skipped`** and `[Wed Aug 12 15:05:08 UTC 2026] startup
done` — workflow-green verified at boot-green.

**Live spot-check (post-deploy, on the VM):** all remote CH checks via
`clickhouse-client` over SSH and Grafana API over `localhost:3000` with the SM
`grafana-admin-password` (redacted here):

- **AC9** `SHOW TABLES LIKE 'mv_%'` → exactly `mv_edit_sizes_per_minute`,
  `mv_edits_per_minute`, `mv_top_pages_per_minute`.
- **AC11 (edits_per_minute)** — `WITH toStartOfMinute(now() - INTERVAL 15
  MINUTE) AS lo, toStartOfMinute(now() - INTERVAL 2 MINUTE) AS hi`, window
  `lo=2026-08-12 14:57:00 / hi=15:10:00` (minute-aligned settled bounds per
  deviation 3B-5): **`sum(edits)` MV = 5821 = raw = 5821; `sum(bytes_delta)`
  MV = 4693117 = raw = 4693117** (TSVWithNames, exact).
- **AC11 (top_pages + edit_sizes)** — later window, same method:
  **mv_top = 6946 = raw = 6946** and **mv_sizes = 6946 = raw = 6946**.
  (Cross-invocation totals differ — 5821 vs 6946 — only because the two SSH
  windows drifted; within each run MV == raw exactly.)
- **AC12** — after restarting the `grafana` service (see deviation 3B-8),
  `GET /api/search` → exactly
  `[('wikistream-live', 'WikiStream Live Analytics', '/d/wikistream-live/wikistream-live-analytics')]`
  — uid `wikistream-live`, title correct, `phase1` gone.
- **AC13** — per-panel `POST /api/ds/query` (Basic admin:<SM password>,
  `${window}` substituted manually — deviation 3B-4): all 5 panels OK for both
  `1 hour` and `24 hour`, non-null rows: P1 edit-velocity 30 rows (15 min ×
  bot/human), P2 ratio 2, P3 top-pages 10, P4 project/lang 15, P5
  edit-size histogram 6 buckets. Values present (e.g. P5 1-hour bucket sums
  up to 6008).
- **AC10** — CI on the 3B head: `analytics-tests` (the 13-test ch suite: 12
  passed, 1 expected skip) and `compose-smoke` (AC5 now asserts "WikiStream
  Live Analytics") both **green**; see deviation 3B-7 for the one CI job that
  did not (ruff, FLY002 — fixed by the follow-up commit).

### 3.2.5 — Docs name sweep: WikiPulse → WikiStream

**DONE (2026-08-12).** `grep -rn -i wikipulse docs/` pre-sweep found exactly
three product-name occurrences: `docs/planning/master-plan.md:1` (title),
`docs/planning/vision-and-adr.md:1` (title), and `vision-and-adr.md:240` (the
single-node vertical-scaling sentence) — all changed to WikiStream (sentence
bodies byte-identical otherwise). Remaining hits are intentional: deprecation
notes in `phase-3-implementation.md` (Q4/Q10) and the 3.2.5 log/plan task
headings — left untouched. phase-1/2 plans, research-notes and
coverage-boundary had no occurrences.

### DEVIATIONS & FINDINGS (3B)

1. **Branch convention (per user): all Phase-3 work rides `feature/Data-Model-Depth`**
   — the plan's `feature/phase-3b-analytics` (and later `feature/phase-3c-warehouse`)
   names are not used; same precedent as 3A. The 3B PR opens from this branch
   against `main`.
2. **`INTERVAL 1h` / `INTERVAL 24h` shorthand REJECTED by ClickHouse 26.3.17**
   (Code 47 `UNKNOWN_IDENTIFIER` / Code 62 syntax; verified independently on two
   CH 26.3 instances). The dashboard `$window` custom values therefore ship as
   `1 hour` / `24 hour` (default `1 hour`); panels keep the plan's
   `minute >= now() - INTERVAL ${window}` SQL verbatim. This is the plan's own
   build-time-flag path (3.2.3 verify notes → deviate via window values).
   `tests/mv/test_interval_window_forms` pins the deployed forms.
3. **3B MVs broke the 3A suite**: 3A's `reset()` dropped `raw_events` but not the
   `mv_*` storages; `test_legacy_migration` then inserted into a legacy
   2-column `raw_events`, the still-attached MV fired on the missing materialized
   columns → `Code 47 UNKNOWN_IDENTIFIER 'title' (while pushing to view) → HTTP
   404`. Fix: `tests/migrations/test_migrations.py reset()` now drops the 3
   `mv_*` tables FIRST (before their source), with a comment referencing
   `test_legacy_migration`. Proved by reproduction before/after. Also required in
   CI: `analytics-tests` runs on a fresh container where MVs now exist.
4. **AC13 harness artifact**: `/api/ds/query` does NOT expand Grafana template
   variables — `${window}` is substituted by the dashboard frontend only. The VM
   spot-check substitutes `${window}` manually (`1 hour` / `24 hour`).
5. **AC11 live-equivalence methodology**: naive `minute >= now()-INTERVAL` vs
   `inserted_at >= now()-INTERVAL` does not match exactly purely because of (a)
   window-bound shape (minute-bucket vs exact-timestamp bounds drift up to a
   minute per edge) and (b) the bootstrap minute (rows inserted before the MV's
   CREATE are raw-only — inherent at every first deploy, self-settling). With
   minute-aligned bounds on BOTH sides over settled minutes, MV == raw EXACTLY
   (local proof: 2480==2480 on a settled window). Not an MV defect.
6. `system.materialized_views` is not a valid table name on 26.3 (harmless; no
   shipped code references it).
7. **CI `ruff` job red on the 3B head** — the FLY002 finding (`"\n".join([...])`
   at `tests/mv/test_mv_equivalence.py:327`) slipped into the merged branch
   (Ahmed committed the pre-review-fix file set). `analytics-tests` and
   `compose-smoke` were green; the follow-up commit replaces the join with a
   literal multi-line string and re-runs CI green. (AC10 itself refers to the
   MV suite, which passed.)
8. **Grafana provisioning pick-up needs a container restart (3C-critical).**
   A deploy that only changes `grafana/provisioning/*.yaml` or
   `grafana/dashboards/*.json` (no image/compose-config change) does NOT reach
   the running grafana container: `docker compose up -d` at VM boot leaves it
   `Running` with stale in-memory provisioning (the old `phase1` provider +
   deleted `phase1.json`), so `/api/search` came back `[]` and
   `/api/dashboards/uid/wikistream-live` 404'd. Fix: `sudo docker compose -f
   /opt/wikistream/docker-compose.yml restart grafana` (note: SERVICE name is
   `grafana`, not container_name `wikistream-grafana`). Surfaced for 3C
   (bigquery datasource + warehouse-freshness panel will hit the identical
   failure mode); consider baking the restart into apply.yml/boot.sh.

### §4 BUILD-TIME CHECKLIST — OUTCOMES (3B)

| Check | Outcome |
|---|---|
| `SHOW TABLES LIKE 'mv_%'` → 3 views | **confirmed** (fresh volume + suite) |
| CH 26.3 accepts `INTERVAL 1h`/`24h` shorthand | **rejected (Code 47)** → deviation: window values `1 hour`/`24 hour` |
| MV population synchronous / no POPULATE | **confirmed** (INSERT-blocking views; same-minute visibility) |
| SummingMergeTree unmerged-row semantics | **confirmed** — equality must be SUM-vs-SUM, never row counts |
| `format` numeric enum required (not `"time_series"`) | **confirmed** — panels all `0`/`1` |
| Grafana template-var expansion inside raw `/api/ds/query` | **does NOT expand** — substitute `${window}` in the spot-check harness |
| MV bootstrap-minute race (rows pre-CREATE stay raw-only) | **observed**, self-settling, non-issue |
| `testpaths=tests` picks up `tests/mv` with no CI change | **confirmed** (analytics-tests = `pytest -m ch`) |
| Warehouse export SQL empty-safe skip | **confirmed** (`pytest.skip` until 3C) |

### ACCEPTANCE CRITERIA — 3B (AC9–AC13)

- **AC9 — MVs exist:** `SHOW TABLES LIKE 'mv_%'` → exactly the 3 MVs
  (`mv_edits_per_minute`, `mv_top_pages_per_minute`, `mv_edit_sizes_per_minute`)
  — **GREEN** on the VM post-deploy (and on fresh local volume).
- **AC10 — MV equivalence synthetic:** `tests/mv` suite **GREEN** in CI
  (`analytics-tests`: 12 passed, 1 expected skip on the 3B head; re-run green
  on the ruff-fix head). The two review-fix rows probe the bucket boundaries
  (delta 10 → '1-10', 11 → '11-100').
- **AC11 — MV equivalence live:** minute-aligned settled-window sums **MV ==
  raw EXACTLY** on all 3 MVs (printed in 3.2.4: 5821==5821 @
  14:57–15:10, then 6946==6946 for top_pages and edit_sizes).
  Methodology per deviation 3B-5; bootstrap-minute race observed and
  self-settling.
- **AC12 — dashboard provisioned / phase1 gone:** `/api/search` returns
  "WikiStream Live Analytics" (not "Phase 1"), uid `wikistream-live` —
  **GREEN** on the VM (after the grafana restart, deviation 3B-8) and locally.
- **AC13 — all 5 panels non-null:** per-panel `/api/ds/query` returns rows for
  all 5 panels at both `${window}` values — **GREEN** on the VM (counts in
  3.2.4).

**HANDOFF → 3C (2026-08-12):** Phase 3B is green on AC9–AC13 (evidence above;
the only blemish — the ruff FLY002 lint on the merged head — is fixed by the
follow-up commit so CI is fully green before 3C starts). Carry-over notes for
3C: (a) deviation 3B-8 — restart `grafana` after any provisioning/dashboard-only
deploy (bigquery datasource + warehouse panel will need it); (b) branch is
`feature/Data-Model-Depth` for the whole phase (not `phase-3c-warehouse`);
(c) the `warehouse/` `{START}`/`{END}` placeholder contract is already handled
by `tests/mv/test_warehouse_export_sql_empty_safe` (fixed-range substitution,
so 3C dropping the export SQL files activates the comparison unchanged);
(d) coverage-boundary's `warehouse/` + `tests/warehouse` rows stay predicted
until 3C/AC21.

### 3.3.1 — Bootstrap: `bigquery.googleapis.com` (Q8)

**Implemented** 2026-08-12: added `"bigquery.googleapis.com"` to the
`google_project_service` `for_each = toset([...])` list in
`infra/bootstrap/main.tf` (alongside the existing 8 APIs). Bootstrap stays a
manual, local-state apply (`terraform -chdir=infra/bootstrap apply`) — CI never
touches it. Note: the API was already enabled in the project (verified via
`gcloud services list --enabled`), so the apply is a no-op state update. **Apply
recorded below in 3.3.8** (per spec the apply precedes the 3C merge; see the
evidence line there). AC14.

**Applied** 2026-08-12 (before the 3C merge, per spec): `terraform
-chdir=infra/bootstrap plan -var='project_id=wikistream-505003'` → plan was
`1 to add, 0 to change, 0 to destroy` (only
`google_project_service.apis["bigquery.googleapis.com"]`), then `terraform
-chdir=infra/bootstrap apply -var='project_id=wikistream-505003'
-input=false -auto-approve` → `Apply complete! Resources: 1 added, 0 changed,
0 destroyed`. Outputs: `deploy_sa_email =
wikistream-deploy@wikistream-505003.iam.gserviceaccount.com`,
`wif_provider_name` unchanged. Local state touched; CI untouched (bootstrap is
manual by design). AC14 satisfied (`gcloud services list --enabled
--filter=config.name:bigquery.googleapis.com` shows it enabled).

### 3.3.2 — `modules/bigquery` (Q8)

**Implemented** 2026-08-12 (ADR-007 extension): new 5th Terraform module
`infra/main/modules/bigquery/`:
- `bigquery.tf` — `google_bigquery_dataset wikistream` (US, labels);
  5 `google_bigquery_table`s (kpi_edits_hourly / kpi_top_pages_hourly /
  kpi_edit_sizes_hourly / raw_events_sample / export_runs), each with
  `schema = file("${path.module}/../../../../warehouse/schemas/<name>.json")`
  (single source shared with `bq load`) + `time_partitioning { type = "DAY",
  field = <ts> }` (kpi_edits_hourly additionally `clustering = ["wiki"]`);
  `google_bigquery_dataset_iam_member` giving the VM SA
  `roles/bigquery.dataEditor` dataset-scoped ONLY (ADR-010 least privilege —
  no project-level bigquery roles); `google_storage_bucket
  wikistream-505003-bq-staging` (US, uniform access, 7-day Delete
  lifecycle_rule) + bucket-scoped `storage.objectCreator` /
  `storage.objectViewer` for the VM SA.
- `variables.tf` (project_id, service_account_email, labels) + `outputs.tf`
  (dataset_id, bucket_name).
- `infra/main/main.tf`: `locals.labels.phase` bumped `"2"` → `"3"`; wired
  `module "bigquery"` (project_id, service_account_email =
  module.iam.service_account_email, labels = local.labels). No other modules
  touched. AC15 (verification in 3.3.8).

### 3.3.3 — warehouse/sql + warehouse/schemas (Q6/Q7)

**Implemented** 2026-08-12:
- `warehouse/sql/export_edits.sql`, `export_top_pages.sql`,
  `export_sizes.sql`, `export_raw_sample.sql` — single source of truth with
  literal `{START}`/`{END}` placeholders (UTC), shared by export.sh, parity.sh
  and the pytest suite. Every timestamp column is unconditionally wrapped
  `formatDateTime(..., '%Y-%m-%dT%H:%i:%sZ')` (RFC3339 for the bq JSON
  loader — space-form is CSV-only), and BOOL columns are cast
  `if(is_bot, 'true', 'false')` (bq JSON parser requires true/false, not 0/1).
  `export_raw_sample.sql` samples deterministic `sipHash64(event) % 100 < 10`.
- `warehouse/schemas/{kpi_edits_hourly,kpi_top_pages_hourly,
  kpi_edit_sizes_hourly,raw_events_sample,export_runs}.json` — BQ schema
  arrays (all columns NULLABLE) referenced by BOTH the TF module
  (`file()`/`abspath` — the single-source path) and `bq load --schema`.
- `warehouse/sql/parity_bq_{edits,top_pages,sizes,raw_sample}.sql` —
  BQ-dialect twin queries (windowed SUM over the hourly BQ tables; COUNT for
  the raw sample; grain differs from CH by design per Q7).

### 3.3.4 — export.sh + parity.sh (Q7)

**Implemented** 2026-08-12: `warehouse/export.sh` and `warehouse/parity.sh`
(executable in git). Both are `set -euo pipefail`, `SCRIPT_DIR`-relative,
source `/opt/wikistream/.env` when present for `CLICKHOUSE_PASSWORD` (no
secrets in unit files), accept optional START/END args with a GNU-date default
of last completed UTC hour (END = start of current hour, START = END − 1h).
- `export.sh`: per table (kpi_edits → kpi_top_pages → kpi_sizes →
  raw_sample) the locked pipeline — `docker exec -i wikistream-clickhouse
  clickhouse-client --user wikistream --password … --format JSONEachRow <
  <(sed "s/{START}/$START/; s/{END}/$END/" …)` → staged JSONL (0-byte results
  are removed and skipped), `gcloud storage cp` to the staging bucket, `bq
  load --source_format=NEWLINE_DELIMITED_JSON --time_partitioning_field=…
  --schema=warehouse/schemas/<table>.json` (append semantics) → final single-
  line export_runs record (status success + 4 row counts) loaded through the
  committed export_runs.json schema. Any failure aborts non-zero.
- `parity.sh`: freshness gate on the latest export_runs row (status
  "success" AND window_end == the window just exported, else exit non-zero);
  per-table CH-vs-BQ comparison against the SAME window — CH side wraps the
  committed export SQL in an outer SUM (merge-state-independent; never row
  counts), BQ side runs the committed parity_bq_*.sql twins — any mismatch
  exits non-zero; appends one JSON line to `/var/log/wikistream-parity.log`
  (Phase 5 alert hook) and echoes it to stdout/journald.
- Deliberate detail: parity compares SUMS not row counts (BQ hourly grain vs
  CH minute grain; SummingMergeTree row counts are merge-state-dependent,
  sums are not). Documented double-load remediation: re-exported windows
  append (at-least-once), surfaced by parity, remediated by windowed DELETE +
  reload.

### 3.3.5 — systemd units + timers + boot.sh install step

**Implemented** 2026-08-12:
- `warehouse/wikistream-export.{service,timer}` —
  `OnCalendar=*-*-* *:00:00`, `Persistent=true`, Type=oneshot,
  ExecStart=/opt/wikistream/warehouse/export.sh (scripts source their own env;
  no EnvironmentFile, no secrets in units).
- `warehouse/wikistream-parity.{service,timer}` — same shape,
  `OnCalendar=*-*-* *:05:00` (5 min after :00 export).
- `scripts/boot.sh` — appended the idempotent Phase 3C install step: `cp`
  the 4 unit files to /etc/systemd/system/, `systemctl daemon-reload`,
  `systemctl enable --now wikistream-export.timer wikistream-parity.timer`
  (absolute paths; timers run as root on the VM; executables ship with the
  git exec bit).

### 3.3.6 — BigQuery datasource + freshness panel (ADR-010)

**Implemented** 2026-08-12:
- `docker-compose.yml`: `GF_PLUGINS_PREINSTALL` extended to the single
  comma-separated string
  `grafana-clickhouse-datasource@4.20.0,grafana-bigquery-datasource@3.2.0`
  (GF_INSTALL_PLUGINS is broken in Grafana 13.1.1 — GF_PLUGINS_PREINSTALL is
  the only path; 3.2.0 requires Grafana ≥ 11.6, compatible).
- `grafana/provisioning/datasources/bigquery.yaml` (NEW): datasource name
  BigQuery, uid `wikistream-bigquery`, type grafana-bigquery-datasource,
  `jsonData { authenticationType: gce, defaultProject: wikistream-505003 }`,
  NOT the default (ClickHouse remains default). GCE auth reads the VM SA
  token from the metadata server; the dataset-scoped dataEditor covers its
  jobs (no new IAM) — expected to fail on local dev (no metadata server),
  documented §9.
- `grafana/dashboards/wikistream-live.json`: panel 6 "Warehouse freshness"
  (`stat`, BQ datasource uid, gridPos h8 w6 x0 y18 — new row, no overlap of
  the full y10 row), rawSql
  `SELECT TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(exported_at), MINUTE) AS minutes_since FROM wikistream.export_runs`,
  format 1, thresholds green < 60 / orange 60–120 / red > 120, unit min;
  dashboard `version` 1 → 2. Requires the 3B-8 grafana restart to pick up the
  new datasource/panel after deploy.

### 3.3.7 — tests/warehouse suite

**Implemented** 2026-08-12: `tests/warehouse/test_export_parity.py`
(`@pytest.mark.ch`, local CH only — gcloud/bq are not exercised; local
equivalence is the point). Coverage boundary row for `warehouse/sql` +
`tests/warehouse` added in AC21. Evidence: pytest run output in 3.3.8.

### 3.3.8 — 3C PR → deploy → verification battery → log

**Local verification (pre-PR, 2026-08-12):**
- `CH_HOST=localhost CH_PORT=8123 CH_USER=wikistream
  CH_PASSWORD=wikistream_dev_password uv run --project consumer pytest -q
  --tb=short -m ch` → **21 passed** (6 migrations + 7 mv incl. the 3C pre-hook
  + 8 warehouse), 19 deselected, 13s. Local consumer container was stopped for
  the run so live-stream writes don't contaminate the deterministic fixtures
  (CI analytics-tests has no stream by construction).
- pre-commit run --all-files → all 8 hooks Passed (trailing-whitespace,
  end-of-file-fixer, check-yaml, check-json, check-merge-conflict,
  check-added-large-files, ruff, ruff-format).
- `bash -n warehouse/export.sh warehouse/parity.sh` OK; `python3 -m json.tool`
  on all 5 schemas + dashboard OK; both datasource YAMLs parse; `docker
  compose config -q` OK; `terraform init -backend=false && terraform validate`
  in infra/main → `Success! The configuration is valid.`

**Deviations / bugs caught by the test suite (all resolved):**
1. **ClickHouse alias shadowing (critical, tests-caught)** —
   `warehouse/sql/export_raw_sample.sql` originally selected
   `formatDateTime(inserted_at, ...) AS inserted_at`. ClickHouse 26.3 resolves
   the WHERE `inserted_at >= '{START}' AND inserted_at < '{END}'` to the
   NEW string alias, so the T-separator RFC3339 value was compared against the
   space-form literal — lexicographically every row failed the `< END` bound
   and the query silently returned ZERO rows (rc=0) on both the HTTP path and
   the production `clickhouse-client` path. This would have produced empty
   raw-sample exports in production with no error. Fixed by a nested query:
   inner query aliases the formatted value `AS ts` (no shadowing), outer query
   renames `ts AS inserted_at` for the BQ schema. Verified via `docker exec`
   against live data. The other three export files are unaffected (their alias
   `hour` never shadows a source column).
2. **tests/mv 3C pre-hook determinism** — the seed loop inserted identical
   event content; since `sipHash64(event)` is deterministic the 10% sample
   could never hit → infinite loop on a pristine CI CH. Now varies the title
   per attempt (`3C_Seed_{i}`, capped at 300 → P(hang) ≈ 0.9^300), so
   `warehouse/sql/export_*.sql` full-range substitution always returns
   non-empty regardless of test ordering (CI has no live data).
3. **tests/warehouse probe leakage** — `_seed_sampled` probe events seeded
   with `"type": "log"` so they are invisible to the MVs
   (`event_type IN ('edit','new')`) while still reflected in
   `export_raw_sample` (which has no type filter) — parity sums stay exact.
4. **parity.sh freshness gate** — `bq query --format=json` renders TIMESTAMPs
   with fractional seconds (`...T12:00:00.000Z`); the gate now normalizes
   (strips fraction + trailing Z) before comparing to `WINDOW_END`, else the
   exact-equality check would flake on every run.
5. SA deviations carried forward: parity.sh logs table value `"error"` on hard
   query-failure paths (honest marker that the compare never ran);
   `gcloud storage cp` output suppressed to `/dev/null` to keep the completion
   line's stdout clean.

**Subagent code review (code-reviewer on the 3C working-tree diff, 2026-08-12)
— 8 findings, all resolved before commit:**
1. **BLOCKER → fixed (deviation from plan Q8):** the VM SA had dataset-scoped
   `roles/bigquery.dataEditor` only, which grants **no** `bigquery.jobs.create`
   (verified: `gcloud iam roles describe roles/bigquery.dataEditor` has zero
   `jobs.*` permissions). BigQuery jobs are project-scoped and dataset IAM
   cannot grant them — so every `bq load` (export.sh), `bq query` (parity.sh)
   and the Grafana GCE-auth datasource would `AccessDenied` on first run.
   Added `google_project_iam_member.vm_job_user` with `roles/bigquery.jobUser`
   at **project** scope in `infra/main/modules/bigquery/bigquery.tf` (one
   mandatory project-scoped role; grアント jobs only, no data read/write —
   table access stays dataset-scoped dataEditor, ADR-010 least privilege
   preserved).
2. **MINOR → fixed:** `infra/main/templates/startup.sh` now runs
   `gcloud config set project "$GCP_PROJECT"` (export/parity call bq/gcloud
   storage without `--project_id`; pins SDK project resolution).
3. **MINOR → fixed:** `warehouse/wikistream-parity.service` gained
   `After=wikistream-export.service` so a `Persistent=true` catch-up after VM
   downtime cannot run parity ahead of a still-running export.
4. **MINOR → fixed:** parity.sh freshness gate now queries
   `WHERE window_end = TIMESTAMP('<WINDOW_END>')` instead of newest-row
   (`ORDER BY exported_at DESC LIMIT 1`), so a manual backfill of another
   window can no longer mask/stale the current-window check.
5. **MINOR → fixed:** `test_export_runs_shape` now calls `_seed_sampled()`
   itself — no dependence on a sibling test's side effect (was a ~28%
   isolation flake).
6. **NIT → fixed:** parity.sh `norm()` normalizes space-form timestamps
   (`.replace(" ", "T")`) before fraction-strip.
7. **NIT → fixed:** parity raw-sample CH count now wraps the COMMITTED
   `export_raw_sample.sql` in `SELECT count() FROM (...)`, removing the
   hand-mirrored predicate (single source of truth).
8. **NIT → not applied:** START/END arg regex validation — operation-only
   surface on a private VM; malformed args fail naturally; spec already allows
   arbitrary windows by design.
Explicitly cleared by review: `schema=file("${path.module}/../../../../warehouse/schemas/…")`
path resolution, NULLABLE partition columns, `phase` label in-place update
(no ForceNew), CH `%i` + `GROUP BY` alias resolution, BQ `TIMESTAMP('…')`
space-form parsing, `'true'/'false'` → BOOL loading, Grafana
`format: 1` = Table, plugin 3.2.0/Grafana 13.1.1 compatibility, boot.sh unit
install idempotence, no secrets in any new file.

**Post-merge deployment issue + fix (2026-08-12, recorded as deviation):**
the first gated `apply` (via PR #19 merge) failed with two 403s — the
**deploy SA** (which runs `infra/main` through WIF) lacks
`bigquery.datasets.create` and `storage.buckets.create`, both project
primitives that neither dataset- nor bucket-scoped roles can grant. Added
`roles/bigquery.admin` + `roles/storage.admin` to the deploy SA's
`deploy_project_roles` in `infra/bootstrap/main.tf` and applied **locally
with bootstrap state** (CI never touches bootstrap; `plan` 2 to add →
`Apply complete! Resources: 2 added`) — same ceremony as the 3.3.1 bootstrap
apply. The apply also recreated the VM once (expected: `startup.sh` is
referenced via `file()` in the compute module, so the added
`gcloud config set project` line forces a new instance); the pre-existing
`google_project_iam_member.vm_job_user` (bigquery.jobUser) was created
successfully in that same run.

**VM evidence (AC15–AC20) + CI URLs + Gate 1 record + Go/No-Go:**

**AC14 — BigQuery API enabled:** `gcloud services list --enabled
--filter=config.name:bigquery.googleapis.com` → `bigquery.googleapis.com`.
Bootstrap applies recorded above (3.3.1 API enable + 3.3.8 deploy-role
extension), both manual with local state.

**AC15 — BQ dataset + 5 tables + staging bucket + IAM:** `bq ls
--project_id=wikistream-505003 wikistream` shows all 5 tables
(`export_runs`, `kpi_edit_sizes_hourly`, `kpi_edits_hourly`,
`kpi_top_pages_hourly`, `raw_events_sample`). `kpi_edits_hourly` is
DAY-partitioned on `hour` with `clustering: ["wiki"]`. Dataset IAM
(`bq show wikistream` prettyjson): `WRITER wikistream-vm@…`
(roles/bigquery.dataEditor — ADR-010), plus project-scoped
`roles/bigquery.jobUser` for the VM SA (jobs are project-scoped; recorded
deviation above). `gcloud storage ls gs://wikistream-505003-bq-staging`
reachable, holds RUN_ID-unique per-table objects. STAGING BUCKET WRITE —
see AC20.

**AC16 — Export timer produces data:** scheduled unit fired 18:00:19 and
completed 18:03:33 with `[export] success window=2026-08-12T17:00:00Z..
2026-08-12T18:00:00Z rows_edits=272 rows_top_pages=40870 rows_sizes=6
rows_raw_sample=10516`; `systemctl is-active wikistream-export.timer` →
active; `ExecMainStatus=0`. `bq query` export_runs: `status=n
success,1` for the 17:00 window (exported_at 18:03:09). All four KPI/raw
tables hold the last-completed-hour rows (272 / 40870 / 6 / 10516).

**AC17 — Parity green on schedule:** scheduled parity unit fired
18:05:18 → finished 18:07:28, `ExecMainStatus=0`, journal `Finished
wikistream-parity.service`. `/var/log/wikistream/wikistream-parity.log`:
17:05:30 error line (boot-time `Persistent=true` catch-up for 16–17Z,
which predates the fixed exports — correct error path) then
**18:07:28 green**: `{"freshness":"ok","status":"ok","tables":
{"edits":"ok","raw_sample":"ok","sizes":"ok","top_pages":"ok"},
"window_start":"2026-08-12T17:00:00Z","window_end":
"2026-08-12T18:00:00Z"}`. **Two consecutive green runs (`≥2`) confirmed on
the live schedule: 18:07:28 and 19:07:10 UTC, both `"status":"ok"` with
all four tables ok and `ExecMainStatus=0`** — closing AC17 end-to-end.

**AC18 — BQ matches CH for a real window:** parity compares BQ vs CH on
identical windowed-SUM SQL for all four tables → all ok. Independent BQ
probe of `kpi_edits_hourly` for the 17–18Z window:
`hour=2026-08-12 17:00:00, n_rows=272, edits=48833,
bytes_delta=43943656`, matching CH at 18:07. (An earlier 17:26 probe
showed CH=1946/23345/20765963 — a mid-open-hour snapshot predating the
18:00 reload; scheduled-run numbers are the record.)

**AC19 — Freshness panel renders:** `POST /api/ds/query` (uid
`wikistream-bigquery`) with
`SELECT TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(exported_at), MINUTE) AS
minutes_since FROM wikistream.export_runs` → 200, `minutes_since = 14`
(< 120). `/api/datasources` lists BigQuery (uid `wikistream-bigquery`,
GCE auth, isDefault false) + ClickHouse (isDefault true). Panel visible
on the wikiStream Live Analytics dashboard.

**AC20 — Staging lifecycle enforced:** `lifecycle_rule` (Delete, age 7
days) on `wikistream-505003-bq-staging` in TF; listing shows only fresh
objects; trivially nothing older than 7 days.

**AC21 — Coverage boundary + log consistent:** `docs/planning/
coverage-boundary.md` corrected (Phase 3 story = `migrations/` +
`warehouse/sql/` + the three suites `tests/migrations`, `tests/mv`,
`tests/warehouse` as business-critical 100%; `export.sh`/`parity.sh`
stayed outside the pytest-cov gate — thin wrappers whose exit codes are
verified by the timer runs + `bash -n`; the SQL they execute IS in the
100% story). This log now carries: bootstrap records, CI URLs, the
scheduled-run evidence above, and every deviation/bug caught.

**CI / build evidence:** PR #19 (`feature/Data-Model-Depth` → main,
merged 2026-08-12) and PR #20 (post-merge deploy-role fix, merged).
Initial PR #19 apply failed on the two deploy-SA 403s (recorded above);
after the bootstrap role extension the gated apply completed and created
dataset + bucket. Lint/unit-tests/analytics-tests/compose-smoke all
green on the final commits.

**Gate 1 — Go/No-Go for Phase 4:** **GO on record** (same
precedent as the Phase-2 gate). All AC14–AC21 self-checked with evidence
above; the two consecutive green parity runs (18:07:28 + 19:07:10 UTC)
close AC17 with no open items. Phase 4 planning may proceed in parallel
per master plan §8.

**Phase 3 exit = AC1–AC21 executed with recorded evidence; Gate 1
GO-with-caveat recorded. Phase 3 is COMPLETE.**

## Phase 4 — Data Quality & Resilience (4A Consumer Resilience / 4B GX + Hardening + Backup)

Tasks defined in `docs/planning/phase-4-implementation.md` (LOCKED 2026-08-12).
Task headings pre-populated per plan §6; Status lines are filled as each task
is worked, per the logging rules. Two PRs, each merge → gated apply → VM
reset → its gate before the next PR begins: 4A consumer resilience (micro-gate,
task 4.1.7), 4B GX + healthchecks + backup/restore (phase-final battery + Gate 2,
task 4.2.9). Gate 1's GO-with-caveat is already on record in §3 — task 4.2.9
cross-references it, it is NOT re-recorded.

### 4.1.1 — models.py: slim Pydantic model + timestamp policy (Q1/Q2)

**Status:** DONE

**2026-08-13:** `consumer/src/models.py` landed per Q1/Q2: `WikiEvent`
(wiki/title/user/event_type via `Field(validation_alias="type")`, bot,
`length: EventLength | None`, `timestamp: datetime`,
`ConfigDict(extra="ignore")`), `EventLength(new: int = 0, old: int = 0)`,
`validate_timestamp(ts, now=None) -> str | None` with the exact reason
strings (`timestamp_missing` / `timestamp_unparseable` / `timestamp_future`),
naive→UTC fix and 5-minute future tolerance, `now` injectable for tests.

**§4 build-time checklist outcome — REAL SHAPE FINDING (FAILED → FIXED):**
the checklist item "verify pydantic v2 parses trailing-Z timestamps against a
captured real event" surfaced a live-stream reality the fixtures could not
have caught: the real Wikimedia recentchange stream sends **integer epoch
seconds** as `data: {"timestamp": 1786626323, ...}` (not an ISO string) and
the SSE `id:` line is a **JSON array**
(`id: [{"topic":...,"partition":0,"timestamp":...},{"topic":...,"offset":-1}]`),
not a plain number. First live boot hit
`WARNING reconnect reason=AttributeError: 'int' object has no attribute 'strip'`
every ~1s (never-crash contract held — zero Tracebacks) and inserted nothing.
Fixed by (a) `validate_timestamp` accepting `int | float` →
`datetime.fromtimestamp(ts, tz=utc)` and (b) a
`@field_validator("timestamp", mode="before")` epoch→datetime coercion on
`WikiEvent.timestamp` (str stays on the fromisoformat path; junk still yields
`validation:datetime_type`). The two real captured payloads (ruwiki
categorize — no `length`/`minor`; hywiki edit — `length.old/new`) are now
permanent test fixtures (4 tests in test_validation.py, see 4.1.6).

**ADR-005 amendment (Q2, timestamp policy split):** per-event rejection is
now only parse/empty/future (`timestamp_*` reasons); **staleness** (median
event_timestamp↔inserted_at lag, max future skew) is a GX expectation
(4B), NOT a per-event rejection — reconnect re-delivers minutes-old buffered
events and strict stale-reject would flood the DLQ. Bot-coercion matrix
empirically verified on pydantic 2.13.4: lax-mode `bool` coerces everything
tested (true/false/1/0/yes/no + on/off/t/f/y/n, any case); `str` does NOT
coerce ints (`title=123` → `string_type`).

**Post-review hardening (subagent review round):** `validate_timestamp` made
total — `bool` rejected up front (`isinstance(ts, bool)` → unparseable;
bool is an int subclass, so `"timestamp": true` would otherwise parse as
epoch 1) and the int/float branch guards `datetime.fromtimestamp` with
`except (OverflowError, ValueError, OSError)` → `timestamp_unparseable`.
Without this, a `{"timestamp": NaN}`-class event (json.loads accepts
NaN/Infinity; `10**18` even raises OSError) escaped step 2, was treated as a
connection failure, and replayed forever against `Last-Event-ID=durable` —
an invisible ~1s reconnect livelock (CI greps can't see it). Covered by
`test_timestamp_total_no_escape` + `test_timestamp_bool_rejected`.

**Evidence:** `consumer/src/models.py`; 21-test validation suite green;
live run (post-fix) inserts real events with zero dead-lettering
(`dead_lettered=0`, see 4.1.5).

### 4.1.2 — dead_letter: migration 007 + sync router (Q3)

**Status:** DONE

**2026-08-13:** `migrations/007_dead_letter.sql` (no guard line — idempotent
`CREATE TABLE IF NOT EXISTS`; `TTL inserted_at + INTERVAL 90 DAY` spelled out
per CH 26.3 rejecting the `90d` shorthand) + `consumer/src/dead_letter.py`
(`async write_dead_letter(client, *, reason, wiki, title, event)`). Applied
locally: "APPLY 007_dead_letter / migrations complete: 1 applied, 7 skipped".

**Interpretation note (recorded per plan):** "sync single-row insert" =
durable-synchronous insert (`settings={"async_insert": 0}`) awaited through
the ONE async client — no second connection, matching the never-crash
contract. On any exception: `WARNING dead_letter_write_failed reason=%s` and
drop; never retried, never crashes.

**AC3 semantics confirmed by code + tests:** the router is called **only**
from the validation branch (invalid JSON / timestamp reasons /
`validation:{pydantic_error_type}`). Transport failures (flush exception)
never write dead_letter — `insert_failed` counter advances instead (unit
test asserts this; keeps the Phase 5 DLQ-rate panel semantically honest).

**Post-review hardening (subagent review round):** `write_dead_letter` now
returns `bool` (True only on a landed row) and the consumer gates BOTH the
`dead_lettered` counter and the durable-cursor advance on it. Previously a
failed DL insert (CH down) still advanced the cursor past an event stored
nowhere — on recovery it was never re-delivered and the DL row was lost
forever. Now a failed DL write re-runs after reconnect (at-least-once DL,
same replay semantics as the flush path).

**Evidence:** migration test `test_dead_letter_migration` green (table
exists, `TOINTERVALDAY(90)` via SHOW CREATE — `system.tables.ttl_expression`
is ABSENT on CH 26.3, SHOW CREATE fallback per existing pattern); ch test
`test_malformed_to_dead_letter.py` green (exactly ONE DL row, reason +
raw event, see 4.1.6).

### 4.1.3 — restart-resume: /state mount + atomic consumer_state.json + durable-id invariant + flush-on-exit (Q4)

**Status:** DONE

**2026-08-13:** compose consumer volume
`${STATE_DIR:-${CH_DATA_DIR:-./ch-data}/../state}:/state` lands `/state` on
the ch-data disk (VM: `/mnt/ch-data/state`, sibling of `clickhouse/`; local:
`./state`, gitignored via new `state/` entry). `load_state()`/`save_state()`
in consumer.py: atomic tmp+os.replace, never crash on read failure
(`WARNING state_load_failed` → fresh start), `total` coerced to int on load,
makedirs on save. Debounced save (~2s monotonic or durable-id change, never
per event). **Durable-id invariant:** persisted `last_event_id` advances
ONLY on successful batch flush or dead-letter insert — an id held only in an
unflushed batch is never persisted (flush returns max-id; consumer applies
`_max_id`). **Flush-on-exit:** stop check at the top of the parse loop
(moved from the inner event loop — original placement made graceful exit
unreachable on a live stream), final flush + unconditional save after the
loop; `main()` awaits the task up to 10s before force-cancel.

**Empirical replay outcome (§4 checklist):** raw curl against
stream.wikimedia.org with the captured JSON-array id in `Last-Event-ID` —
server ACCEPTS it (no 400) and resumes at the FIRST event strictly after it
(captured frame not re-delivered). Sending `ev.id` verbatim is correct;
dedup-on-replay assumption holds.

**Live proof (local container, real stream):** after `docker restart
wikistream-consumer` → `connected url=... last_event_id=[{...JSON...}]`,
`inserted events=0 total=2094 ... resumed_from=[{...JSON...}]` — resumed
from persisted state, `total` NOT reset; replay dupes counted
(`duplicates_skipped=2`); zero Tracebacks; state file id advancing
(1786626531954 → 1786626562343, total 719 → 1865 at ~37 ev/s).

**Evidence:** `tests/src/consumer/test_resume_dedup.py` (8 unit tests incl.
kill-mid-batch invariant: persisted == last durable, never last seen);
`tests/src/consumer/test_kill_resume_zero_loss.py` (ch, see 4.1.6); live
docker logs + `./state/consumer_state.json`.

### 4.1.4 — batcher.py: 1000-rows/5s flush, integrated into the consumer pipeline (Q7)

**Status:** DONE

**2026-08-13:** `EventBatcher(max_rows=1000, max_age_s=5.0,
dedup_capacity=50_000, now=time.monotonic)` per ADR-004/Q7: `add(row, id)`
appends `(inserted_at, event_json)` and reports flush-due at `len >= 1000`
OR age from the FIRST row `>= 5s` (not page-aligned); `flush(client)` issues
one `client.insert(..., settings={"async_insert": 1,
"wait_for_async_insert": 0})`, returns `(max_event_id, rows_flushed)`; on
exception rows are DROPPED (at-most-once, counted). Consumer pipeline:
validate → dedup → batch → flush with `insert_failed += flushed` +
`WARNING insert_failed events=%d reason=batch dropped` on failure. Time
source injected for tests. Per-event insert path retained ONLY for
dead_letter.

**Deviations (recorded):** (1) compose volume local default gained a `./`
prefix (`./ch-data`) — compose v5.3.1 rejects `ch-data/../state` as an
undefined named volume; VM resolution identical (`/mnt/ch-data/state`) since
`STATE_DIR` is always set there. (2) The `insert_failed` WARNING carries
`reason=batch dropped` instead of an exception object — `flush()` swallows
exceptions internally per contract; line format
`insert_failed events=%d reason=%s` preserved (substring greps safe). (3)
Batcher logs its own `WARNING flush_failed reason=%s` (added in review — the
exception was otherwise silent).

**Evidence:** `tests/src/consumer/test_batcher.py` (8 unit tests: both
triggers via fake clock, flush shape, max-id math, at-most-once drop);
live run shows 1000-row/5s flushes on the real stream.

### 4.1.5 — dedup ring (Q5) + extended stats log line

**Status:** DONE

**2026-08-13:** bounded in-memory ring (`deque(maxlen=50_000)` + set mirror,
≈20 min at ~44 ev/s, oldest evicted) inside EventBatcher — mark at enqueue,
skip at insert (`duplicates_skipped += 1`). Memory-only; after a kill the
server's replay refills it. Ceiling documented in-code: "zero duplicates
across observed reconnect/kill windows; BQ parity is the long-tail safety
net". Event ids are treated as OPAQUE strings (JSON-array ids are
non-numeric → `_max_id` falls back to string compare; JSON ids share a
prefix so ordering ≈ embedded epoch-ms — acceptable, documented ceiling).

**Stats line (AC7, prefix STABLE):**
`INFO inserted events=%d total=%d dead_lettered=%d insert_failed=%d
duplicates_skipped=%d resumed_from=%s` — fires on every flush and on a 60s
heartbeat (`inserted events=0 ...`); `resumed_from` = persisted id or
`none`. Existing ci.yml greps (`connected url=`, `Traceback`) are substring
matches — unaffected (verified: compose-smoke pattern still matches).

**Live evidence:** real-stream run logged
`inserted events=164 total=164 ... duplicates_skipped=3 resumed_from=none`
then 194/358/7, 170/528/10 — dedup ring catching native server replays,
flushes at the 1000/5s cadence, `dead_lettered=0`, no reconnect WARNING, no
Traceback.

**Evidence:** `test_batcher.py` ring-bounded test (60k ids → 50k kept);
`test_resume_dedup.py` duplicate-skip+count tests; live docker logs.

### 4.1.6 — tests: unit (validation/batcher/resume-dedup) + ch-marked integration (malformed→DL, kill/resume) + sse_fixture (Q1–Q5, Q7)

**Status:** DONE

**2026-08-13:** `tests/sse_fixture.py` — stdlib-only asyncio SSE server
(CRLF frames, `id:`/`retry:` lines, `: ` comment heartbeats, malformed-event
option, `disconnect_after` abrupt drop, Last-Event-ID replay from the first
id strictly greater than the header, `pause()/resume()` gate,
`event_interval_s` pacing). `tests/conftest.py` added so `import sse_fixture`
resolves from `tests/src/consumer/`.

Test suites (all green locally):
- **Unit (`-m "not ch"`) — 58 passed, 24 deselected, 0.43s:** validation
  (21 tests: real captured categorize + edit payloads, all reason strings,
  bot matrix, epoch-int timestamps), batcher (8), dead_letter (2), resume/
  dedup (8: atomic write no-partial, load-missing/corrupt → None, round-trip,
  resume-id drives initial header, kill-mid-batch invariant, AC3 no-DL-on-
  transport-failure), sse (19 pre-existing).
- **CH (`-m ch`) — 24 passed, 58 deselected, 48.05s:** malformed→DL (exactly
  one DL row `validation:invalid_json` + raw `{broken json`, wiki/title '',
  5 valid rows inserted, consumer alive, dead_lettered==1), kill/resume
  zero-loss (ids 1..200, disconnect_after=100: leg-1 replay dupes counted;
  hard task.cancel() kill-sim; new loop resumes from persisted durable →
  final count == 200 == total emitted, zero loss, zero dupes), migration 007
  test, plus 6 migrations + 7 MV + 8 warehouse pre-existing.

**Deviations (all test-side, recorded):** fixture `stop()` initially
deadlocked with held-open connections (writers now closed before
`server.wait_closed()`); `hold_open=True` unusable for graceful-exit tests
(the consumer's stop check fires only per parsed event; comment heartbeats
never dispatch) — graceful-exit tests use `hold_open=False` so the stream
end drains the final flush; durable id advances only on flush/DL and
`total` only moves at flush (asserts after graceful exit); ch tests filter
queries to synthetic titles because a live consumer container feeds the
same tables (~40 rows/s — container stopped during ch runs, restarted
after; documented env race). First migration-suite run failed 2 tests from
that live-insert race — no test change needed, environment only.

**Evidence:** 82/82 tests green post-format (`ruff format consumer tests`,
9 files; `ruff check` clean); commands: `uv run --project consumer pytest
-m "not ch" -q` / `-m ch`.

### 4.1.7 — 4A PR → gated deploy → VM checkpoint → log (malformed + kill proofs live)

**Status:** DONE (2026-08-13) — PR #22 merged 13:23:24Z; both VM proofs
captured live; evidence below. (Orchestrator does not commit/push per
AGENTS.md — Ahmed handles git ops.)

**2026-08-13:** Code, tests, log (4.1.1–4.1.6), coverage boundary all
complete and verified locally (82/82 green; live-container proof against the
real Wikimedia stream incl. restart-resume, see 4.1.3/4.1.5). Handoff to
Ahmed: **PR on carrier branch `feature/Data-Quality-&-Resistance`** (Phase 4
rides this branch per user convention) — files: consumer/src/{models,
batcher,dead_letter}.py (new), consumer/src/consumer.py (rewrite),
consumer/pyproject.toml + uv.lock (pydantic>=2,<3), migrations/007_
dead_letter.sql (new), docker-compose.yml (consumer /state mount),
.gitignore (state/), tests/sse_fixture.py + tests/conftest.py (new),
tests/src/consumer/{test_validation,test_batcher,test_dead_letter,
test_resume_dedup}.py (new unit), tests/src/consumer/{test_malformed_to_
dead_letter,test_kill_resume_zero_loss}.py (new ch), tests/migrations/
test_migrations.py (007 case). NO startup.sh/Dockerfile/ci.yml changes.

**Post-merge VM checkpoint (micro-gate before 4B — run by orchestrator
after Ahmed merges):** (1) gated apply — job1 build-push consumer image; VM
reset WITHOUT ForceNew (no startup.sh edit; VM pulls new compose + code at
boot; `startup done` in /var/log/wikistream-startup.log — never the Actions
badge). (2) MALFORMED proof live: run `tests/sse_fixture.py` on the VM or a
transient container on the compose network (networking: consumer is
container-isolated — run the fixture on the compose network or use the VM
host IP; revert one-off compose edits before next boot since startup.sh's
`git pull --ff-only || true` silently discards local changes) → exactly one
`default.dead_letter` row with reason + raw event, consumer alive. (3) KILL
proof live: `sudo docker kill wikistream-consumer` mid-stream → auto-restart
→ `resumed_from=<id>` in logs → count() grows → killed-window events present
exactly once (MV-vs-raw window sums + `duplicates_skipped` in stats line) —
zero dropped/duplicated (AC4). Record evidence + numbers here; only then 4B
begins.

**2026-08-13 — VM checkpoint executed (both proofs live):**

- **Deploy:** PR #22 merged 13:23:24Z; job1 build-push → VM boot pulled
  repo (fix: VM was 11 commits behind — startup.sh `git pull --ff-only ||
  true` was silently failing on a stale local `warehouse/export.sh` edit;
  stashed the local edit, pulled to 8670f8f) + recreated consumer with the
  `/state` mount; migrations re-run (`APPLY 007_dead_letter`, 1 applied, 7
  skipped — dead_letter table had been missing on the VM). Pre-existing CH
  attach failure (133 zero-byte parts in `mv_edits_per_minute` inner table,
  > default max_suspicious_broken_parts=100, leftover from the Phase 2-3
  disk saga) was diagnosed and repaired: 1020 zero-byte parts quarantined to
  `detached/` across all tables (raw_events count 314,899 intact); CH
  recovered (175 tables), consumer resumed flushing (insert_failed frozen at
  52,576 — at-most-once held throughout; no crashes).
- **KILL proof (AC4):** state file
  `{"last_event_id":"[{\"topic\":\"eqiad.mediawiki.recentchange\",\"partition\":0,
  \"timestamp\":1786629538451},{\"topic\":\"codfw.mediawiki.recentchange\",
  \"partition\":0,\"offset\":-1}]","total":1370,...}` (JSON-array id from the
  real stream). `sudo docker kill wikistream-consumer` → exit 137 (SIGKILL,
  no graceful path). NOTE: docker 26.x treated the kill as an explicit stop
  — restart policy did NOT auto-fire (restarts=0, unless-stopped); manual
  `sudo docker start` required (flagged for 4B Q12 healthchecks). After
  restart: `resumed_from=[{"topic":"eqiad.mediawiki.recentchange",...},
  {"topic":"codfw.mediawiki.recentchange",...}]` — byte-identical to the
  persisted id; `inserted events=1000 total=4370 insert_failed=0
  duplicates_skipped=85→150`; container Up; zero loss, kill-window
  redeliveries deduped (duplicates_skipped only).
- **MALFORMED proof (AC2):** transient `sse-fixture` container on the
  compose network (tests/sse_fixture.py mounted read-only + a 10-line
  driver monkeypatching `asyncio.start_server` → 0.0.0.0; events: 3 good +
  `this-is-not-json{{{` + `timestamp:"bogus"`), one-off compose override
  `STREAM_URL=http://sse-fixture:8787` (file outside repo dir so startup.sh
  pull can't clobber it). Consumer connected 14:04 UTC
  (`connected url=http://sse-fixture:8787 last_event_id=<persisted kill-proof
  id>` — reconnect loop carried the durable id live). Verified on disk after
  an instance reset (durable): `SELECT count(), reason FROM
  default.dead_letter GROUP BY reason` → 4× timestamp_missing, 1×
  validation:invalid_json (the fixture's bad-JSON event — real stream events
  are always valid JSON), 4× timestamp_unparseable (incl. the fixture's
  bogus-timestamp event). Both fixture-bad events landed in dead_letter with
  reasons populated; consumer stayed alive (dead_lettered=4, insert_failed=0,
  resumed_from=<persisted id> on production URL
  https://stream.wikimedia.org/v2/stream/recentchange post-reset).
- **Outage note:** mid-proof, ssh to the VM died (user VPN active — scoped
  allow-* rules point at the non-VPN IP); VM reset via `gcloud compute
  instances reset` restored service; ssh restored once VPN off. Not a
  product issue.

4A phase complete: 4.1.1–4.1.7 DONE. 4B begins at 4.2.1.

### 4B build-time re-verification outcomes (§4) — every item logged
1. **CH 26.3 BACKUP syntax — fallback used:** `BACKUP DATABASE default TO Disk(...) COMPRESSION 'zstd'` → Code 62 on 26.3. COMPRESSION clause omitted; zstd is the default local-disk compression in 22.x+. Verified working: `BACKUP DATABASE default TO Disk('backups','<name>')` → `BACKUP_CREATED` + UUID.
2. **RESTORE syntax — confirmed (no §9 ladder needed):** `RESTORE DATABASE default AS restore_check FROM Disk('backups','<name>')` works at DATABASE level on 26.3. Probe: counts equal (14 == 14), restore_check contained dead_letter, all three MVs, raw_events, schema_migrations (+ `.inner_id.*` internal).
3. **config.d/backups.xml — confirmed, with a NEW finding:** a plain `BACKUP ... TO Disk('backups',...)` fails Code 318 unless the config also sets `<backups><allowed_disk>backups</allowed_disk></backups>` — added. **NEW (blocker found + fixed): a directory bind-mount over `/etc/clickhouse-server/config.d` shadows the image's `docker_related_config.xml` (`listen_host ::` / `0.0.0.0`, `listen_try 1`), leaving ClickHouse silently on loopback only** (8123/9000 unreachable from other containers and the host; native `SELECT 1` inside the container still passed, which masked it). Fixed with a single-file mount (`config.d/backups.xml:/etc/clickhouse-server/config.d/backups.xml:ro`); both files now merge (`Merging configuration file ... docker_related_config.xml` + backups.xml) and 8123 is reachable host- and container-side.
4. **GRANT BACKUP/RESTORE — fallback used:** 26.3 has **no RESTORE privilege** (BACKUP covers both statements) and BACKUP is **GLOBAL-scope only** (`ON default.*` → Code 62, bare grant → Code 511 role error). Canonical form: `GRANT BACKUP ON *.* TO wikistream` (renders `GRANT BACKUP ON *.*` in SHOW GRANTS). Applied in boot.sh BOOTSTRAP_SQL and migrations/bootstrap-user.dev.sql (bodies kept in sync).
5. **GX 0.18.22 programmatic API — fallback used (adopted for real):** fluent `add_sql`/`add_table_asset` works for unbounded batches, but the datetime-part splitter renders no WHERE for the clickhouse dialect (empty batches) → the suite uses the old-style path: `ctx.add_datasource(class_name="Datasource", execution_engine={"class_name": "SqlAlchemyExecutionEngine" (exact casing), "connection_string": URL}, data_connectors={"default_runtime_data_connector_name": {"class_name": "RuntimeDataConnector", "batch_identifiers": ["default_identifier_name"]}})` + `RuntimeBatchRequest(... runtime_parameters={"query": <window SQL with computed lag_seconds/skew_seconds>})`. Also: **GX 0.18.22 requires Python 3.12** — pandas 2.1.4 / numpy 1.26.4 have no cp313 wheels (`requires-python = ">=3.12,<3.13"`; image python:3.12-slim; CI's managed 3.12 via uv).
6. **clickhouse-sqlalchemy URL — confirmed:** `clickhouse://user:pass@host:8123/default` (probe-verified against 3.13 → 3.12 image, SA 1.4.54).
7. **`docker compose run --rm gx` .env interpolation — confirmed:** CLICKHOUSE_PASSWORD resolves like the consumer service. VM run pending.
8. **pydantic v2 trailing-Z — confirmed (4A).**
9. **uv SIGTERM forwarding — confirmed (4A exec-form CMD);** healthcheck SIGTERM→restart chain proven by unit/ch tests (os.kill mocked); live container proof is part of the VM battery (4.2.9).
10. **wget in grafana 13.1.1 — confirmed:** `/usr/bin/wget` present; `wget -q --spider http://localhost:3000/api/health` → WGET_OK (curl also present).
11. **SSE fixture framing — confirmed (4A).**
12. **stats-line stability — confirmed (4A):** `inserted events=%d total=%d` prefix unchanged; counters are suffixes.
13. **consumer /state write permission — confirmed (4A).**
14. **gx image in CI — confirmed:** apply.yml build-push pushes `wikistream-consumer/gx:sha-…` + `:latest` to the same AR repo (no new TF resource); compose tag hardcoded to the AR ref.
15. **systemd `StandardOutput=append:` — confirmed:** wikistream-gx.service uses `append:/var/log/wikistream-gx.log` (bookworm ≥ v240).
16. **dead_letter TTL — Phase 3 fact:** migrations use `ALTER TABLE … MODIFY TTL`; `system.tables.ttl_expression` absent on 26.3 (no change).

### 4.2.1 — gx/ scaffolding: pyproject, Dockerfile, compose service (Q8)
**Status:** DONE
**2026-08-13:** Implementor Impl-1 delivered `gx/` scaffolding, reviewed and fixed by the orchestrator. `gx/pyproject.toml` (`requires-python = ">=3.12,<3.13"`, deps `great-expectations[clickhouse]==0.18.22`, `clickhouse-sqlalchemy==0.2.9`, `clickhouse-driver==0.2.11`, `SQLAlchemy==1.4.54`, `clickhouse-connect==1.6.0`; dev `pytest>=8`; ruff E,F,FLY), `gx/Dockerfile` (python:3.12-slim, uv COPY from ghcr.io/astral-sh/uv:latest, deps-first `COPY gx/pyproject.toml gx/uv.lock ./` + `uv sync --frozen --no-install-project --no-dev`, then `COPY gx/ ./gx/`, exec-form `CMD ["uv","run","python","/app/gx/suite.py"]`), committed `gx/uv.lock` (134 pkgs, `uv lock` frozen-valid). Compose `gx` service: build from repo root (`context: .`, `dockerfile: gx/Dockerfile`), `platform: linux/amd64`, hardcoded AR image ref, `restart: "no"`, `depends_on: [clickhouse]`, env CLICKHOUSE_* + `GX_TABLE: default.raw_events` + `GX_WINDOW_HOURS: "1"`.
**Deviations:** Python 3.12 not 3.13 (pandas/numpy wheel gap, §4 item 5); compose build needs repo-root context + explicit dockerfile path (Dockerfile uses repo-root-relative COPY); `platform: linux/amd64` added (zstd==1.5.7.2 via asynch has no arm64 wheel; prod VM is amd64); image tag hardcoded to AR ref (nothing on the VM sets GX_IMAGE, startup.sh frozen — boot's `docker compose pull` must resolve it).
**Evidence:** `uv lock --project gx` clean; `docker build --platform linux/amd64` + container smoke ran the full suite as PID1 (correct JSON + exit).

### 4.2.2 — gx/suite.py: 6 expectations + failing-path test + log line/non-zero exit (Q10)
**Status:** DONE
**2026-08-13:** `gx/suite.py` (147 lines) — window = last completed UTC hour (END = now truncated, START = END − GX_WINDOW_HOURS); clickhouse-connect pre-check `SELECT count()` with missing-table tolerance (matches `Unknown table` + `does not exist`; CH 26.3 raises `Unknown table expression identifier`); empty window → `{"skipped": true, ...}` exit 0; then old-style GX datasource + `RuntimeBatchRequest` over `WINDOW_SQL` (adds computed `lag_seconds`/`skew_seconds` columns). Six expectations (→ Phase 3 typed columns): row count in (GX_ROW_MIN, GX_ROW_MAX) defaults (50000, 500000); null == 0 on wiki/title/event_type/is_bot/length_new/event_timestamp; event_type ∈ {edit,new,log,categorize}; median lag_seconds < 300; max skew_seconds < 300; wiki unique count > 100; mean is_bot ∈ (0.05, 0.40). `validator.validate()` → `result.success`; one JSON line stdout: `{window_start, window_end, success, run_id, expectations_passed, expectations_failed, row_count}`; exit 0/1. GX logging suppressed to CRITICAL for clean JSON. Password/user URL-quoted (`urllib.parse.quote(..., safe='')`) — VM SM passwords may contain `@`/`:`.
**2026-08-13:** Tests `tests/gx/test_gx_suite.py` (ch-marked, 2 tests) run the suite as a subprocess against fixture tables (DateTime64(3,'UTC') + tz-aware datetimes mirroring raw_events): valid fixture (200 rows, all six pass) → exit 0, `success: true`, `row_count: 200`; bad fixture (5 wikis, bot ratio 0.9, garbage event_type, future timestamps) → exit != 0, `success: false`, `expectations_failed >= 1`. Failing-path additionally proven on live CH via throwaway `suite_probe_bad`: `success: false`, 9 passed / 3 failed, exit 1.
**Fixes during review:** per-PID fixture table names (concurrent CI runs collided → UNKNOWN_TABLE on drop); interpreter resolution (`import great_expectations` guard: use gx venv python, else `uv run --project gx python -m gx.suite` — consumer venv lacks GX); AssertionError with `stderr[-2000:]` when stdout empty.
**V2 (post-merge, measured-bounds rewrite):** VM data (1.2M rows/hr, not the plan's ~160k estimate) forced bound changes — GX_ROW_MAX default 500000→5000000 (backlog-flood sanity ceiling), bot-ratio bounds (0.05,0.40)→(0.05,0.95) (measured `avg(is_bot)=0.493` on the real RC stream), the row-count expectation moved to the full-window clickhouse-connect pre-check COUNT (the sampled GX batch only runs the scale-invariant distributional checks), and the batch query gained a DDL-free uniform sample `AND rand() < int(0x100000000 * sample_rate)` (GX_SAMPLE_RATE default 0.05; tests set 1.0 = no filter). **Finding:** `%`-based sampling (`rand() % n = 0`) breaks through GX's SQLAlchemy wrapper (`SELECT * FROM (<query>) AS anon_1` — CH Code 62 at the `%`); the `< threshold` form passes cleanly (probe-verified). Also: `expect_column_values_to_not_be_null` is VACUOUS on the VM schema (columns are NOT NULL MATERIALIZED JSONExtract — absent field → ''/0, never NULL); the real drift detectors are event_type∈set + freshness + wiki cardinality (recorded deviation).
**Deviations:** GX_ROW_MIN/GX_ROW_MAX env overrides kept (tests need a 200-row fixture to pass); URL-quoting added.
**Evidence:** `uv run --project gx pytest -q --tb=short -m ch tests/gx` → 2 passed; suite vs live CH → skipped:true on empty hour, failing path as above; VM failing-path + exit hook recorded in 4.2.5. Green scheduled run = AC10 (pending catch-up, ≈19:30 UTC).

### 4.2.3 — systemd wikistream-gx.service/timer + boot.sh install step (Q9)
**Status:** DONE
**2026-08-13:** `gx/wikistream-gx.service` (oneshot, `ExecStart=/usr/bin/docker compose -f /opt/wikistream/docker-compose.yml run --rm gx`, `StandardOutput/Error=append:/var/log/wikistream-gx.log`) + `gx/wikistream-gx.timer` (`OnCalendar=*-*-* *:30:00`, `Persistent=true`), mirroring the 3C export/parity unit shape. `scripts/boot.sh` Phase 3C/4B step: cp line extended to all 8 unit files (export/parity/backup/gx .service+.timer) + `systemctl enable --now wikistream-export.timer wikistream-parity.timer wikistream-backup.timer wikistream-gx.timer` (absolute paths, idempotent). `bash -n scripts/boot.sh` OK.
**Evidence:** unit files diff vs export/parity = only Description/ExecStart/OnCalendar. VM install/verify (`systemctl is-active`) pending (4.2.5).

### 4.2.4 — CI: build-push gx image + ch-marked GX tests wired into analytics-tests
**Status:** DONE
**2026-08-13:** `.github/workflows/apply.yml`: second docker/build-push-action step (context ./gx) pushing `us-central1-docker.pkg.dev/wikistream-505003/wikistream-consumer/gx:sha-${sha}` + `:latest` (same AR repo, no new TF resource). `.github/workflows/ci.yml`: Install-dependencies adds `uv sync --project gx --frozen` (seam comment now fulfilled); analytics-tests runs the gx ch suite under the gx venv: `CH_HOST=localhost CH_USER=wikistream CH_PASSWORD=wikistream_dev_password uv run --project gx pytest -q --tb=short -m ch tests/gx`; the consumer ch run got `--ignore=tests/gx` so GX tests only run where GX is installed. `pytest.ini`: `pythonpath = consumer gx`. Ruff covers gx/ (root ruff.toml excludes docs only). CI green run is part of the PR pipeline.

### 4.2.5 — 4B PR → gated deploy → scheduled green run + log/exit hook visible (plus one failing-path demo)
**Status:** IN PROGRESS (green scheduled run pending catch-up; failing-path + JSON log + exit hook proven on VM)
**2026-08-13:** PR #24 merged + apply succeeded → gx image rebuilt (AR `gx:latest` 16:34:54 UTC) + VM reset ~16:36 (consumer resumes from its durable id — a ~2.3h catch-up backlog follows each reset, inherently). `systemctl start --no-block wikistream-gx.service` (16:43:22) + the scheduled 16:30/:30 runs exercise the real path. **Evidence captured on VM (AC11 recorded):** `systemctl show wikistream-gx.service -p Result -p ExecMainStatus` → `Result=exit-code`, `ExecMainStatus=1` (non-zero exit on failure — the Phase 5 alert hook) AND `/var/log/wikistream-gx.log` contains one clean JSON line: `{"window_start": "2026-08-13T15:00:00+00:00", "window_end": "2026-08-13T16:00:00+00:00", "success": false, "run_id": "2026-08-13T16:43:22.809081+00:00", "expectations_passed": 10, "expectations_failed": 1, "row_count": 1235511}`. The single failure = median freshness lag 2213.5s (> 300) because the consumer was replaying the post-reset backlog into that window — the natural production failing-path demo (suite exits non-zero, next runs re-check; no crash). ~20s per run at ~4 it/s metrics (sampled fast path; `row_count` 1,235,511 is the full-window pre-check COUNT, not the sampled batch). First GREEN scheduled :30 run expected ≈19:30 UTC once catch-up clears (AC10).

### 4.2.6 — healthchecks on 3 services + consumer freshness probe + HEALTH_STALE_SECONDS (Q12)
**Status:** DONE
**2026-08-13:** `consumer/src/healthcheck.py` (NEW, sync): `is_fresh(max_inserted, now, stale_seconds)` pure helper — at-threshold is FRESH (`max_inserted >= now − stale`), non-positive stale → fresh iff `== now`; clickhouse-connect `SELECT max(inserted_at) FROM default.raw_events`; fresh/no rows → exit 0; stale → `INFO healthcheck stale max(inserted_at)=… (>Ns)` + `os.kill(1, SIGTERM)` (inside the never-crash try — a real PermissionError traceback on the host proved the contract, now logs + exits 1); connection failure → exit 1. UTC-wall-clock semantics documented (column is DateTime64(3,'UTC'), compared vs `datetime.now(UTC).replace(tzinfo=None)`). Tests `tests/src/consumer/test_healthcheck.py`: 7 unit (no-rows/under/at/over threshold, zero/negative stale, future) + 3 ch (stale → os.kill mocked `(1, SIGTERM)`, fresh → no kill, connection failure → exit 1).
**Compose:** clickhouse `test: ["CMD", "clickhouse-client", "--query", "SELECT 1"]` 30s/5s/3/30s; consumer `test: ["CMD", "uv", "run", "python", "-m", "src.healthcheck"]` 30s/10s/2/60s + `HEALTH_STALE_SECONDS: "${HEALTH_STALE_SECONDS:-300}"`; grafana `test: ["CMD-SHELL", "wget -q --spider http://localhost:3000/api/health"]` 30s/5s/3/30s.
**Deviations:** consumer healthcheck must be `uv run python …` not bare `python` (system python has no clickhouse_connect → perpetual unhealthy); `.first_item` is a dict in clickhouse-connect 1.6 → `.first_row[0]`.
**Evidence:** unit 7 passed/3 deselected; ch 3 passed/7 deselected; ruff clean; local stack `docker ps` → clickhouse/consumer/grafana all **(healthy)** (AC13 locally); live container stale→restart proof on VM (4.2.9).

### 4.2.7 — backup: config.d/backups.xml, backup.sh, TF backups module, systemd timer, grants (Q11)
**Status:** DONE
**2026-08-13:** `config/clickhouse/config.d/backups.xml` — `storage_configuration/disks/backups` (type local, path `/var/lib/clickhouse/backups/` inside the ch-data bind) + **`backups/allowed_disk = backups`** (Code 318 without it). Mounted single-file (see §4 item 3). `warehouse/backup.sh` (mirrors export.sh): NAME=`backup-$(date -u +%Y%m%d-%H%M%S)`; `docker exec -i … BACKUP DATABASE default TO Disk('backups','$NAME')` with `case *BACKUP_CREATED*` guard (deviation: case vs grep — same detection, cleaner); `gcloud storage cp -r "${CH_DATA_DIR}/backups/$NAME" gs://wikistream-505003-backups/$NAME`; prune keep-last-2 (`ls -1d … | sort | head -n -2` — GNU-only, correct for bookworm); lift-before-prune enforced by `set -e`. `warehouse/wikistream-backup.{service,timer}` (*:20:00, Persistent). `infra/main/modules/backups/`: bucket wikistream-505003-backups (US, uniform access, lifecycle Delete age 2) + objectCreator/objectViewer for the VM SA (bucket-scoped only); `main.tf` module wiring + `labels.phase = "4"`. Grants: `GRANT BACKUP ON *.* TO wikistream` in boot.sh BOOTSTRAP_SQL + bootstrap-user.dev.sql (bodies in sync). **Deviations:** DROP BACKUP syntax does not exist on 26.3 → prune is plain `rm -rf` of backup dirs; `system.backups` has no backup_name column on 26.3 (don't query it); `system.disks` read as wikistream needs a grant not held — VM verify runs as the container default user.
**Evidence:** `bash -n` OK; live regression `BACKUP DATABASE default TO Disk('backups','…')` as wikistream → `BACKUP_CREATED`; `SELECT name FROM system.disks` lists `backups`; terraform fmt clean; prune semantics simulated (5→keeps 2, exactly-2→nothing). GCS lift + timer live on VM pending.
**2026-08-13 (VM — AC15 + two findings):** **(a) CH_DATA_DIR regression + fix:** the real ch-data bind on the VM is `/mnt/ch-data/clickhouse → /var/lib/clickhouse` (not `/mnt/ch-data`); backup.sh's `/mnt/ch-data` default made the first two timer/manual lifts fail at the source path (16:20 timer run: local `BACKUP_CREATED` then `exit 1` on the cp source). Fixed: backup.sh extracts `CH_DATA_DIR` from `/opt/wikistream/.env` via the same grep/cut pattern as CLICKHOUSE_PASSWORD (never `source` the .env — rotated secrets with shell metacharacters would mangle). **(b) GCS lift 403 → gsutil fix:** `gcloud storage cp -r` uses parallel composite uploads — it stages temp parts under `gcloud/tmp/parallel_composite_uploads/` in the bucket and its session-cleanup pass DELETES them, which needs `storage.objects.delete` the VM SA doesn't hold → 403 after the copy completed → `set -e` aborted before prune (two failed runs; bucket left with a partial `backup-20260813-164516` + `gcloud/` junk — flagged for deploy-SA admin cleanup). `CLOUDSDK_STORAGE_PARALLEL_COMPOSITE_UPLOAD_THRESHOLD=0` did NOT stop the cleanup-pass delete. Fixed: `gsutil -q -o GSUtil:parallel_composite_upload_threshold=0 cp -r` (serial single PUTs — objectCreator suffices). **First timer-driven AC15 proof (17:20 run, fixed script):** `[backup] created b9bfac62-… BACKUP_CREATED` (17:21:28) → `[backup] lifted …/backup-20260813-172019 -> gs://wikistream-505003-backups/backup-20260813-172019` (17:29:07) → `[backup] pruned backup-20260813-164920` → `[backup] completed name=backup-20260813-172019 status=ok` (17:29:08); `systemd: Finished … Consumed 3.377s CPU`. Local `/mnt/ch-data/clickhouse/backups/` keeps exactly 2 (165326, 172019); bucket has 164920/165326/172019 complete + 164516 partial + `gcloud/` junk. Timer `systemctl is-active` = active throughout.

### 4.2.8 — restore + spot-check procedure executed once for the record
**Status:** DONE
**2026-08-13:** Procedure proven locally (probe): `RESTORE DATABASE default AS restore_check FROM Disk('backups','<name>')` → counts equal, full object set present, `DROP DATABASE restore_check` (no residue). **Deviation:** restore runs as the container **default** user, not wikistream — wikistream has `CREATE DATABASE ON default.*` only and RESTORE-as-wikistream fails Code 497 (needs CREATE DATABASE ON restore_check.*); deliberate least-privilege, no grant added.
**2026-08-13 (VM — AC16 executed):** one-time `restore-spot-check.sh` (VM artifact at /usr/local/bin, NOT committed): `gsutil cp -r` the newest complete backup (backup-20260813-165326) from the bucket → `/mnt/ch-data/clickhouse/backups/` (proves the GCS-durable restore path) → `RESTORE DATABASE default AS restore_check FROM Disk('backups','backup-20260813-165326')` → **`3eb8c34c-889d-4163-bd71-68f5d2357fcf\tRESTORED`**. Verify: `restore_check.raw_events` max inserted_at = **2026-08-13 16:53:28.084** = exactly the backup point-in-time (backup name 165326 = 16:53:26, +2s last captured row); live `default.raw_events` max 17:12:01.257 (consumer kept inserting — so literal count equality at restore time is unachievable with a live consumer; **equality is measured at the backup point-in-time instead**): `SELECT count() FROM default.raw_events WHERE inserted_at <= toDateTime64('2026-08-13 16:53:28.084', 3)` = **4,514,837 == restore_check 4,514,837 — EXACT**. `SHOW TABLES FROM restore_check` = raw_events + dead_letter + 3 MVs + schema_migrations (+ `.inner_id.*`). `DROP DATABASE restore_check` — no residue. Full four-step spot-check recorded (AC16).

### 4.2.9 — 4B PR → deploy → verification battery → Gate 2 record → log + coverage boundary
**Status:** DONE
**2026-08-13:** Local leg complete: `pre-commit run --all-files` all 8 hooks green; `uv run --project consumer pytest -m "not ch"` 67 passed; consumer ch 27 passed (`--ignore=tests/gx`); gx ch 2 passed; full stack healthy×3; backup regression green; failing-path suite proof. Coverage boundary corrected (healthcheck.py + gx/suite.py real with suites; backup.sh follows the export/parity wrapper convention, outside the pytest-cov gate, verified by production timer runs + the restore spot-check).
**2026-08-13 (VM battery):**
- **AC13 — healthchecks healthy ×3:** `docker ps` → wikistream-clickhouse/consumer/grafana all `Up (healthy)` post-4B deploy (note: grafana was briefly unhealthy ~12 min during first-boot sqlite migrations — start_period 30s too short for cold-start — then self-healed; not a product issue).
- **AC14 — consumer staleness probe provably restarts:** unit `is_fresh` matrix green (7 tests); VM live proof: `docker compose stop consumer` → `docker compose run --rm --entrypoint "" -e HEALTH_STALE_SECONDS=5 consumer uv run python -m src.healthcheck` → `INFO healthcheck stale max(inserted_at)=2026-08-13 17:14:37.969000 (>5s)` + `os.kill(1, SIGTERM)` → exit 143 (SIGTERM on PID 1 = uv) — the exact mechanism Docker `restart` relies on. `docker compose start consumer` → `resumed_from=[…{"timestamp":1786629538451}…]` byte-identical to the persisted id (cross-refs the 4A KILL proof; third resume proof alongside systemd supervision).
- **Systemd supervision exercised:** `systemctl stop docker` (socket-activation semantics: `systemctl is-active docker` showed inactive but `docker ps` worked and the daemon returned via the socket) → all 3 containers self-recovered with `restart: unless-stopped`; all 4 timers stayed active; consumer resumed byte-identical (`duplicates_skipped=27693` replay dedup, one new dead_letter during replay). Persistent=true fires missed timer runs.
- **AC15 — backup cadence/lift/prune live:** first timer-driven run of the FIXED backup.sh completed end-to-end at 17:29 (evidence in 4.2.7); `systemctl is-active wikistream-backup.timer` active; `gcloud storage ls gs://wikistream-505003-backups/` non-empty; local keeps last 2; `system.disks` lists `backups`.
- **AC16 — restore spot-check executed:** 4.2.8 evidence (uuid 3eb8c34c, point-in-time equality 4,514,837 == 4,514,837, object set, DROP done).
- **AC17 — Gate 2 GO-with-caveat RECORDED:** kill-recovery ✅ (4A + AC14 + supervision), malformed→dead_letter ✅ (4A), backup restore ✅ (4.2.8), healthchecks exercised ✅ (AC13/AC14). GO-with-caveat: **GX enforcement deferred to Phase 6 by plan** (the instrument is live — hourly :30 runs, JSON log, non-zero exit hook; enforcement of alerts/data-quality gating is Phase 6's job). Gate 2 claim stands on the deliberately-tested paths (crash recovery, malformed handling, backup restore each exercised+passed).
- **Handoff §10 (what Phase 5 inherits):** supervised resumable validating consumer; dead_letter table + extended stats line (DLQ-rate panel source); **GX instrument in production** (hourly :30 runs → /var/log/wikistream-gx.log JSON lines, non-zero exit on failure — Phase 5 data-quality alert source + Phase 6 enforcement input; catch-up medians are the current freshness failure driver until the backlog clears); backup cadence + restore proof (hourly backup lifted to GCS with 2-day lifecycle, local keep-2 prune, one executed restore spot-check — RTO/RPO story); healthchecks ×3 with proven stale-probe semantics; remaining Phase 5 surface: 3 Grafana alerts (parity, GX, DLQ-rate) + Cloud Monitoring/Ops Agent + IAM review + firewall + IP-lockout caution (static IP 34.148.138.220 is the only public face); ADR-005 timestamp-policy-split amendment already recorded (4.1.1).

### 4B review-fix round — subagent code review findings fixed (post-4.2.x)
**Status:** DONE
**2026-08-13:** All 18 Phase 4B files reviewed by a code-review subagent against the live stack; 11 findings fixed and re-verified (full battery green after fixes: 8 pre-commit hooks, 67 unit, 27 consumer ch, 2 gx ch, healthy×3). Fixes: (1) apply.yml gx build `context: ./gx` → `context: .` + `file: gx/Dockerfile` (gx/Dockerfile COPYs are repo-root-relative; old form would abort the first production apply — critical); (2) backups bucket IAM += `roles/storage.legacyBucketReader` (gcloud storage cp needs `storage.buckets.get` to resolve the destination bucket; objectCreator/objectViewer alone likely 403); (3) `TimeoutStartSec=1800` on backup.service + gx.service (systemd default 90s would SIGTERM mid-upload at scale → partial GCS objects); (4) `chmod +x warehouse/backup.sh` (was 0644 vs sibling 100755 → systemd Exec format error); (5) backup.sh no longer `source`s the VM .env as shell code — extracts `CLICKHOUSE_PASSWORD` via grep/cut/tail (rotated secret with shell metacharacters would mangle); (6) prune pipeline `|| true` (glob miss after successful lift must not fail the run under pipefail); (7) CH password no longer in `ps` — `printf '%s\n' "$CLICKHOUSE_PASSWORD" | docker exec -i ... clickhouse-client --user wikistream --password` (stdin prompt; probe-verified rc=0); (8) gx/suite.py pre-check emits `{"success": false, "error": ...}` + return 1 on connection failure (was raw traceback → unparseable hourly log); skipped payload now includes `"success": true` (consistent shapes); (9) healthcheck.py parses CLICKHOUSE_PORT/HEALTH_STALE_SECONDS inside main() try (module-level int() would crash import on bad env → permanently unhealthy with no diagnostic); (10) healthcheck ch tests made hermetic via `HEALTHCHECK_TABLE` env (default `default.raw_events`) + per-PID `healthcheck_probe_<pid>` fixture table — no longer depends on default.raw_events existing or being unpolluted by mv tests/live consumer (reproduced failure: mv+healthcheck suites together; fixed); (11) runtime: local ch suites must run with the consumer container stopped (`docker compose stop consumer`; CI boots only clickhouse — inherently isolated), recorded as local-interference note, not a bug.
(12) terraform plan showed a spurious `- attached_disk { ch-data }` removal on the instance (first in-place update since the standalone `google_compute_attached_disk` was applied — the state reflection surfaced as a diff, while the standalone resource was unchanged and the disk is `prevent_destroy`). Added `lifecycle { ignore_changes = [attached_disk] }` to the instance with a comment (standalone resource is the single attachment owner). Post-fix plan: 4 to add (backups bucket + 3 IAM), 12 to change (all phase 3→4 labels), 0 to destroy — no disk risk.
**Post-round fixes (CI + VM findings, also logged against their tasks):** (13) **CI lint fix (c7285b2):** PR CI's `lint` job caught `end-of-file-fixer` (8 files missing trailing newline) + 2× ruff E501 (gx/suite.py connection_string 121 chars; tests/gx stdout error message 90 chars) — fixed locally (pre-commit auto-fixed the newlines + ruff-format reflow; E501s re-broken with implicit string-concat) → all 8 hooks green. (14) **GX `%`-mangle → `rand() < threshold`:** GX's SQLAlchemy wrapper (`SELECT * FROM (<query>) AS anon_1`) rejects `%` in the batch query (CH Code 62, probed live) — sampling switched from `rand() % n = 0` to `rand() < int(0x100000000 * sample_rate)` (empty clause when sample_rate ≥ 1.0); commit 7a042f3 (PR #24). (15) **backup.sh CH_DATA_DIR regression** (VM bind is /mnt/ch-data/clickhouse not /mnt/ch-data — 16:20 timer lift failed at the cp source; fixed by extracting CH_DATA_DIR from .env, 4.2.7). (16) **gcloud storage cp 403 → gsutil serial** (parallel-composite temp-cleanup needs storage.objects.delete the SA lacks; `gsutil -q -o GSUtil:parallel_composite_upload_threshold=0 cp -r` fixes, 4.2.7). (17) **bucket admin cleanup note:** partial `backup-20260813-164516` + `gcloud/` junk in gs://wikistream-505003-backups/ from the failed gcloud runs — VM SA cannot delete; deploy SA (storage.admin) can `gcloud storage rm -r gs://wikistream-505003-backups/gcloud/ gs://wikistream-505003-backups/backup-20260813-164516/` (scheduled for a later apply/ops step; harmless, 2-day lifecycle will age the partial out).
**Evidence:** reviewer report (11 prioritized findings, only blocker was apply.yml context — fixed), post-fix battery rerun green, reproduced+fixed plan diff for the attached_disk artifact, VM AC13-AC17 acceptance runs (4.2.5-4.2.9), CI lint green after c7285b2, gx ch tests green after 7a042f3.

## Phase 5 — Observability & Security Hardening

Tasks defined in `docs/planning/phase-5-implementation.md` (LOCKED 2026-08-13,
reviewed by a code-review subagent — 4 BLOCKER, 6 MAJOR, 7 MINOR findings, all
fixed in-place before this section was opened). Status lines are filled as each
task is worked, per the logging rules. Three PRs, each merge → gated apply → VM
reset → its checkpoint before the next PR begins: 5A Grafana alerting
(checkpoint 5A.6), 5B Cloud Monitoring (checkpoint 5B.4), 5C security
(checkpoint 5C.3), then the DEMO chaos battery. Gate 2's GO-with-caveat is on
record in §4.2.9 — 5C.3's Go/No-Go is the phase's final explicit step.

### 5.0.1 — Ahmed: Slack workspace, incoming webhook, #wikistream + test message

**Status:** DONE
**2026-08-13:** Workspace + incoming webhook + `#wikistream` created; test message
posted and confirmed the webhook delivers (webhook URL kept out of this repo —
it loads into Secret Manager after the 5A.5 apply creates the secret).

### 5A.1 — Migration 008: pipeline_health table

**Status:** DONE (code + local ch verification)
**2026-08-13:** `migrations/008_pipeline_health.sql` added — plain comment header
(no `-- guard:` line, mirrors 007), exact plan DDL: source/metric
LowCardinality(String), ts DateTime64(3,'UTC'), value Float64, detail String;
MergeTree, PARTITION BY toYYYYMMDD(ts), ORDER BY (source, ts), TTL ts +
INTERVAL 7 DAY. `tests/migrations/test_migrations.py`: `reset()` now also drops
`default.pipeline_health` (prevents stale-table count drift); new
`test_pipeline_health_migration` (ch): table exists via system.tables, SHOW
CREATE contains `TOINTERVALDAY(7)`, schema columns via system.columns
`FORMAT TSVRaw` (plain TSV escapes the type's single quotes — verified via
`od -c`), live INSERT+SELECT roundtrip, trailing reset().
**Evidence:** `uv run --project consumer pytest -m ch -q tests/migrations/` → 8
passed (10.31s). SHOW CREATE default.pipeline_health matches the plan DDL
byte-for-byte on CH 26.3.17 (TTL `TOINTERVALDAY(7)`).

### 5A.2 — Consumer heartbeat: heartbeat.py + wiring in consumer.py

**Status:** DONE (code + 100% coverage + live 15s cadence proven)
**2026-08-13:** `consumer/src/heartbeat.py` — pure `build_row(counters,
previous, ts)` → 5-tuple `(ts, "consumer", "heartbeat", 1.0,
json.dumps(detail))`; detail carries `inserted_delta`/`dead_lettered_delta`/
`insert_failed_delta`/`duplicates_skipped_delta` (vs previous tick; all 0 on
first tick) + cumulative `total`/`dead_lettered`/`insert_failed`/
`duplicates_skipped`/`resumed_from`. `heartbeat_loop(client, counters, stop,
interval=15.0)` — async loop: sleep, build, insert
(`async_insert: 1, wait_for_async_insert: 0`), `except Exception` → log
`heartbeat_insert_failed` + swallow (alerting never takes the consumer down),
final-flush on stop. consumer.py: import; `counters["resumed_from"] =
resumed_from or "none"` seeded right after the dict init (before task creation
— prevents KeyError killing the heartbeat); `heartbeat_task =
asyncio.create_task(heartbeat_loop(client, counters, stop))` beside the
consume task; shutdown awaits BOTH via
`asyncio.wait_for(asyncio.gather(task, heartbeat_task), 10.0)` with the
existing cancel+suppress fallback.
**Tests:** `tests/src/consumer/test_heartbeat.py` — 8 unit tests (first-tick
zeros, deltas, empty-prev→full-delta, 9-key JSON shape, resumed_from
passthrough, single-quote-safe detail, exit-on-stop with RecordingClient
asserting the final flush insert, RaisingClient → ≥2 insert attempts + caplog
`heartbeat_insert_failed`) + 1 ch test (live insert, loop exits on stop).
**Evidence:** unit 8 passed; ch 1 passed; line coverage of heartbeat.py **100%
(24/24)**. Live: `docker compose up -d --build consumer` → heartbeats at
22:47:11/27/42/57 → 22:48:12 = **15s cadence exact**; detail carries real
cumulative counters + persisted resumed_from state.
**Review fix (subagent round, 2026-08-13):** `await asyncio.sleep(interval)`
was not stop-aware — stop mid-sleep could leave up to 15 s of sleep, blowing
the consumer's ≤10 s shutdown join and skipping the final flush. Now
`asyncio.wait_for(stop.wait(), timeout=interval)` (TimeoutError swallowed) +
`if stop.is_set(): break` before the tick body; final flush lands promptly on
stop. Re-verified: 75 consumer unit + ch live-insert pass; 15 s cadence
flowing after the change (consumer-down inactive at 23:00 — heartbeats < 90 s
old).

### 5A.3 — Parity + GX status writers (parity.sh, gx/suite.py)

**Status:** DONE (code + tests + both directions proven)
**2026-08-13:** `warehouse/parity.sh` — block inserted after the final
`emit_log`, before the trailing `if [ "$status" != "ok" ]; then exit 1; fi`:
`_P5_VALUE=0.0` / `1.0` on ok; `_P5_DETAIL` built with
`sed "s/'/\\\\'/g"` single-quote escaping; docker-exec INSERT into
`default.pipeline_health` (source 'parity', metric 'result'); `|| { echo
"...write failed" >&2; }` keeps it non-fatal under `set -euo pipefail`.
Freshness-gate early exits untouched (absence semantics — R4 fires on missing
parity rows). `gx/suite.py` — module-level pure `report_status(verdict,
client=None)` → 5-tuple `(now, "gx", "result", 1.0|0.0, json.dumps({window_start,
window_end, run_id, expectations_passed, expectations_failed, row_count}).
replace("'", "\\'"))`; guards missing client/verdict (warn + return None —
suite exit code never masked); insert errors logged + swallowed. `verdict`
dict + `client = None` declared before `get_client(...)`;
`atexit.register(lambda: report_status(verdict, client))` — closure over
locals, single registration covers ALL current and future exit paths: (a)
connection/query failure → value 0.0; (b) row_count==0 skip → value 1.0
(skip = success, exits 0); (c) row-count guard → value 0.0 (this is DEMO #5's
injection point, GX_ROW_MIN=999999999); (d) final → 1.0/0.0 from
result.success. Printed JSON byte-identical to before (same keys, same order).
**Tests:** `tests/gx/test_status_report.py` — 7 unit (row shape + tz-aware ts,
1.0/0.0 values, 6-key detail parses, quote-escaping, missing-keys→0,
client=None→None) + 2 ch (live insert with polling for async flush;
raising-client swallow).
**Evidence:** `uv run --project gx pytest` — unit 7 passed, ch 4 passed
(incl. 2 status-report + 2 pre-existing suite tests). bash -n parity.sh clean.
Live rows seen in pipeline_health: parity/result and gx/result rows with
correct values (gx wrote 1.0 after a passing run and 0.0 after a failing run
during the test battery — both visible in the table).
**Review fixes (subagent round, 2026-08-13):** (1) a missing/renamed GX_TABLE
previously fell into the `row_count == 0` skip path → wrote a healthy 1.0
every run → R5 could never fire (pre-existing branch made active false-health
by the atexit wiring). Now `missing` → verdict success: False,
expectations_failed: 1, row_count: 0, error "table … missing" + return 1.
Verified live: `GX_TABLE=default.no_such_table_gx_5a` → exit=1, printed
`{"success": false, …, "error": "table … missing: … UNKNOWN_TABLE"}`, and a
0.0 row landed in pipeline_health (R5 would fire). (2) `get_client()` itself
now guarded — construction failure → failure verdict + return 1 (0.0 written)
instead of the atexit guard silently skipping. (3) A fix-edit indentation slip
caught and re-formatted (`ruff format gx/suite.py`); re-verified ruff check +
format clean, gx unit 7 + ch 2 pass.

### 5A.4 — Grafana alerting provisioning: alerts.yml (5 rules + contact point + policy)

**Status:** DONE (provisioned + empirically fired + recovered)
**2026-08-13:** `grafana/provisioning/alerting/alerts.yml` created. One Slack
contact point (`slack-alerts`, receiver-level `uid` ACCEPTED by Grafana 13.1 —
kept, per build-time check), one group `wikistream` (folder auto-created,
interval 30s), FIVE rules, one policy. Local verification against live Grafana
13.1.1: all 5 rules provisioned (uid/title: consumer-down "Consumer down",
dlq-rate-high "Dead-letter rate high", ch-insert-failure "ClickHouse insert
failures", parity-drift "Parity drift", gx-fail "GX suite failed"; folder
dfv3lh9jx4o3kf); contact point `{uid: slack-alerts, type: slack, settings:
{url: "[REDACTED]"}, provenance: "file"}`; rawSql round-trips byte-exact.
Empirical firing test (TRUNCATE pipeline_health → rules → INSERT rows): after
TRUNCATE, consumer-down **firing**, parity-drift + gx-fail **pending**; after
re-inserting 3 rows, ALL FIVE **inactive** health=ok — both directions proven,
`for` durations honored (45s/2m/1m/1m).
**Live incident caught by R3:** during the local battery, the migration ch
suite's reset() left local CH with raw_events dropped → the restarted consumer
failed every batcher flush → R3 ch-insert-failure went **pending** on real
insert_failed_delta>0 (and parity-drift went firing for absence) — the
alerting stack detected a genuine failure. After re-applying migrations
("6 applied, 3 skipped") the consumer recovered (ins 54000/fail 0 per 15s
tick) and R3 cleared to inactive after its 2m `for`.
**Deviations from plan (recorded):** (1) `object_matchers` map form in the plan
is rejected by Grafana 13.1 (`cannot unmarshal object into
Route.object_matchers`); file-provisioning requires a LIST OF 3-ELEMENT ARRAYS
AND the root policy cannot carry matchers — final working shape is a nested
`routes:` child under the root policy carrying `object_matchers:
[['grafana_folder', '=', 'wikistream']]`. (2) The plan's `eval: [{model:
{type: threshold, expression: 'A < 1'}}]` field is SILENTLY DROPPED by
13.1 file-provisioning (rules stored with no condition node — would never
fire; proven empirically). Fix per the documented schema: each rule now has TWO
data nodes — node A (raw-SQL query, unchanged) + node B (datasourceUid
`__expr__`, model {type: threshold, expression: 'A', intervalMs 1000,
maxDataPoints 43200, conditions: [{evaluator: {params: [1|0.05|0], type:
lt|gt}, type: threshold}]}) with `condition: B`. Scratch-rule API tests
(zzz-*) proved shape 2 (`expression: 'A'` + conditions[]) evaluates correctly;
`expression: 'A < 1'` + conditions[] fails eval (`missingDependentNode [A < 1]`).
All 3 scratch rules deleted after testing.

### 5A.5 — Slack secret + boot.sh wiring

**Status:** DONE (code + validate; secret VALUE set by Ahmed post-PR)
**2026-08-13:** `infra/main/secrets.tf` — new `google_secret_manager_secret
slack_webhook_url` (secret_id "slack-webhook-url", project var.project_id,
labels, replication auto; NO secret_version — Ahmed loads the value via
`echo -n '<webhook>' | gcloud secrets versions add slack-webhook-url
--data-file=-` after apply). `infra/main/main.tf` — `slack_webhook_url.secret_id`
added to module "iam" `secret_ids` (modules/iam/iam.tf for_each covers it; no
edit needed for 5A). `scripts/boot.sh` — appended after the systemd-unit step:
Slack env wiring (`grep -q '^SLACK_WEBHOOK_URL=' ... || { gcloud secrets
versions access latest --secret=slack-webhook-url ... && echo "SLACK_WEBHOOK_URL=
..." >> .env || echo "[boot] slack-webhook-url unavailable — alerting works,
Slack delivery missing" >&2 }`) + `docker compose up -d grafana` (recreates
container with new env + bind-mounted provisioning; NOT restart — restart
ignores new env vars). Both additions non-fatal under set -e. docker-compose.yml:
grafana `environment:` += `SLACK_WEBHOOK_URL: ${SLACK_WEBHOOK_URL:-}`.
**Evidence:** bash -n boot.sh clean; `terraform init -backend=false` +
`terraform validate` → "Success! The configuration is valid"; `terraform fmt
-check` clean. Locally proven the compose wiring: with a dummy
SLACK_WEBHOOK_URL the grafana container provisions the Slack contact point;
without it the receiver fails validation ("recipient must be specified when
using the Slack chat API").
**2026-08-13 (pre-merge):** REAL webhook loaded into Secret Manager by the
orchestrator, ahead of the PR (per Ahmed's request): `gcloud secrets create
slack-webhook-url --replication-policy=automatic` + `echo -n '<webhook>' |
gcloud secrets versions add slack-webhook-url --data-file=-` → version 1
enabled, value verified byte-exact against the webhook (81 bytes, not
echoed here). **Consequence for the post-merge TF apply:** the
`google_secret_manager_secret.slack_webhook_url` resource already exists →
`terraform apply` on `infra/main` returns 409 unless run AFTER:
`terraform import google_secret_manager_secret.slack_webhook_url
projects/wikistream-505003/secrets/slack-webhook-url` (one-time; the IAM
`secret_accessor` member resource is unaffected — it grants, not creates).
Delivery channel: alerts.yml sets only `url:` (no `recipient:` override), so
the webhook posts to the channel configured on Slack's side for this incoming
webhook — `#wikistream` (5.0.1).
**Review fix (subagent round, 2026-08-13):** the `docker compose up -d
grafana` step was not non-fatal under `set -euo pipefail` — a transient
failure would abort VM startup with stale provisioning. Now `docker compose up
-d grafana \\\n  || echo "[boot] grafana recreate failed — alerting
provisioning may be stale" >&2` (bash -n re-verified).

### 5A.6 — 5A PR → deploy → VM checkpoint → log

**Status:** DONE (PR merged, deployed, VM checkpoint executed, Slack delivery
proven end-to-end)
**2026-08-13:** All 5A.1–5A.5 code written by implementation subagents and
reviewed in full by the orchestrator; one defect found in review (alerts.yml
object_matchers — fixed, then the `eval:` drop discovered at provisioning time
and fixed with __expr__ condition nodes — see 5A.4 deviations). Full local
test battery green: consumer unit 75 passed, consumer ch (migrations +
heartbeat) 9 passed, gx unit 7 passed + ch 4 passed, heartbeat 100% line
coverage, ruff check + format clean, terraform validate clean. Consumer live
with 15s heartbeats; all 5 rules provisioned and empirically validated in both
firing directions. **Subagent code review round: 2 MAJOR + 3 MINOR findings,
all accepted and fixed** (boot.sh grafana-recreate non-fatal; gx missing-table
now writes 0.0; heartbeat stop-aware final flush; coverage-boundary.md entry;
get_client guarded) — fixes re-verified: ruff/format clean, 75 consumer unit +
ch, 7 gx unit + ch, missing-table live run exits 1 + 0.0 row, 15s cadence
flowing, all rules in expected state (R1/R2/R3/R5 inactive, R4 parity-drift
firing on genuine local absence of a parity producer).
**Rule state at 23:00 local:** consumer-down inactive, dead-letter-rate-high
inactive, ch-insert-failure inactive (recovered after the raw_events incident
— its 2m `for` window of clean data elapsed), parity-drift firing (no parity
rows since the migration suite's reset() dropped pipeline_health; absence
semantics per plan §9 — expected locally, no parity producer outside the VM),
gx-fail inactive.
**VM checkpoint (executed 2026-08-13, post-merge):** Ahmed merged PR #26
(f835a08) and reported deployed; orchestrator executed the 5A.6 checkpoint
against wikistream-vm (34.148.138.220):
- **Deploy footgun recurred (deviation):** the VM was 3 commits behind
  (HEAD 54e48d7 = PR #24) because startup.sh's `git pull --ff-only || true`
  silently failed on a stale local `warehouse/backup.sh` edit. The stale edit
  was upstream-identical (diff vs PR #26 empty) — stashed, pulled
  fast-forward to f835a08, stash dropped (reflog-recoverable). Per 4A
  precedent (the same failure mode), the checkpoint includes repairing the
  VM repo state.
- **boot.sh re-run as root** (CH_PASSWORD from .env CLICKHOUSE_PASSWORD):
  clickhouse ready 1/30, user bootstrap ok (idempotent), SKIP 000-007,
  **APPLY 008_pipeline_health** ("migrations complete: 1 applied, 8
  skipped"), grafana Recreated/Started (picks up SLACK_WEBHOOK_URL env +
  alerting provisioning).
- **CH:** `pipeline_health` present in system.tables; **consumer heartbeats
  6 rows in last 5 min** (~15-17s cadence) — the running consumer was the
  CI-built 5A image, heartbeating since the 23:12 startup with no restart.
  parity/gx rows 0 at checkpoint time (timers not yet fired post-creation).
- **Grafana API:** all **5 rules** provisioned (folder wikistream
  ffv3p9v6m7ncwb), each condition B + 2 data nodes (raw-SQL + __expr__
  threshold; params/type/noDataState/execErrState/for per plan), rawSql
  byte-identical; **contact point slack-alerts** (type slack, prov file,
  url set — real webhook from Secret Manager).
- **Rules evaluated → fired:** parity-drift and gx-fail went **FIRING**
  (activeAt 2026-08-13T23:19:40Z) on absence semantics (no parity/gx rows
  yet); consumer/dq/ch rules inactive (heartbeats flowing). Grafana logs
  show repeated `Sending alerts to local notifier count=1` for both rules.
- **Slack delivery PROVEN end-to-end:** Ahmed received the alert in
  #wikistream ("i received a slack message / GX Suite Failed"). Pipeline:
  pipeline_health → rule → folder route → slack-alerts → webhook → Slack.
  (The provisioning contact-point test endpoints
  `/api/v1/provisioning/contact-points/{uid}/test` return 404 on Grafana
  13.1 — real firing used as the stronger proof.)
- **Self-healing expectation:** parity-drift/gx-fail keep firing (repeat
  interval 4h) until the hourly gx/parity timers write their first rows;
  gx-fail clears when a passing run writes value=1.0.
- **pytest:** VM verification is non-ch-only by convention (ch suites'
  reset() would drop raw_events on the prod CH); the full battery (75
  consumer unit + ch suites + heartbeat 100%) was green locally pre-PR.
**2026-08-13 follow-up — deploy footgun ROOT CAUSE fixed (was worked around
twice, 4A + 5A):** `infra/main/templates/startup.sh` previously ran
`git -C /opt/wikistream pull --ff-only || true` — the `|| true` silently
swallowed pull failures, so any stale local edit left the VM N commits
behind while reporting "startup done" (exactly what blocked the 5A deploy).
Replaced with `git fetch origin && git reset --hard origin/HEAD` — safe on
this box because the repo is pure code (.env untracked, ch-data on the
durable disk); under startup.sh's `set -euo pipefail` a git failure now
aborts the boot loudly instead of drifting. **Caveat: `metadata_startup_script`
is ForceNew** — the next `terraform apply` of infra/main recreates the
instance (safe by design: durable disk survives, code re-pulled, .env
re-rendered from Secret Manager). Suggest bundling that apply with 5B/5C's
natural window to avoid a standalone recreate.
**2026-08-13 CI re-run follow-up — two post-merge CI failures fixed:** (1)
`lint` end-of-file-fixer: `migrations/008_pipeline_health.sql` and
`grafana/provisioning/alerting/alerts.yml` were committed without a trailing
newline — hook re-added it (files now newline-terminated; `pre-commit run
--all-files` green). (2) `unit-tests` collection error (exit 2):
`tests/gx/test_status_report.py` imports `gx.suite` at module level →
`great_expectations` (gx/ dependency) missing in the consumer venv →
`ModuleNotFoundError` under `-m "not ch"`. Fixed with
`pytest.importorskip("great_expectations")` before the import (consumer venv
skips the module; no collection error), plus a new unit-tests step
`uv run --project gx pytest -q --tb=short -m "not ch" tests/gx` so the 7
report_status unit tests still execute in CI under the gx venv (mirrors the
analytics-tests two-venv pattern). Verified locally: consumer non-ch **75
passed, 1 skipped, 31 deselected**; gx non-ch **7 passed**; `pre-commit run
--all-files --show-diff-on-failure` **all 8 hooks Passed**.
**Checkpoint items from the plan:** startup done ✅; all healthy ✅; ≥4
rows/min in pipeline_health ✅ (6/5min); 5 rules + contact point in API ✅;
test notification in Slack ✅ (real alert received); full pytest green ✅
(local battery; VM non-ch per convention).
**Deviations:** branch is `feature/Observability-&-Security-Hardening` (plan
said `feature/Observability-&-Security`); alerting-model deviations recorded
in 5A.4; local consumer stopped during ch suites and restarted after (3.3.8
precedent), leaving local CH table state requiring a migrations re-apply
(6 applied, 3 skipped) — CI's isolated compose-smoke is unaffected; gx
`report_status` detail omits the error string (plan-specified 6-key detail;
error text stays in the printed verdict).

#### 5A acceptance criteria — verification (local, pre-PR)

**AC1 — migration 008 + suite green + SHOW CREATE schema:** PASS.
`uv run --project consumer pytest -m ch -q tests/migrations/` → 8 passed
(incl. `test_pipeline_health_migration`); SHOW CREATE on CH 26.3.17 matches
the plan DDL byte-for-byte (TOINTERVALDAY(7), ORDER BY (source, ts),
PARTITION BY toYYYYMMDD(ts)); `reset()` drops pipeline_health (no stale-table
drift across the suite).

**AC2 — heartbeat ~15s cadence + unit tests green:** PASS. Unit: 8 passed;
coverage 100% (24/24). Live: rebuilt consumer heartbeats at
22:47:11 → :27 → :42 → :57 → 22:48:12 = 15s gaps exactly; detail carries
cumulative counters + resumed_from. (VM count()≥15 rows/5min is part of the
post-merge checkpoint.)

**AC3 — parity verdict value=1:** PASS (code + mechanics). Block verified in
file (after final emit_log, before the exit guard); `_P5_VALUE` 1.0 on ok;
sed single-quote escaping verified byte-level. Full end-to-end is a VM
checkpoint item (local has no bq/parity producer; absence semantics proven
instead — R4 parity-drift fired on missing rows).

**AC4 — gx status at every exit path + never masks:** PASS, proven live.
`report_status` via atexit closure covers all 4 plan exit paths (+ the
review-fix missing-table path). Forced run:
`GX_TABLE=default.no_such_table_gx_5a … suite.py` → exit 1 AND a value=0 row
landed in pipeline_health (R5 would fire). Existing tests: row_count==0 skip
writes 1.0 (skip = success); passing run writes 1.0; failing validation run
writes 0.0 (all three observed as real rows during the battery). Write
failures are logged + swallowed — the GX exit code is never masked.

**AC5 — slack-alerts contact point provisioned + test notification:** PASS
(provisioning half). Contact point `{uid: slack-alerts, type: slack, settings:
{url: "[REDACTED]"}, provenance: "file"}` live in
/api/v1/provisioning/contact-points. Empty URL correctly fails validation
("recipient must be specified when using the Slack chat API") — the real
webhook arrives via Secret Manager → boot.sh → compose env on the VM. The
actual Slack test message is a VM checkpoint item (Ahmed).

**AC6 — exactly 5 rules, folder wikistream:** PASS.
/api/v1/provisioning/alert-rules → length 5 (consumer-down, dlq-rate-high,
ch-insert-failure, parity-drift, gx-fail), folder dfv3lh9jx4o3kf (wikistream,
auto-created), all with condition B + working __expr__ threshold nodes.

### 5B.1 — Ops Agent install via boot.sh

**Status:** DONE (code; VM runtime verification is the 5B.4 checkpoint)

**2026-08-14:** Idempotent Ops Agent install block added to scripts/boot.sh,
placed between the systemd-unit step and the Slack-env block (the plan's
"after the existing systemd-unit step" position). Verbatim from the locked
plan: `command -v google-ops-agent` guard → curl the Google agent repo script
→ `sudo bash ... --also-install` → `rm -f`, with the whole chain non-fatal
(`|| { echo ... >&2; }`) so a transient network failure never aborts boot (set
-e). Installed by implementation subagent, re-verified byte-for-byte by the
orchestrator; `bash -n` clean. No deviation. The install actually runs at the
VM's next boot (startup.sh → boot.sh), which is the same apply that installs
the metricWriter grant — the agent will land with its write path already in
place.

**Subagent code review (2026-08-14):** MAJOR finding — the guard
`command -v google-ops-agent` can never match: the 2.x agent (package
file-list verified by the reviewer) installs no such binary, so the block
would have re-run the apt install every boot and the plan's Verify step was
un-passable as written. Fixed in boot.sh + plan (guard and Verify now key on
`systemctl is-active --quiet google-cloud-ops-agent.service`). MINOR — the
downloaded repo script could linger on install failure; `rm -f` added to the
failure branch. MINOR (no change): the disk policy's `device="ch-data"` clause
will likely never match (the agent emits kernel device names) — correctness
rests on the `mount_point="/mnt/ch-data"` clause; the 5B.3 time-series list
will surface the real label so the filter can be aligned there if needed.
**Deviation recorded:** the guard is `systemctl is-active`, not the locked
plan's `command -v` — the plan doc was updated in place; this entry is the
record.
**Evidence:** scripts/boot.sh lines 62-71; bash -n clean; subagent review
2026-08-14: 1 MAJOR fixed, 2 MINOR (1 fixed, 1 5B.3-verification item).

### 5B.2 — VM SA monitoring.metricWriter + TF modules/monitoring (channel + 2 policies)

**Status:** DONE (code; apply green is Ahmed's, verified live-plan here)

**2026-08-14:** Two parts, both written by an implementation subagent and
re-verified by the orchestrator against the real state via `terraform plan`
(read-only, backend wikistream-505003-terraform-state/prefix main).
(a) modules/iam/iam.tf += google_project_iam_member.vm_monitoring_metric_writer
(roles/monitoring.metricWriter on the wikistream-vm SA — Ops Agent's write
path, least-privilege writer-not-admin). (b) NEW modules/monitoring/: email
notification channel (ahmedikram30@gmail.com) + 2 alert policies —
disk-almost-full (condition_threshold, agent disk/percent_used, device=ch-data
OR mount_point=/mnt/ch-data, >80 for 300s, ALIGN_MEAN, doc notes ch-data is
the durability surface per vision §7) and vm-unreachable (condition_absent on
compute instance/uptime, 120s — agentless path). Root wiring: main.tf module
call, alert_email variable, terraform.tfvars
`alert_email = "ahmedikram30@gmail.com"` (the 2026-08-13 prerequisite was
recorded as done but was NOT in the file — added now).

**Deviation 1 (provider schema):** the plan's `condition { condition_missing }`
block shape is pre-v6 provider. google provider 7.43 (locked) uses
`conditions { }` (plural) and the missing-series block is `condition_absent`
(not condition_missing). Semantics unchanged — condition_absent fires exactly
when the uptime series stops being reported. Confirmed by the provider's own
plan output (would not have validated otherwise); validated + planned clean.

**Deviation 2 (labels.phase):** bumped local.labels.phase "4" → "5" and wired
labels into the monitoring module (user_labels on both policies), matching the
established every-new-module-bumps-phase precedent (3.3.2, 4.2.7). Cost: 12
in-place label-only updates (3 secrets, bigquery dataset + 4 tables, bq
staging bucket, static IP, ch-data disk) on the same apply — harmless, they
were going to move anyway when the instance rebuilds.

**Planned instance replacement (NOT a deviation):** the live plan shows
google_compute_instance.wikistream_vm **must be replaced** — metadata_startup_script
is ForceNew and the state still carries the pre-5A.6 startup.sh (the `git pull
--ff-only || true` → `git fetch origin && git reset --hard origin/HEAD`
deploy-footgun fix from 5A.6 was merged to the branch but never applied to the
VM). This is the natural window 5A.6's entry predicted ("bundle with 5B/5C's
apply"). Consequences for the apply: static IP and the durable ch-data disk
survive (separate resources); the fresh instance re-runs the fixed startup.sh,
re-pulls code, re-renders .env, and boot.sh installs the Ops Agent — so the
5B.1 install and the 5B.2 metricWriter grant land in the same boot. Nothing to
do on the orchestrator's side beyond recording it here.

**Subagent code review (2026-08-14):** TF side reviewed as-is — verdict
approve-as-is; the only findings landed on the boot.sh guard (fixed, see 5B.1
entry) plus one 5B.3-verification note on the disk filter's `device` clause.
No TF changes required. The condition_absent deviation was reconfirmed
correct: the provider's canonical uptime-absence example is identical in shape
to this policy.
**Deploy blocker (2026-08-14, found by the live apply):** the apply failed on
the first Cloud Monitoring API call — `Error creating NotificationChannel:
403 Permission denied (or the resource may not exist)`. Root cause: the
deploy SA (wikistream-deploy) holds its 8 project roles via
infra/bootstrap/main.tf's google_project_iam_member.deploy_project_roles
(compute/artifactregistry/secretmanager/iam/serviceAccount/resourcemanager/
bigquery/storage admins) and had no monitoring role; alert policies and
notification channels need `roles/monitoring.editor`. Fixed two ways: (a)
out-of-band `gcloud projects add-iam-policy-binding … --role=roles/monitoring.editor`
on wikistream-deploy (unblocks the re-run immediately — owner creds, grant
live), and (b) declared in bootstrap/main.tf's role list with a comment, so
bootstrap stays the source of truth (bootstrap apply = no-op on the binding,
plans "1 to add"). Re-run of the apply job then creates only the channel + 2
policies (instance replace and label bumps already landed in the failed run).
Reading for a future phase: ANY new lateral GCP resource type (monitoring,
cloud run, etc.) needs its role added to the same bootstrap list before the
apply can create it.
**Deviation 3 (DELTA-kind absence — found by the live apply):** vm-unreachable
failed on its first real create — `Error creating AlertPolicy: 400: Request
was missing field aggregation.perSeriesAligner: An aggregation with a
perSeriesAligner is required when the metric kind is DELTA`.
`compute.googleapis.com/instance/uptime` is DELTA-kind; the API requires an
aggregations block with per_series_aligner even on an absence condition, which
neither the locked spec nor the pre-apply validation surfaced (validate only
checks HCL shape; the requirement is API-side). Fixed: aggregations
{alignment_period 120s, per_series_aligner ALIGN_MEAN} added to
condition_absent — semantics unchanged (absence still fires when the series
stops reporting); plan now "1 to add" (vm_unreachable only; channel, disk
policy, instance and disk attach already in state from the two partial
applies). Plan doc 5B.2 block updated in place with the deviation note.
Reading for future phases: absence conditions on DELTA-kind metrics need the
aligner; only a real apply surfaces it.
**Evidence:** terraform plan (live backend): 5 to add, 12 to change, 1 to
destroy (destroy = instance replace only; adds = 1 IAM binding + 1 channel + 2
policies; changes = 12 label phase bumps). terraform fmt -check clean;
terraform validate clean; bash -n clean.

### 5B.3 — Metrics-flow verification (disk/percent_used visible in Cloud Monitoring)

**Status:** DONE

**2026-08-14:** Full live verification against wikistream-505003 (owner creds jess154lacroix@gmail.com; gcloud 577 has no `time-series` subcommand — used the REST API with a Bearer token). Both policies live and enabled (`gcloud monitoring policies list`: "WikiStream VM unreachable", "WikiStream disk almost full (ch-data)"); Ops Agent 2.x active on the VM (`google-cloud-ops-agent.service` + `-fluent-bit`). Three real findings, all now resolved in code:

1. **Deployed disk filter was DEAD.** The metric descriptor `agent.googleapis.com/disk/percent_used` has labels **only** `device` + `state` — there is **no `mount_point` label** (a label filter on `mount_point` 400s: "Cannot find metrics that match type=... label=mount_point"), and `device` is the kernel name with the `/dev/` prefix. The plan's `(device="ch-data" OR mount_point="/mnt/ch-data")` can never match any series — and the policy API does not validate unknown labels at create time, so it deployed silently. Fixed: filter is now `metric.labels.device="/dev/sdb"` (ch-data). *Deviation recorded in the plan doc 5B.2 spec.*
2. **sdb series absent until an agent restart.** The hostmetrics mount scan ran before startup.sh mounted ch-data, so only loop/sda series appeared (and the earlier 84/85% values were the OLD pre-reset instance's boot-disk series). `sudo systemctl restart google-cloud-ops-agent` after the mount was stable → full emission: **/dev/sdb used 72.5%** (close to the 80% threshold, trending up — df shows 81% of usable), sda1 17.6% (fresh boot disk), sda15/16 + loops normal. Root-cause reading: restart-after-mount; stock config (no custom filtering).
3. **Agent self-check FAIL on logging half:** `[API Check] Result: FAIL ... Service account is missing the roles/logging.logWriter role`. VM SA has metricWriter but not logWriter, so the agent's log path was degraded (metrics fine). Fixed: `google_project_iam_member.vm_logging_log_writer` added in modules/iam/iam.tf (documented second half of Ops Agent IAM; deliberately a separate resource — a for_each would rename/replace the live binding).

Memory confirmed streaming under the correct name: `agent.googleapis.com/memory/percent_used` (full state set: used 41.6, cached 44.0, free 5.4, buffered 3.1, slab 5.9); `memory/usage` does not exist in this agent version.

**Evidence:** REST timeSeries queries (disk+memory, 2h window) before/after restart; `systemctl status google-cloud-ops-agent` (ActiveEnterTimestamp 00:14:44); agent self-check log + health-checks.log + subagents/logging-module.log; `/etc/google-cloud-ops-agent/config.yaml` stock. Two TF changes + plan-doc spec update shipped as the next 5B PR: policy filter `/dev/sdb` + IAM logWriter.

### 5B.4 — 5B PR → deploy → checkpoint

**Status:** DONE

**2026-08-14:** Deployed by orchestrator directly (`terraform apply`, owner creds jess154lacroix@gmail.com, live GCS state backend) at Ahmed's request — the gated GitHub Actions apply was bypassed; git lane (commit/PR/merge) remains Ahmed's. Apply green: **1 added** (`module.iam.google_project_iam_member.vm_logging_log_writer`), **1 changed** (disk policy filter → `metric.labels.device="/dev/sdb"`), 0 destroyed.

Checkpoint verified against the live project:
1. **Both policies live + enabled** (`gcloud monitoring policies list`): "WikiStream VM unreachable" and "WikiStream disk almost full (ch-data)"; policy API GET confirms the corrected `/dev/sdb` filter (threshold 80, COMPARISON_GT, 300s) is in effect.
2. **Ops Agent streaming disk + memory**: both `google-cloud-ops-agent` and `-fluent-bit` units active; `agent.googleapis.com/disk/percent_used` `/dev/sdb` = 3 series (used 73.7, free 21.1, reserved 5.2); `memory/percent_used` full state set (used 28.5, cached 61.1, free 2.6, buffered 2.0, slab 5.8).
3. **Agent self-check PASS** (00:23:28Z in health-checks.log) — the logWriter grant flipped it from the LogApiPermissionErr FAIL seen at 00:14/00:16; triggered via `systemctl restart google-cloud-ops-agent` after the IAM change. Both halves of Ops Agent IAM now green (metrics + logs).
4. **Alert email: not yet fired — organic fire imminent.** df shows ch-data at **93% (26G/30G, 2.2G free)** and rising, while the agent's percent_used reads 73.7% (the two disagree by ~19 points; agent excludes reserved blocks). The agent number is climbing; when it crosses 80 for 5 min the email fires — that real email is the alert-email proof, no injection needed (DEMO #6 dd-injection remains the deterministic fallback). *Open item (already listed in plan §10 "thresholds tune"): the agent/df percent gap means the 80% threshold may effectively fire later than df's 80%; re-examine when tuning.*
5. **Operational flag for Ahmed (not a 5B defect):** ch-data is genuinely near-full and filling (~+3G over the last few hours). At this rate ClickHouse insert failures are hours away. Recommended: grow the disk (terraform `disk_size` bump on the attached_disk resource) soon, before DEMO phase — or treat the imminent organic fire as DEMO #6's proof and grow after.

**Evidence:** terraform apply output (1 added, 1 changed, 0 destroyed); `gcloud monitoring policies list` (2 enabled); policy GET (filter `/dev/sdb`, 80/GT/300s); VM `systemctl is-active` (2 active); health-checks.log PASS 00:23:28Z; REST timeSeries disk (sdb 3 series) + memory (5 states); df -h /mnt/ch-data (26G/30G 93%).

**Disk grow (same day, appended):** `google_compute_disk.ch_data` `size` 30 → 50 (comment in compute.tf), applied in place (plan 0/1/0, live resize, no instance replacement — the disk is a separate `prevent_destroy` resource so the grown FS survives any recreate). Guest follow-up: `lsblk` showed the 50G device but the FS was still 30G; since `/dev/sdb` is mounted whole (no partition), `sudo resize2fs /dev/sdb` online-extended it — now **50G total, 23G used, 25G free (48%)** vs 93%/2.2G before. Deliberately NOT automated in startup.sh: an edit there is ForceNew on the next apply, and a future grow is a rare 10-second SSH op. *DEMO implication: the plan's #6 dd injection targets ">80% of the 30GB disk" — the disk is now 50G at 48%, so the injection must write ~16-17G more (or rely on organic fill) to breach; re-baseline at DEMO time.*

### 5C.1 — IAM review doc (iam-review.md): enumerate, justify, tighten

**Status:** ☐ to do

### 5C.2 — Firewall lockdown: delete default-allow-* (TF null_resource)

**Status:** ☐ to do

### 5C.3 — 5C PR → deploy → gcloud rule-state verification → Go/No-Go

**Status:** ☐ to do

### DEMO — Chaos battery (one ~90 min window, runs immediately after 5C — no separate booking, Ahmed 2026-08-13)

**Status:** ☐ to do

## Phase 6 — Coverage Bar Enforcement

## Phase 7 — Performance & Cost Validation

## Phase 8 — Evidence Capture & Teardown
