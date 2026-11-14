# ADR 0015: SQL Data-Quality Gate Replaces Great Expectations

**Status:** Accepted
**Date:** 2026-11-14
**Supersedes:** ADR-005 (Pydantic inline + GX batch split)

## Context

The hourly quality gate ran Great Expectations `0.18.22` against a 5% sample of the last completed UTC hour. Its 11 expectations were plain aggregates, but the path materialized ~60k rows through SQLAlchemy and pandas and took ~17 minutes on the same 2-vCPU VM as ClickHouse. GX also forced a Python 3.12 pin, a separate image, a second `uv.lock`, a separate CI sync, and a `platform: linux/amd64` compose pin.

The checks are: six non-null guards, the `event_type` domain, median lag, max skew, wiki cardinality, and bot ratio. The heavy dependencies were the only reason the gate needed its own image. The consumer image already carries `clickhouse-connect`, the only runtime dependency the SQL version needs.

## Decision

- Replace `gx/suite.py` with `consumer/src/data_quality.py`: one ClickHouse aggregate over the **full** completed window, no sampling, no pandas/GX.
- Preserve all 11 checks, the one-JSON-line stdout contract, the exit-code contract, the `atexit` verdict write, and the `pipeline_health` detail keys.
- Keep the six null checks as schema-drift guards even though the current `MATERIALIZED` columns are non-nullable.
- Write the verdict as `source='data_quality'`, `metric='result'`; the consumer healthcheck and the Grafana alert read that row.
- Run it as a one-shot compose service `data_quality` using `${CONSUMER_IMAGE}` and `python -m src.data_quality`; drive it hourly at `:30` with `wikistream-data-quality.timer`.
- Delete `gx/`, its Dockerfile, `uv.lock`, image build, project sync, test suite, and `GX_*` names. Fold the tests into the consumer project and add `src.data_quality` to the 100% business-critical coverage list.
- Rename the alert to `data-quality-fail`; the cutover pre-seeds a real 1.0 row before the VM rebuild, so the immediate rename has no no-data gap.

## Alternatives

- **Keep GX weekly as a second opinion.** Rejected: keeps pandas/numpy/SQLAlchemy, the 3.12 pin, the separate image, and the 505-line test suite; only saves the hourly runtime.
- **Keep a separate `quality/` image.** Rejected: the heavy dependencies are gone; a second image/lock/CI path is now ceremony.
- **Keep 5% sampling.** Rejected: the full-window aggregate is cheap at ~1.2M rows/hour and catches outliers the sample could miss.
- **Rust/PyO3 gate.** Rejected: no measured bottleneck once the query is server-side; adds a build and FFI surface for no pipeline gain.

## Consequences

- The gate scans 100% of the window and is expected to run in well under a second on the VM; the real number is recorded during cutover.
- Max skew is stricter than the sampled version: one future timestamp the sample missed can fail the first real run. That is a finding, not a regression.
- The consumer image now ships the gate; if that image is broken, the gate cannot run. The image is already the pipeline, so the coupling is acceptable.
- Removing `GX_IMAGE` edits the frozen `startup.sh` (`metadata_startup_script` is ForceNew), so the deploy includes a VM rebuild and a pre-rebuild backup. The GCP estate is currently torn down; the next deployment is a fresh rebuild, not a live cutover, so the old-stack pre-seed bridge is not needed. `boot.sh` seeds one `data_quality` verdict after migrations; prove the forced failure before the first `:30` run.
- Historical planning docs and ADR-005 stay as written; this ADR records the supersession.

## References

- `consumer/src/data_quality.py`
- `consumer/wikistream-data-quality.service`, `consumer/wikistream-data-quality.timer`
- `consumer/src/healthcheck.py`
- `grafana/provisioning/alerting/alerts.yml`
- `scripts/boot.sh`, `infra/main/templates/startup.sh`
- `tests/src/data_quality/`
- `gx/suite.py` (deleted)
