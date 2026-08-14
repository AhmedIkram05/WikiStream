# Phase 6 Implementation Phase Plan — Coverage Bar Enforcement

**Status:** LOCKED — 2026-08-14. No edits without a decision record.
**Reviewed by:** code-review subagent (post-implementation).
**Position in hierarchy:** Research Notes → Vision & ADR → master-plan.md (§5, Phase 6) → coverage-boundary.md (the gate spec) → this plan. ADR-009 closes out here.
**Branch:** `feature/Coverage-Bar-&-Cost-Validation` (1 commit ahead of main: 6bd859e Kafka composite cursor).

---

## 1. Objective

"Turn the bar on, don't teach testing." Per master plan §5: tests were written continuously through Phases 1–5; this phase wires pytest-cov into CI with **both gates enforced** (100% line coverage on the business-critical modules from coverage-boundary.md, ~90% overall on `src`), **closes the measured gaps**, and **proves the gate actually blocks** — not just reports.

Exit bar: both gates enforced in CI and passing; the gate demonstrably blocks under-covered code (local reproduction of the exact CI command); gap-closing tests land; final coverage numbers recorded in the implementation log.

Prerequisites check (all satisfied, recorded in log): Phases 4 and 5 substantially complete (5 GO recorded, Gate 2 GO-with-caveat recorded in log §4.2.9 — GX enforcement deferred to this phase by plan).

## 2. Scope

| In | Deliberately out |
| --- | --- |
| Extend `BUSINESS_CRITICAL_MODULES` in ci.yml to the full Python boundary set | `consumer.py` at 100% — it is NOT a boundary module; governed by the ~90% overall gate |
| Add the ~90% overall gate (`--cov=src --cov-fail-under=90`) to the unit-tests job | SQL artifacts (`migrations/**`, `warehouse/sql/**`) as pytest-cov targets — line coverage is N/A for SQL; they are enforced by their exercising ch suites (tests/migrations, tests/mv, tests/warehouse execute the real files; compose-smoke applies them via apply.sh). This is the documented mechanism per coverage-boundary.md |
| Add the gx/suite.py 100% gate (unit + ch combined) to the analytics-tests job | `gx`'s internal GX-library machinery coverage (we measure our suite, not great-expectations) |
| Close measured gaps: consumer.py mock-loop tests, healthcheck main() unit tests, batcher/models edge lines | VM deploy / infra change — Phase 6 is CI-only; consumer image is unchanged, no apply needed |
| Prove the gate blocks (exact-command local reproduction, restore, re-pass) | `# pragma: no cover` as a silent escape hatch — exceptions are documented in coverage-boundary.md + log, never pragma'd by default |

## 3. Locked decisions

