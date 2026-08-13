# Coverage Boundary — Business-Critical Modules (ADR-009)

**Status:** Locked at Phase 0 (2026-08-09). The boundary is fixed now; the
paths below are predicted and will be corrected when Phase 1 lands the real
structure. The *set* of modules is the deliverable — Phase 6 wires pytest-cov
to enforce **100% line coverage** on exactly these, **~90% overall** elsewhere.

**2026-08-10 (Phase 1 correction):** `consumer/src/sse.py` landed at the
predicted path and is verified at **100% line coverage** (19/19 tests pass,
79/79 statements — Phase 1 AC9). The remaining consumer paths
(`models.py`, `batcher.py`, `dead_letter.py`) do not exist yet; they stay
predicted and are corrected when their phases land them.

**2026-08-12 (Phase 3B correction):** `migrations/**` and the three MV
definitions (`004/005/006_mv_*.sql`, ADR-006) are now REAL paths, with
`tests/migrations/` (6 ch tests) and the new `tests/mv/` equivalence suite
(6 tests + 1 3C-aware skip) as their spot-check guarantee — MV output is
asserted equal to the equivalent raw GROUP BY, synthetically in CI and again on
live stream data at the 3B deploy spot-check.

**2026-08-13 (Phase 4A correction):** `consumer/src/models.py`,
`consumer/src/batcher.py` and `consumer/src/dead_letter.py` are now REAL
paths, with `tests/src/consumer/test_validation.py` (21 tests incl. two
real captured Wikimedia payloads), `test_batcher.py` (8), `test_dead_letter.py`
(2) and the ch suites `test_malformed_to_dead_letter.py` + `test_kill_resume_
zero_loss.py` as their spot-check guarantee. `gx/suite.py` stays predicted
until 4B lands it (4.2.2).

**2026-08-13 (Phase 4B correction):** `consumer/src/healthcheck.py` is now a
REAL path, with `tests/src/consumer/test_healthcheck.py` (7 pure unit tests
covering the `is_fresh` boundary matrix + 3 ch tests asserting the stale→SIGTERM
branch, fresh pass, and connection-failure exit) as its spot-check guarantee.
`gx/suite.py` is now a REAL path, with `tests/gx/test_gx_suite.py` (2 ch tests:
valid fixture → all six expectations pass, bad fixture → at least one fails and
the process exits non-zero) as its spot-check guarantee. `warehouse/backup.sh`
follows the export/parity wrapper convention: **outside the pytest-cov gate**,
verified by the production timer runs and the executed restore spot-check.

**2026-08-12 (Phase 3C correction, AC21 phase-final wording):**
`warehouse/sql/` and the new `tests/warehouse/` suite are now REAL paths.
The Phase 3 CQL story is complete: **`migrations/` + `warehouse/sql/` + the
three test suites (`tests/migrations`, `tests/mv`, `tests/warehouse`) form the
business-critical 100% line-coverage story** — the warehouse SQL is exercised
by the 3C ch suite (8 tests: export rollups, deterministic raw sample, CH-side
parity totals, BQ-dialect SQL shape, export_runs record shape, schema-drift
guards against `SHOW TABLES`/`DESCRIBE TABLE`), exactly as the Ingest+MVs pair
is. The bash wrappers (`warehouse/export.sh`, `warehouse/parity.sh`) are
**thick-enough orchestrators and deliberately OUTSIDE the pytest-cov gate** —
their exit codes and log lines are verified by the production timer runs
(parity's non-zero exit is the Phase 5 alert hook) and by `bash -n` +
mocked-dependency unit coverage in the 3C suite. The SQL files they execute
(and substitute `{START}`/`{END}` into) ARE in the 100% story via
`tests/warehouse`.

## The modules

| Module (predicted path) | Responsibility | Why business-critical |
| --- | --- | --- |
| `consumer/src/sse.py` | Hand-written SSE parser (ADR-004) | Malformed-stream handling is the "reconnects handled" claim; a parser bug drops the stream silently |
| `consumer/src/models.py` | Pydantic event models + inline validation | The first gate: bad events must be rejected pre-insert, never crash the consumer |
| `consumer/src/batcher.py` | Batch assembly + flush-trigger logic (size / interval) | Wrong flush logic = orphaned or lost events = the data-loss window |
| `consumer/src/dead_letter.py` | Dead-letter routing to `dead_letter` table | Schema-drift catch (ADR-004/005); a broken route makes drift look like success |
| `migrations/**` + MV definitions | Versioned DDL + materialized views (ADR-006) | A silently-wrong MV is ADR-006's named risk; assertions here are the spot-check guarantee |
| `warehouse/sql/**` | Hourly BigQuery rollup + parity SQL (ADR-003/010, Q6/Q7) | The BQ warehouse is the audit tier; a silently-wrong export SQL ships wrong numbers to BigQuery with no consumer error |
| `gx/suite.py` | GX suite configuration + execution | The periodic data-quality gate; a broken suite is a broken guarantee with no alarm |

## Enforcement notes (for Phase 6)

- 100% is line coverage per module, enforced as a CI gate that blocks merges.
- If a path proves genuinely untestable in practice (e.g. only reachable under
  a real network partition), that is a *documented, defensible exception* — not
  a silent scope-down (master plan Phase 6).
- Tests are written continuously through Phases 1–5; Phase 6 only turns the
  gate on and closes gaps.
