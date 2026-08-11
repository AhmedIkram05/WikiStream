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

**Status:** DONE
**2026-08-10:** `consumer/src/sse.py` — `SSEEvent` + `SSEParser`, stdlib only (`dataclasses`, `codecs`), WHATWG-compliant: trailing-`\r` hold (a buffer-ending `\r` that could pair with a following `\n` is held, so `\r\n` split across chunks can't fabricate a frame boundary), CRLF = one terminator, bare `\r` is a terminator, comment-only frames don't dispatch (`_has_data` flag; explicit empty `data:` still emits), strict UTF-8 decoder. `consumer/src/consumer.py` — main loop: httpx2 streaming GET with `Last-Event-ID` header, per-connection `SSEParser`, batch insert via `clickhouse_connect.get_async_client` with `settings={"async_insert": 1, "wait_for_async_insert": 0}`, SSE `retry:`/`Retry-After` backoff, log lines matching the verification contract (`INFO  connected url=`, `WARNING reconnect reason=`, `WARNING insert_failed event=`, `INFO  inserted events=<n> total=<cum>`). `consumer/Dockerfile` (python:3.13-slim) + `consumer/requirements.txt`.
**DEVIATION (2026-08-10):** `clickhouse-connect` pinned `==1.6.0`, not the plan's `2.1.0` — 2.1.0 does not exist on PyPI; 1.6.0 is the latest real release ≥1.6.0 (verified via pypi.org JSON). `httpx2==2.10.0` verified real (maintained httpx fork).
**DEVIATION (2026-08-10, C1):** Wait formula differs from plan §6.1's literal `min(retry_ms or 1.0, retry_after or 30.0)` — with `retry_ms` unset (before the first SSE retry hint) and `Retry-After: 45` on a 429/503, that formula yields `min(1.0, 45.0)` = 1s, the exact 1s-hammering the plan's intent sentence forbids. Implemented: `min(max(retry_ms/1000 if retry_ms else 1.0, retry_after if retry_after else 0.0), 30.0)` — SSE retry hint is the floor, `Retry-After` is honored and capped at 30s. Intent sentence governs over the stale formula.
**Judgment calls (reviewed, kept):** log format `%(levelname)-5s %(message)s` pads `INFO` so both contract greps match literally; `parser.flush()` dropped (fresh parser per connection — flush would be dead code); `Retry-After` parsed as int seconds only (HTTP-date form → 30s cap); `CLICKHOUSE_HOST` unset → `localhost` (passing `None` would yield `http://None:8123`).
**CORRECTION (2026-08-10):** the "client init is lazy, CH cold-start surfaces only as caught insert failures" claim above is **wrong for clickhouse-connect 1.6.0** — `get_async_client` eagerly runs `SELECT version(), timezone()` at creation (asyncclient.py `_initialize` → init_sequence over HTTP :8123). During CH cold-start this raises `OperationalError` uncaught → traceback → exit 1 → compose restart. See the 1.6 fix entry below.

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

**2026-08-11:** `infra/bootstrap/main.tf` extended and applied (ADC, project `wikistream-505003`): 7 `google_project_service` enables (compute, artifactregistry, secretmanager, iamcredentials, sts, oslogin, cloudresourcemanager) via for_each; WIF pool `wikistream-ci` + provider `github` (attribute_condition `assertion.repository == "AhmedIkram05/WikiStream"`, issuer `https://token.actions.githubusercontent.com`); deploy SA `wikistream-deploy` (5 project roles via for_each + `storage.objectAdmin` scoped to the tfstate bucket only); WIF binding (`principalSet` on project number 984854414993, `roles/iam.workloadIdentityUser`); AR repo `wikistream-consumer` (us-central1, DOCKER). `outputs.tf` added: `wif_provider_name`, `deploy_sa_email`. **AC1 verified:** pool ACTIVE; provider name `projects/984854414993/locations/global/workloadIdentityPools/wikistream-ci/providers/github`; SA exists with all 5 project roles; AR repo exists (DOCKER); both outputs resolve to exactly the values hardcoded in the workflows. **DEVIATIONS:** (1) AR repo creation initially failed in the same apply with "Artifact Registry API has not been used before" — API-activation propagation race (the 7 enables completed in the same apply); re-applied after ~90s, repo created, zero manual console steps. (2) `data "google_project"` initially used `project = var.project_id` — provider rejects `project` on this data source; fixed to `project_id`. (3) Plan-scope deviation carried forward from the README note: bootstrap owns identity + AR repo in addition to the bucket (plan Q1, ADR-007 "bucket only" superseded by plan decision). (4) Pre-commit CI-access audit: deploy SA initially lacked `resourcemanager.projects.setIamPolicy` — infra/main's `oslogin_human` binding (roles/compute.osLogin, project-level IAM, added at review) would have failed the first gated CI apply with "Permission resourcemanager.projects.setIamPolicy denied" (none of the 5 original roles include it). Added `roles/resourcemanager.projectIamAdmin` to the deploy SA (6 project roles now) and re-applied bootstrap. WIF provider name + SA email outputs unchanged.

### 2.2 — Main config + modules + startup script (Q2/Q4/Q5/Q6/Q9/Q10)

**Status:** DONE (implementation + local verification; deploy verification in 2.5–2.7)

**2026-08-11:** `infra/main/` created: `main.tf` (backend gcs `wikistream-505003-terraform-state` prefix `main`, google >= 7.43 + random providers, labels local `{project=wikistream, managed-by=terraform, phase=2}`, 4 module calls, `vm_static_ip` output), `variables.tf`, committed `terraform.tfvars` (allowed_ips = Ahmed's current IP 209.35.91.152 per plan Q6), `templates/startup.sh`, and the 4 modules (network: VPC + subnet 10.0.0.0/24 + 4 firewall rules allow-internal/ssh/grafana/clickhouse — the last three from `allowed_ips`; compute: static IP + e2-medium/50GB pd-standard/ubuntu-2404-lts + OS Login `enable-oslogin=TRUE` + cloud-platform scope; iam: `wikistream-vm` SA + per-secret secretAccessor on the two secrets; storage: AR reader on `wikistream-consumer`). `terraform init` (real backend) + `validate` + `plan` clean: **18 to add, 0 change, 0 destroy**; startup script injected verbatim; firewall source ranges correct; labels applied on every label-capable resource. Lockfile `.terraform.lock.hcl` generated (pinned at build, committed). **DEVIATION (CRITICAL, plan recipe broken):** plan §Q2's apt recipe is invalid on ubuntu-2404-lts — `google-cloud-cli` and `docker-compose-plugin` do NOT exist in Ubuntu 24.04 default repos (apt EXIT=100 "Unable to locate package", verified in container). Verified working recipe (container-tested): gcloud via Google's apt repo (packages.cloud.google.com, keyring + `cloud-sdk main` source, `apt-get install -y google-cloud-cli`, got 579.0.0); compose plugin via **`docker-compose-v2`** package (got 2.40.3, registers `/usr/libexec/docker/cli-plugins/docker-compose`); docker.io + git from Ubuntu repos (29.1.3 / 2.43.0). Script otherwise verbatim plan (umask 077, tee to /var/log/wikistream-startup.log + serial console, metadata-server project id, secrets fetch, initdb.d heredoc, .env render, configure-docker, `compose pull` + `up -d --no-build`). **Labels finding (plan-compliant):** provider 7.43 exposes no `labels` field on network/subnet/firewall/SA/IAM resources — labels applied to instance, static address, and both secrets; verified against pinned provider source.

**Dress rehearsal (local, pre-apply):** full startup.sh run inside ubuntu-24.04 container with shims (metadata curl → project id, gcloud secrets → 24-char values, configure-docker/compose pull/systemctl → no-op): clone OK; rendered `.env` and `docker/clickhouse/initdb.d/001-init.sql` byte-correct (wikistream user, plaintext_password, scoped grants, raw_events MergeTree ORDER BY inserted_at); both files 0600 (umask proven); log ends `startup done`. Stage 2 (host-side `docker compose up -d --no-build` on the rendered tree — exactly what the VM runs): 3 services Up, consumer connected + inserting, count() 2,682 → 3,613 over 20s, `SELECT 1` via :8123 as wikistream with the rendered password OK, Grafana serving (302 → /login). **RESTORED LOST FIX (findings later phases depend on):** the Phase 1.6 cold-start retry fix (client-init retry loop, `clickhouse_unavailable` WARNING) was absent from `consumer/src/consumer.py` on both main and this branch — verified absent in git history (single consumer commit c93228d; zero `clickhouse_unavailable` occurrences anywhere). Restored it verbatim per the 1.6 log entry; post-fix cold-start re-test on wiped volume: RestartCount=0, 0 Traceback, 2 expected `clickhouse_unavailable` WARNINGs during CH boot, clean inserts after. 19/19 unit tests still pass. (Restoration, not new code; coverage boundary unchanged.)

**2026-08-11 Subagent code review (2 parallel reviewers, code-reviewer type) — REQUEST-CHANGES / APPROVE-WITH-FIXES, all findings fixed or recorded:**
- **BLOCKER (fixed):** `terraform.tfvars` `allowed_ips` was a bare IP, not CIDR — GCP firewall API rejects at apply (Error 400 "Must be a CIDR address range"; plan passes, apply fails — terraform#30749). Fixed: `["209.35.91.152/32"]` + `validation` block in `variables.tf` (`can(cidrhost(c, 0))`), so a bad value now fails at plan time.
- **SHOULD-FIX (fixed):** `git pull --ff-only || true` wedges silently forever the first time any commit touches the tracked `docker/clickhouse/initdb.d/001-init.sql` (rendered secret → permanently dirty tree → ff-only refuses, `|| true` swallows it — simulated & verified). This is exactly the Phase 3A PR that retires initdb.d. Fixed: `git -C /opt/wikistream checkout -- docker/clickhouse/initdb.d/001-init.sql 2>/dev/null || true` before the pull (re-rendered right after anyway).
- **NIT (fixed):** metadata-server curl now `-fsS` (empty body would otherwise silently produce a malformed `CONSUMER_IMAGE`).
- **NIT (fixed):** `.gitignore` now covers `*.tfplan` (13 KB binary artifact was about to ship with the PR).
- **NIT (fixed):** module iam now binds `roles/compute.osLogin` for the human account (`oslogin_human_member = "user:jess154lacroix@gmail.com"` in tfvars) per plan §4:74 — previously only worked because owner covers it. Plan went 18 → 19 resources.
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

**2026-08-11 Pre-commit hooks — were never installed.** `.git/hooks/pre-commit` did not exist (only `.sample`), so no commit ever ran the hooks; and even if installed, the ruff hook failed on BLE001 (not auto-fixable) + the format drift above. Fixed by: the ruff config changes above (hook now green), and `pre-commit install` (registered 2026-08-11). `pre-commit run --all-files` — all 8 hooks green. The config comment in `.pre-commit-config.yaml` was updated (ruff config now lives in `consumer/pyproject.toml` + root `ruff.toml`; version pinned 0.16.2 in BOTH ci.yml and the hook rev — universal).

### 2.5 — First deploy through the gate

**Status:** in progress (gated apply run #1 FAILED at terraform apply; fix applied, re-run pending)

**2026-08-11:** PR #4 merged to main → `apply.yml` ran: job 1 build-push SUCCESS (consumer image pushed to AR as `sha-<commit>` + `:latest` — AC3). Job 2 (`apply`, environment `production`) showed **Waiting** in the Actions UI and was approved by AhmedIkram05 (AC4 evidenced in run #1: approval identity + timestamp in the run's timeline) — then `terraform apply` **FAILED**:
`Error: Error creating service account: googleapi: Error 403: Identity and Access Management (IAM) API has not been used in project 984854414993 before or it is disabled...` on `module.iam.google_service_account.wikistream_vm` (modules/iam/iam.tf:1).
**DEVIATION (plan gap, Q1 API list incomplete):** the bootstrap list enabled 7 APIs but NOT `iam.googleapis.com` — service-account CRUD needs it; the deploy SA's roles cannot auto-enable APIs. The failed run had already created 3 resources (VPC, subnet, allow-internal firewall) — those remain in state; re-run continues from there. Fix: added `iam.googleapis.com` as the 8th API in `infra/bootstrap/main.tf` (with in-file comment), bootstrap re-applied (1 added, outputs unchanged), API enablement verified propagated (2026-08-11). Re-run = workflow_dispatch on the Apply (GCP) workflow → approval → resume.
Also noted (informational): runner log shows Node 20 deprecation notice — Actions now default to Node 24; no action needed, pins unaffected.


### 2.6 — Deploy-path proof (Q2): reset → recover

**Status:** pending

### 2.7 — Destroy-and-reapply cycle (Q8)

**Status:** pending

### 2.8 — Wrap-up

**Status:** pending

## Phase 3 — Data Model Depth (3A Schema / 3B Analytics)

## Phase 4 — Data Quality & Resilience

## Phase 5 — Observability & Security Hardening

## Phase 6 — Coverage Bar Enforcement

## Phase 7 — Performance & Cost Validation

## Phase 8 — Evidence Capture & Teardown
