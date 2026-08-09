# Coverage Boundary — Business-Critical Modules (ADR-009)

**Status:** Locked at Phase 0 (2026-08-09). The boundary is fixed now; the
paths below are predicted and will be corrected when Phase 1 lands the real
structure. The *set* of modules is the deliverable — Phase 6 wires pytest-cov
to enforce **100% line coverage** on exactly these, **~90% overall** elsewhere.

## The modules

| Module (predicted path) | Responsibility | Why business-critical |
| --- | --- | --- |
| `consumer/src/sse.py` | Hand-written SSE parser (ADR-004) | Malformed-stream handling is the "reconnects handled" claim; a parser bug drops the stream silently |
| `consumer/src/models.py` | Pydantic event models + inline validation | The first gate: bad events must be rejected pre-insert, never crash the consumer |
| `consumer/src/batcher.py` | Batch assembly + flush-trigger logic (size / interval) | Wrong flush logic = orphaned or lost events = the data-loss window |
| `consumer/src/dead_letter.py` | Dead-letter routing to `dead_letter` table | Schema-drift catch (ADR-004/005); a broken route makes drift look like success |
| `migrations/**` + MV definitions | Versioned DDL + materialized views (ADR-006) | A silently-wrong MV is ADR-006's named risk; assertions here are the spot-check guarantee |
| `gx/suite.py` | GX suite configuration + execution | The periodic data-quality gate; a broken suite is a broken guarantee with no alarm |

## Enforcement notes (for Phase 6)

- 100% is line coverage per module, enforced as a CI gate that blocks merges.
- If a path proves genuinely untestable in practice (e.g. only reachable under
  a real network partition), that is a *documented, defensible exception* — not
  a silent scope-down (master plan Phase 6).
- Tests are written continuously through Phases 1–5; Phase 6 only turns the
  gate on and closes gaps.