| # | Decision | Rationale |
| --- | --- | --- |
| Q1 | Overall gate lives in the **unit-tests** job as `--cov=src --cov-fail-under=90` on the non-ch run | Matches the seam already commented in ci.yml lines 23–28. Hermetic, fast, no CH dependency for the gate. Consumer loop gets mock-driven tests (httpx2 stream + fake CH client) so the gate is deterministic |
| Q2 | Per-module 100% gate list: `src.sse,src.models,src.batcher,src.dead_letter,src.heartbeat,src.healthcheck` | All six reach 100% **unit-only** after the gap tests in this plan (healthcheck via monkeypatched `get_client` + recorder'd `os.kill`). heartbeat/sse/dead_letter already measured 100% |
| Q3 | gx/suite.py 100% gate lives in the **analytics-tests** job, measured with plain `coverage` (unit in-process + ch subprocess combined via `COVERAGE_PROCESS_START` + `coverage combine`) | suite.py's GX branch (validator expectations, ~lines 192–266) runs for real only under CH. Subprocess measurement is the documented coverage.py pattern; `coverage` added to gx dev deps |
| Q4 | `consumer.py` stays out of the 100% list (not in coverage-boundary.md) — its real coverage comes from the ch kill/resume + malformed suites, its hermetic coverage from the new mock-loop tests | Boundary file is locked at Phase 0; consumer.py was never a boundary module. Both copies of `_cursor_ts`/`_max_id` (consumer.py + batcher.py, diverged by 6bd859e) get covered |
| Q5 | Gate-blocks proof = local reproduction of the exact CI command (temporarily delete a live line → gate fails → restore → passes), recorded in log. Optional CI demonstration via a throwaway PR at Ahmed's call | Self-checkable by an agent without GitHub push rights; same command semantics CI runs |
| Q6 | No `pragma: no cover` unless a line is genuinely untestable — then it's a documented exception (coverage-boundary.md amendment + log entry), per master plan §5 | Master plan: "documented, defensible exception — not a silent scope-down" |

Handoffs that are constraints, not choices:

- master-plan.md §5 Phase 6 wording: both gates enforced and passing; gate proven to block.
- coverage-boundary.md module table: the set of modules is the deliverable; 100% = LINE coverage per module.
- ci.yml env comment (lines 23–28) already prescribes the extension shape; keep the comment honest after the edit.
- Log entry format (implementation-log.md): `### 6.x — name` / Status / date / Evidence.

## 4. Prerequisites

- [x] Gate 2 GO-with-caveat recorded (log §4.2.9); Phases 4/5 done.
- [x] Local ClickHouse up (`docker compose up -d clickhouse` + `bootstrap-user.dev.sql`); local consumer container stopped during test runs (contamination protocol).
- [x] Baseline measured: unit-only src 74% (125 miss / 483 stmts); combined unit+ch 81% (91 miss). consumer.py unit 52% (105 miss); healthcheck unit 55% (17 miss); batcher 97% (miss 37–38); models 97% (miss 80).

Build-time re-verification checklist (re-run before Gate):

- [ ] `uv run --project consumer pytest -q --tb=short -m "not ch"` — green (incl. new tests)
- [ ] Consumer + healthcheck ch suites still green: `-m ch --ignore=tests/gx` (CH up, consumer stopped)
- [ ] `uv run --project gx pytest -q --tb=short -m ch tests/gx` — green
- [ ] `pre-commit run --all-files` — clean

## 5. Target file structure

```
consumer/src/                    UNCHANGED (no production edits in Phase 6)
tests/src/consumer/
  test_consume_loop.py           # NEW — mock-driven consume_forever/main() coverage
  test_batcher.py                # EDIT — _cursor_ts/_max_id edge tests (miss 37-38)
  test_healthcheck.py            # EDIT — main() unit tests (monkeypatched get_client)
tests/src/consumer/test_validation.py  # EDIT — models.py line-80 test (else → timestamp_unparseable)
tests/gx/
  test_suite_main.py             # NEW — in-process main() pre-GX paths (no CH)
  test_gx_suite.py               # EDIT — subprocess command becomes `coverage run --append -m gx.suite`
gx/pyproject.toml                # EDIT — dev deps += coverage
.github/workflows/ci.yml         # EDIT — extended gate list, overall gate, gx coverage gate
docs/planning/coverage-boundary.md  # EDIT only if an exception is documented (none expected)
docs/implementation-log.md       # EDIT — Phase 6 entries under the existing header
```

## 6. Tasks

| # | Task | Status |
| --- | --- | --- |
| 6.1 | `tests/src/consumer/test_consume_loop.py`: FakeAsyncClient (httpx2.stream, status/headers/aiter_bytes) + FakeCHClient (insert/query) + recorder'd `os.kill`; cover `_parse_retry_after` (valid/invalid/missing), `_cursor_ts` (non-JSON, list-of-non-dicts), `_max_id` (None b, JSON branch), `load_state` (non-dict, wrong-typed fields), `save_state` failure path, full consume_forever flow (valid events → flush; bad JSON → DL; bad ts → DL; ValidationError → DL; flush exception → insert_failed/batch-dropped; non-200 → Retry-After → reconnect; transport exception → reconnect; stop at chunk boundary; 60s idle stats line; final flush on stop; `_dl_fields` dict branch), main() (cold-start failure via `_sleep_or_stop→True`; happy path with stop set by fake task; wait_for TimeoutError → cancel) | |
| 6.2 | test_batcher.py: `_cursor_ts` except branch (invalid JSON id, list of non-dict entries) — lines 37–38 | |
| 6.3 | test_validation.py: models.py line 80 — `validate_timestamp` else branch (ts = dict / list / date) | |
| 6.4 | test_healthcheck.py: main() unit tests — stale path (fake client + recorder, SIGTERM captured), fresh path (no kill), `get_client` raising → 1, bad `CLICKHOUSE_PORT` → 1 | |
| 6.5 | gx: add `coverage` to gx dev deps; test_suite_main.py (password missing → 1, connect fail → verdict error, count-query fail missing-table → verdict error, other query error, row_count 0 → skipped, row bounds out → fail); test_gx_suite.py subprocess → `coverage run --append -m gx.suite`; verify COVERAGE_PROCESS_START + combine mechanics locally | |
| 6.6 | ci.yml: `BUSINESS_CRITICAL_MODULES: src.sse,src.models,src.batcher,src.dead_letter,src.heartbeat,src.healthcheck` + overall gate line `--cov=src --cov-fail-under=90` + analytics-tests gx coverage-gate steps (unit in-process → ch → combine → `report --fail-under=100 --include='*/gx/suite.py'`); update the env comment to present tense | |
| 6.7 | Gate-blocks proof: run the exact CI gate command with one live line temporarily removed from a boundary module → expect failure with missing-line report → restore → expect pass. Record both outputs in the log | |
| 6.8 | Final measurements (unit-only and combined), log entries (6.1–6.8), coverage-boundary.md untouched (no exceptions) | |

## 7. Acceptance criteria

| # | Criterion | How an agent verifies it |
| --- | --- | --- |
| AC1 | Unit suite green with new tests | `uv run --project consumer pytest -q --tb=short -m "not ch"` → all pass |
| AC2 | Per-module 100% gate passes unit-only | `uv run --project consumer pytest -q --tb=short -m "not ch" --cov="src.sse,src.models,src.batcher,src.dead_letter,src.heartbeat,src.healthcheck" --cov-report=term-missing --cov-fail-under=100` → exit 0; term-missing shows no missing lines in those modules |
| AC3 | Overall ~90% gate passes unit-only | same + `--cov=src --cov-fail-under=90` → exit 0; measured % ≥ 90, recorded in log |
| AC4 | gx/suite.py 100% gate passes (combined) | with CH up: `COVERAGE_PROCESS_START=<tmp>/.coveragerc coverage run -m pytest -q -m "not ch" tests/gx` (project gx) → `coverage run --append -m pytest -q -m ch tests/gx` → `coverage combine` → `coverage report -m --fail-under=100 --include='*/gx/suite.py'` → exit 0 |
| AC5 | ch suites still green | `uv run --project consumer pytest -q --tb=short -m ch --ignore=tests/gx` and `uv run --project gx pytest -q --tb=short -m ch tests/gx` (CH up, consumer container stopped) → pass |
| AC6 | Gate proven to block | 6.7 reproduction recorded in log: exact command fails with under-coverage on the deliberately broken line, passes after restore |
| AC7 | Lint clean | `pre-commit run --all-files --show-diff-on-failure` → clean |
| AC8 | Log written | docs/implementation-log.md Phase 6 header has entries 6.1–6.8 (Status/date/Evidence), final measured numbers recorded |

## 8. Verification gates

Master plan §5, Phase 6: "Both gates enforced and passing; the gate proven to actually block, not just report." Risk note: "if the 100% bar proves genuinely impractical for some path ... that's a documented, defensible exception — not a silent scope-down."

- Gate A: AC1–AC3 green locally with the exact CI commands.
- Gate B: AC4–AC5 green (CH-dependent gates).
- Gate C: AC6 evidence recorded (block → fix → pass).
- Gate D: AC7–AC8 (lint, log).

**Go/No-Go (final step):** to be recorded GO-with-caveat against AC1–AC8 with evidence pointers in the implementation log, or No-Go with a recorded, understood fix per failed AC. Cross-references Gate 2's GO-with-caveat (log §4.2.9) — this phase closes the deferred GX-enforcement item.

## 9. Troubleshooting notes

- pytest-cov `--cov=pkg1,pkg2` accepts a comma list in one flag — keep the existing env seam shape.
- The gx gate needs `coverage` importable in the **gx** venv only; consumer's pytest-cov is unaffected (separate venvs, no shared .coveragerc at repo root — a root `[run] parallel=true` would break pytest-cov's existing gates; the gx config file lives under /tmp in CI, not the repo).
- Subprocess coverage requires `COVERAGE_PROCESS_START` to reach the `python -m gx.suite` child — test_gx_suite.py must pass it through `**os.environ` (it already passes env through).
- `coverage run --append` twice (non-ch then ch) is fine — combine merges all parallel data files; `--data-file` isolates the gate from any stray `.coverage`.
- If consumer.py mock-loop tests fight asyncio internals (signal handlers need the main thread): monkeypatch `_sleep_or_stop`/`consume_forever`/`heartbeat_loop` at the module level instead of fighting the event loop; never patch inside `consumer.py` itself.
- Local ch runs drop tables via reset() — re-apply `migrations/apply.sh` after; restart the local consumer container after test runs (contamination protocol).
- If a single line refuses coverage after honest effort: document the exception in coverage-boundary.md + log (Q6), do not pragma it.

## 10. Handoff to Phase 7

Inherits: verified gate commands, exact CI command shapes, mock-test patterns (reused by the 7a burst harness), local CH discipline (consumer stopped during runs), log format discipline.

Open items / known ceilings:

- `consumer.py` stays at "covered by ~90% overall + ch suites", not 100% — recorded decision, not debt.
- SQL modules enforced via exercising suites, not line coverage — documented mechanism.
- Optional CI-level block demonstration (throwaway PR) left to Ahmed's call; the local reproduction is the recorded proof.

## 11. Interview talking points

- "ADR-009 closed out: two-tier coverage gate (100% on six business-critical modules, 90% overall) enforced by CI, with a recorded proof that the gate blocks under-covered code — not just a report."
- "Made the 100% bar honest for Python: mock-driven consumer-loop tests + real-ClickHouse subprocess coverage for the GX suite, combined with coverage.py's process-start mechanism."
- "SQL can't be line-covered — enforced the DDL/export artifacts through suites that execute the real files against a live ClickHouse."
