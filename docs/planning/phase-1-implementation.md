# Phase 1 Implementation Plan — Walking Skeleton, Local

**Status:** LOCKED 2026-08-09. Decisions ratified in a grilling session with
Ahmed (questions Q1–Q7 below). Reviewed 2026-08-09 by three parallel
subagents (technical / logic / impressiveness review) — 36 findings, all
fixed in this revision (fixes marked inline "(review fix)"). Revised again
2026-08-09 per Ahmed: all evidence collection is removed — no `/evidence`
capture, no screenshots, no README work during this phase; verification
numbers are recorded in the implementation log only, and all evidence for the
README is assembled once at the end of the project (Phase 8). This document is
the input for an agentic coding tool — every acceptance criterion is
self-checkable (pass/fail an agent can verify), per master plan §10.

**Position in the hierarchy:** Master Plan §5 Phase 1 → this document. Nothing
here re-decides the ADR or master plan; it makes Phase 1 executable.

---

## 1. Objective

Prove the core premise end-to-end, locally, before any GCP spend: a **real,
continuous connection to the Wikimedia `recentchange` SSE stream** → landing in
**ClickHouse** → visible on a **Grafana panel**, unattended, for a sustained
multi-hour run. The only question this phase answers is "does the core premise
hold" (master plan Phase 1).

## 2. Scope

| In | Deliberately out (later phase) |
| --- | --- |
| Async consumer, httpx2 streaming, hand-rolled SSE parser (ADR-004) | Pydantic validation models (Phase 4) |
| In-memory `Last-Event-ID` resume on reconnect | `batcher.py` client-side batching (Phase 3/4) |
| One raw table `raw_events` (no MVs, no TTL, no partitioning) | Versioned migrations runner (Phase 3A) |
| One Grafana panel reading directly off the raw table | GX suite (Phase 4) |
| `restart: unless-stopped` on all services (one line; healthchecks are Phase 4) | Dead-letter routing (Phase 4) |
| Structured connect/reconnect/processed logging | Alerting, IaC, CI/CD (Phases 2/5) |
| Parser unit tests (business-critical module, ADR-009) | Consumer integration test (live run is the integration test) |

## 3. Locked decisions (from the grilling session)

| # | Decision |
| --- | --- |
| Q1 | Raw table: `(inserted_at DateTime64(3, 'UTC'), event String)` — explicit UTC so the panel's `now()`-based window and time axis are unambiguous regardless of server TZ (review fix). Not a single `String` column (panel needs a clean time axis), not extracted fields (Phase 3A re-creates the table via migrations). |
| Q2 | Insert path: native **`async_insert=1` + `wait_for_async_insert=0`** (server-side batching, client settings only) — no client batcher; consistent with master plan's "no batching sophistication yet" (review fix). `batcher.py` stays a later-phase module; the coverage-boundary doc's predicted path is corrected at wrap-up. |
| Q3 | Reproducibility gate is the honest one: **`docker compose down -v`** (full volume wipe) then `up --build` — the initdb.d SQL must recreate user + table from scratch, unattended. |
| Q4 | No upfront verification script. Verified **on return**: `docker compose ps` uptime vs. wall-clock, `grep -c "connected"` on consumer logs, final `SELECT count()`. Gate must be able to fail (crash-loop detection). No evidence collection — verification numbers go in the implementation log only. |
| Q5 | Parser unit tests now (100% bar per boundary doc). **No** consumer integration test this phase. |
| Q6 | In-memory `Last-Event-ID` resume: **in** (~10 lines; without it a reconnect silently drops events and the "0 dropped" hero metric dies later). Not persisted — disk persistence is Phase 4. |
| Q7 | `docker-compose.yml` at repo root; consumer image parameterized `${CONSUMER_IMAGE:-wikistream-consumer:local}` — the single seam Phase 2 uses to pull from Artifact Registry. Consumer config is env-var-only. |

Plus Phase 0 handoffs that are **constraints, not choices**:

- ClickHouse 26.3's `default` user is localhost-only → the stack must create a
  real user with `CREATE USER ... HOST ANY` + scoped grants (spike-verified
  recipe, implementation-log task 0.4).
- Python pinned to 3.13 for the consumer image (GX caps `<3.14`; keep the image
  aligned so Phase 4 doesn't churn it).
- `consumer/src/sse.py` is business-critical (coverage-boundary doc) → ships
  with tests now.
- Dev credentials are static and local-only, parameterized in compose as
  `${VAR:-default}` so Phase 2's Secret Manager swap is env-only (review fix);
  Secret Manager replaces them in Phase 2.

## 4. Prerequisites

- Phase 0 exit criteria met (implementation-log Phase 0, all DONE).
- Docker + Docker Compose available locally.
- **Build-time re-verification checklist** (Vision §9; verified 2026-08-09
  unless noted):

| Item | Status |
| --- | --- |
| `httpx2` 2.10.0; import name `httpx2`; async streaming (`AsyncClient.stream(...)`, `aiter_bytes()`); `httpx.Timeout(None, connect=10.0)` for infinite streams | ✅ verified on PyPI — re-confirm exact method names against <https://httpx2.pydantic.dev/async/> at build time |
| `clickhouse-connect[async]` async client — `get_async_client()`, settings `{"async_insert": 1, "wait_for_async_insert": 0}`, HTTP-only (port 8123) | ✅ resolved in review (2026-08-09) — re-confirm exact kwarg/settings names on the clickhouse-connect docs at build time |
| `grafana-clickhouse-datasource` 4.18.0 — provisioning schema (`host`/`port`/`protocol`/`username` in jsonData, no `url`/`access`), target field `rawSql` | ✅ resolved in review (2026-08-09) — executor still mirrors the plugin's official example for the wrapper fields |
| Image tags: `clickhouse/clickhouse-server:26.3.17`, `grafana/grafana:13.1.1` | ✅ Phase 0 spike-verified |

Note for the executor: httpx2 2.10.0 ships **native SSE support** (new).
ADR-004's hand-rolled parser is retained — it is a locked decision, the
coverage boundary's 100%-test module, and the CV story. Do not swap to native
SSE in this phase.

**Named Phase 1 risk (master plan §5, carried verbatim):** *"If Wikimedia's
actual event shape, rate, or SSE behavior meaningfully differs from
ADR-003/004's assumptions, this is the cheap place to find out."* Watch for
and RECORD in the log (don't panic, don't "fix"): rate an order of magnitude
off the expected dozens/sec; unexpected `event:` types in the stream;
`retry:`/heartbeat behavior differing from WHATWG assumptions; `data`
payloads that are not JSON objects. Any of these is a *finding*, not a
failure — it changes implementation detail, not architecture, and later
phases consume the recorded observation.

## 5. Target file structure

```
docker-compose.yml                          # the whole local stack; Phase 2 reuses verbatim
consumer/
  Dockerfile                                # python:3.13-slim
  requirements.txt                          # httpx2==2.10.0, clickhouse-connect (pin at build)
  src/
    __init__.py
    sse.py                                  # hand-rolled SSE parser (business-critical)
    consumer.py                             # main loop: stream → parse → insert, resume, logging
docker/
  clickhouse/
    initdb.d/
      001-init.sql                          # user + grants + raw_events table (Phase 1's migrations stand-in)
grafana/
  provisioning/
    datasources/
      clickhouse.yaml
    dashboards/
      dashboards.yaml                       # file provider → /var/lib/grafana/dashboards
  dashboards/
    phase1.json                             # one dashboard, one timeseries panel
tests/
  test_sse.py                               # parser unit tests (100% line coverage on sse.py)
```
(No `evidence/` dir this phase — per revised policy, evidence is collected
once at the end of the project, Phase 8.)

## 6. Tasks

### 1.1 — Consumer package (`consumer/`)

**`consumer/requirements.txt`**

```
httpx2==2.10.0
clickhouse-connect[async]==<latest at build time, ≥1.6.0>   # [async] extra ships the async client (get_async_client); bare install is sync-only
```

**`consumer/Dockerfile`**

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
CMD ["python", "-m", "src.consumer"]
```

**`consumer/src/sse.py` — exact interface (the business-critical module):**

```python
@dataclass
class SSEEvent:
    event: str = "message"     # default per spec when no `event:` field
    data: str = ""
    id: str | None = None      # for Last-Event-ID resume
    retry: int | None = None   # reconnect hint, in ms

class SSEParser:
    def feed(self, chunk: bytes) -> list[SSEEvent]: ...
    def flush(self) -> list[SSEEvent]: ...   # EOF: DISCARDS trailing partial frame (WHATWG: incomplete event not dispatched), resets buffer, returns []
```

Semantics (WHATWG SSE):

- Frames separated by a blank line (`\n\n` or `\r\n\r\n`); line terminators
  are `\n`, `\r\n`, or bare `\r` (strip trailing `\r`).
- Lines `field: value`; after splitting on the FIRST colon, strip exactly ONE
  leading space from the value (`id: 1234` → `1234`) — Wikimedia sends one
  space; without the strip, Last-Event-ID resume breaks (review fix).
- Lines starting `:` are comments/heartbeats → ignored.
- Unknown fields (`foo: bar`) are ignored per spec. `data:` with no value is
  legal (`data:` with empty value → empty string event, still emitted).
  A malformed line (no colon) is skipped. `retry:` with non-digits and `id:`
  containing NUL are ignored (spec).
- Multi-line `data:` fields joined with `\n`.
- Streaming is UTF-8: hold a `codecs.getincrementaldecoder("utf-8")` instance
  across `feed()` calls and decode each chunk before line-parsing — a
  multi-byte char split across chunks must not corrupt (review fix).
- `feed()` returns only **complete** events (holds partial frames in an
  internal buffer across chunks); `flush()` **discards** any trailing partial
  frame (WHATWG: an incomplete event at EOF is not dispatched — emitting it
  would insert truncated JSON garbage on every reconnect) and resets.

**`consumer/src/consumer.py` — behavior spec:**

- Config from env only: `STREAM_URL` (default
  `https://stream.wikimedia.org/v2/stream/recentchange`),
  `CLICKHOUSE_HOST`/`CLICKHOUSE_PORT`/`CLICKHOUSE_USER`/`CLICKHOUSE_PASSWORD`,
  `USER_AGENT` (default `WikiStream/0.1 (personal data-engineering demo;
  https://github.com/AhmedIkram05/WikiStream)`). Wikimedia requires a
  descriptive User-Agent — never send the default httpx2 UA.
  `CLICKHOUSE_PORT` default `8123` (HTTP) — **clickhouse-connect speaks only
  the HTTP interface; port 9000 native is never used by the consumer**
  (review fix).
- Connect: `AsyncClient(timeout=httpx.Timeout(None, connect=10.0))` — NO read
  timeout on an infinite stream (httpx2's default 5s read timeout would trip
  between heartbeats → permanent reconnect storm; review fix — confirm names
  on httpx2 2.10.0 docs). `GET STREAM_URL` with headers `Accept:
  text/event-stream`, `Cache-Control: no-cache`, `User-Agent`, and
  `Last-Event-ID: <last>` when a non-null last event id is held (in-memory
  only).
- Read the streamed response in bytes chunks (`aiter_bytes()` — confirm exact
  method on httpx2 2.10.0 docs); feed every chunk into the `SSEParser`.
- Per complete event with `data:`: insert one row
  `(inserted_at=datetime.now(timezone.utc), event=<raw data JSON as-is>)` into
  `default.raw_events` via the clickhouse-connect async client with
  `settings={"async_insert": 1, "wait_for_async_insert": 0}` (confirm exact
  kwarg/settings name on the clickhouse-connect docs at build time). The
  `wait_for_async_insert=0` is required: left at 1, every insert awaits the
  server-side flush → ~5-20 events/s ceiling vs the stream's 50-200+ events/s
  (review fix). Server-side batching, no client batcher. Non-JSON / garbage
  data is inserted as-is — validation is Phase 4.
  Remember `ev.id` as `last_event_id` when present (Wikimedia sends one per
  event). The SSE `id` is the **resume cursor**; the stable event key for
  later-phase dedup is the `id` field INSIDE the JSON payload (extractable
  from `event` — review fix).
- Insert failure (e.g. ClickHouse temporarily unreachable): log `WARNING
  insert_failed event=<id|None> reason=<...>`, **drop the event, continue** —
  Phase 1 is at-most-once; retry/dead-letter is Phase 4 (review fix).
- On stream end, connection error, or non-200: log `WARNING`, wait, reconnect.
  The loop is the resilience. Do **not** crash on a transient error. Wait
  calc: on non-200 honor the `Retry-After` header when present; cap the wait
  at ~30s — `wait = min(retry_ms or 1s, retry_after or 30s)` (unbounded 1s
  hammering of a 429/503 risks a Wikimedia ban — review fix).
- Graceful shutdown: handle SIGTERM/SIGINT (asyncio task cancellation /
  `try/finally`) → clean exit, no traceback.
- Log lines (exact-ish; these are the verification contract — the gate is checked against them):
  - `INFO  connected url=<STREAM_URL> last_event_id=<id|None>`
  - `WARNING reconnect reason=<...> last_event_id=<id|None>`
  - `WARNING insert_failed event=<id|None> reason=<...>` (see insert-failure
    note above)
  - `INFO  inserted events=<count this batch> total=<cumulative>` — logged
    AFTER the insert call resolves; async_insert rows become visible to
    `count()` ~1-2s later — that lag is expected, not drops (see §9). Emit
    every 100 events or ~60s, whichever comes first.

Note: `consumer/src/models.py`, `batcher.py`, `dead_letter.py` (predicted in
the coverage-boundary doc) are **not** created this phase — Phase 1 corrects
the boundary paths at wrap-up (task 1.9).

### 1.2 — ClickHouse service + initdb.d DDL

**`docker/clickhouse/initdb.d/001-init.sql`**

```sql
-- Phase 1 walking-skeleton bootstrap. Stand-in for versioned migrations (Phase 3A).
-- Spike-verified recipe (implementation-log 0.4): 26.3 `default` user is localhost-only.

CREATE USER IF NOT EXISTS wikistream IDENTIFIED WITH plaintext_password BY 'wikistream_dev_password' HOST ANY;
GRANT SELECT, INSERT, CREATE, ALTER, DROP, TRUNCATE, OPTIMIZE ON default.* TO wikistream;

CREATE TABLE IF NOT EXISTS default.raw_events (
    inserted_at DateTime64(3, 'UTC'),   -- explicit UTC: panel's now()-based window stays unambiguous (review fix)
    event String
) ENGINE = MergeTree
ORDER BY inserted_at;
```

Notes: ClickHouse's entrypoint runs `/docker-entrypoint-initdb.d/*.sql` once,
on an **empty** data volume (first boot) — which is exactly why the Q3 gate is
`down -v`. The file is idempotent (`IF NOT EXISTS`) by design. Comment the
grant list against the spike finding: blanket `GRANT ALL` is denied in 26.3.

### 1.3 — Grafana provisioning + panel

**`grafana/provisioning/datasources/clickhouse.yaml`**

```yaml
apiVersion: 1
datasources:
  - name: ClickHouse
    uid: wikistream-clickhouse
    type: grafana-clickhouse-datasource
    isDefault: true
    # Plugin 4.x schema: connection details live in jsonData (host/port/
    # protocol); url/access are IGNORED by this plugin (review fix).
    jsonData:
      defaultDatabase: default
      host: clickhouse
      port: 8123
      protocol: http
      username: wikistream
    secureJsonData:
      password: ${CLICKHOUSE_PASSWORD:-wikistream_dev_password}
```

**`grafana/provisioning/dashboards/dashboards.yaml`**

```yaml
apiVersion: 1
providers:
  - name: phase1
    orgId: 1
    folder: ""
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    options:
      path: /var/lib/grafana/dashboards
```

**`grafana/dashboards/phase1.json`** — one dashboard ("Phase 1 — Walking
Skeleton"), one timeseries panel "Events per minute (raw_events)":

- datasource: `{ "type": "grafana-clickhouse-datasource", "uid": "wikistream-clickhouse" }`
- panel `description` (self-explaining story — it carries into the
  end-of-project README capture): "Wikimedia recentchange SSE → hand-rolled
  spec-compliant parser (100% tested) → ClickHouse async_insert → this panel".
- the target field is **`rawSql`** (not `sql`) per the plugin's 4.x target
  schema (review fix). Target SQL (reads directly off the raw table — no MV,
  per scope):

```sql
SELECT toStartOfMinute(inserted_at) AS t, count() AS events
FROM default.raw_events
WHERE inserted_at >= now() - INTERVAL 6 HOUR
GROUP BY t ORDER BY t
```

- `format: 0` (numeric TimeSeries enum — the string `"time_series"` form fails
  the plugin backend's JSON unmarshal; verified build-time against
  grafana-clickhouse-datasource `src/types/sql.ts` + sqlds, see 1.9
  deviation), refresh `10s`, time range `now-6h → now` (so the
  sustained-run history is visible on return).
- Executor: confirm the exact target JSON fields (`rawSql`, `format`,
  refId shape) against the grafana-clickhouse-datasource 4.18.0 official
  examples before finalising the JSON — the SQL and uid above are the
  contract; the wrapper fields are a build-time check.

### 1.4 — Root `docker-compose.yml`

```yaml
services:
  clickhouse:
    image: clickhouse/clickhouse-server:26.3.17
    container_name: wikistream-clickhouse
    restart: unless-stopped
    environment:
      CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: "1"   # 26.3 image ships with SQL access-management OFF; without it 001-init.sql's CREATE USER is denied and initdb.d crash-loops (review fix)
    ports:
      - "8123:8123"   # HTTP — Grafana datasource + consumer + ad-hoc verification queries
      - "9000:9000"   # native — clickhouse-client only; the consumer never uses it
    volumes:
      - ./docker/clickhouse/initdb.d:/docker-entrypoint-initdb.d:ro
      - ch-data:/var/lib/clickhouse
    ulimits:
      nofile: { soft: 262144, hard: 262144 }   # required by the CH image

  consumer:
    build: ./consumer                                    # local-only build path; Phase 2 pulls instead (see §10)
    image: ${CONSUMER_IMAGE:-wikistream-consumer:local}  # Phase 2 seam: Artifact Registry tag
    container_name: wikistream-consumer
    restart: unless-stopped
    depends_on: [clickhouse]
    environment:
      CLICKHOUSE_HOST: clickhouse
      CLICKHOUSE_PORT: "8123"                            # HTTP — clickhouse-connect is HTTP-only (review fix)
      CLICKHOUSE_USER: wikistream
      CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD:-wikistream_dev_password}   # local default only; Secret Manager in Phase 2
      STREAM_URL: https://stream.wikimedia.org/v2/stream/recentchange

  grafana:
    image: grafana/grafana:13.1.1
    container_name: wikistream-grafana
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      GF_INSTALL_PLUGINS: grafana-clickhouse-datasource:4.18.0
      GF_SECURITY_ADMIN_PASSWORD: ${GF_SECURITY_ADMIN_PASSWORD:-admin}   # local default only
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
      - ./grafana/dashboards:/var/lib/grafana/dashboards:ro

volumes:
  ch-data:
```

No healthchecks (Phase 4). The consumer's reconnect loop absorbs ClickHouse's
cold-start delay (~20–30s) — early `reconnect` log lines on first boot are
expected, not a failure.

### 1.5 — Parser unit tests (`tests/test_sse.py`)

Plain pytest, no fixtures framework beyond pytest itself. Run from repo root
with a Python 3.13 venv (`pip install -r consumer/requirements.txt pytest
pytest-cov`). Test cases:

1. single complete frame → one `SSEEvent` with event/data/id
2. one frame split across two `feed()` chunks → exactly one event
3. three frames in one chunk → three events, in order
4. CRLF (`\r\n`) line endings → parsed identically
5. bare `\r` as line terminator → parsed identically (spec; review fix)
6. comment/heartbeat lines (`: health check`) → ignored, no events
7. multi-line `data:` → joined with `\n`
8. `id:` captured (the resume contract)
9. `id: 1234` with leading space → value stripped to `1234` — exactly ONE
   space, not all (review fix; this is the Last-Event-ID resume contract)
10. data-only frame → `event` defaults to `"message"`
11. `retry:` captured as int ms
12. `retry:` with non-digits → ignored (spec; review fix)
13. `id:` containing NUL → ignored (spec; review fix)
14. unknown field (`foo: bar`) → ignored, rest of frame intact (spec)
15. malformed line (no colon) → skipped, rest of frame intact
16. empty `data:` value → still emitted as an event (spec behavior)
17. trailing partial frame (no blank line) → `flush()` DISCARDS it and returns
    `[]` (WHATWG: incomplete event not dispatched; review fix)
18. `flush()` after a clean frame → returns `[]` and resets (buffer hygiene)
19. multi-byte UTF-8 char split across two `feed()` chunks → decoded cleanly,
    one event (incremental-decoder contract; review fix)

Coverage check (boundary doc bar, enforced for real in Phase 6):

```
PYTHONPATH=consumer pytest tests/ -v                                   # PYTHONPATH so consumer/src is importable from repo root (review fix)
PYTHONPATH=consumer pytest --cov=src.sse --cov-report=term-missing     # must show 100% on consumer/src/sse.py
```

### 1.6 — Bring-up + smoke verification

```bash
docker compose up -d --build
docker compose ps                                   # 3 services running
docker compose logs consumer --tail 50              # INFO connected ... ; no Traceback
docker compose exec clickhouse clickhouse-client --user wikistream --password wikistream_dev_password \
  --query "SELECT count() FROM default.raw_events"  # ≥1 after ~60s
```

### 1.7 — Sustained run

1. Start the stack (task 1.6), confirm the panel renders non-null values at
   <http://localhost:3000> (dashboard "Phase 1 — Walking Skeleton").
2. Record `start_time` (wall clock), the consumer's `connected` log line, AND
   an opening `SELECT count() FROM default.raw_events` — the opening count
   brackets the run so the final count proves flow across it, not just at
   capture time (review fix).
3. Leave running in the background for **≥ 2 hours** (overnight is a bonus —
   more data for Phase 3/7).
4. On return, verify (the gate must be able to fail):
   - `docker compose ps` — consumer uptime spans the run; if it doesn't,
     reconcile with reconnect lines before claiming the gate.
   - `docker inspect -f '{{.State.StartedAt}}' wikistream-consumer` — exact
     consumer start time to compare against wall-clock (`ps` durations are too
     coarse; review fix).
   - `docker compose logs --timestamps consumer | grep -c "connected url="` —
     `1` = continuous; `>1` = reconnects. Reconnects BEFORE the first
     `connected url=` line are the expected ClickHouse cold-start — exclude
     them; map any later ones to real-world blips via their timestamps. A
     reconnect storm after first connect = crash-loop = gate FAILS.
   - `docker compose logs --timestamps consumer | grep -c Traceback` — must be
     `0`.
   - Final `SELECT count() FROM default.raw_events` — record the number.
5. Derive the numbers this run uniquely enables — BEFORE task 1.8 wipes the
   volume (review fix):
   - **Sustained events/sec** = (final count − opening count) / elapsed
     seconds (hero metric #1; cross-check against the log's cumulative
     `total=`).
   - **Peak events/min** = `SELECT count() AS c FROM default.raw_events GROUP
     BY toStartOfMinute(inserted_at) ORDER BY c DESC LIMIT 1`.
   Write both, plus opening/final counts and elapsed time, into
   `docs/implementation-log.md` (task 1.7 entry) — the log is the only record;
   no evidence files are created this phase.
   In the log entry, also record: the raw stream includes Wikidata/Commons bot
   traffic (Wikipedia-only filtering is Phase 3B; bot/human ratio is a Phase 3
   KPI), and what this run does NOT prove — no restart-resume (in-memory
   only), no dedup proof, no validation (all Phase 4).

### 1.8 — Reproducibility gate (Q3: the honest one)

```bash
docker compose down -v            # wipes ch-data volume → initdb.d must re-run
docker compose up -d --build
# after ~3 min: TWO count() samples ~1 min apart — both ≥1 and the second
# strictly greater (samples must bracket a live window, not be one-offs;
# review fix):
docker compose exec clickhouse clickhouse-client --user wikistream --password wikistream_dev_password \
  --query "SELECT count() FROM default.raw_events"
sleep 60
docker compose exec clickhouse clickhouse-client --user wikistream --password wikistream_dev_password \
  --query "SELECT count() FROM default.raw_events"
docker compose logs consumer --tail 20               # fresh "connected" line
```

Gate passes only with **zero manual steps** between `down -v` and a flowing
pipeline. `docker compose down` (no `-v`) is not an acceptable substitute —
it keeps the volume and proves nothing.

### 1.9 — Wrap-up

1. **Correct the coverage-boundary doc**: `consumer/src/sse.py` path confirmed
   as landed; note that `models.py`, `batcher.py`, `dead_letter.py` remain
   predicted (not landed in Phase 1) — the *set* of modules is unchanged.
2. Populate `docs/implementation-log.md` Phase 1 task entries (format per the
   logging rules; statuses updated as each task completes; verification numbers
   recorded inline — no `/evidence` files this phase).
3. README stays a stub — the README and all evidence for it are assembled once
   at the end of the project (Phase 8), per Ahmed.
4. Commit: one PR per repo convention (`feat/phase-1-walking-skeleton`), commit
   messages matching existing style. No CI yet — that is Phase 2.

## 7. Acceptance criteria (self-checkable)

| # | Criterion | How an agent verifies it |
| --- | --- | --- |
| AC1 | Stack comes up clean | `docker compose up -d --build` exit 0; `docker compose ps` → 3 running |
| AC2 | Consumer connected, no crash | logs contain ≥1 `connected` line, 0 `Traceback` |
| AC3 | Data landing | `SELECT count()` ≥ 1 within 60s of ClickHouse being reachable |
| AC4 | Data still flowing | `count()` strictly increases between two samples ≥ 5 min apart |
| AC5 | Grafana up + dashboard provisioned | `curl -s -o /dev/null -w "%{http_code}" http://localhost:3000` → 200; `GET /api/search` (admin:admin) returns the dashboard |
| AC6 | Panel query returns rows **through Grafana** | `GET /api/ds/query` against Grafana (admin:admin) with `{"queries":[{"datasource":{"type":"grafana-clickhouse-datasource","uid":"wikistream-clickhouse"},"rawSql":"SELECT toStartOfMinute(inserted_at) AS t, count() AS events FROM default.raw_events WHERE inserted_at >= now() - INTERVAL 6 HOUR GROUP BY t ORDER BY t","format":0}]}` → 200 and ≥ 1 row in `response.results` — verifies the datasource + plugin path, not just raw 8123 (review fix; `"format":0` numeric per plugin schema — string form rejected by grafana-plugin-sdk-go FormatQueryOption) |
| AC7 | Sustained run holds (≥ 2h) | Task 1.7 passes: exact consumer start spans the run (or reconnects mapped to blips), 0 Tracebacks, opening+final count() and derived events/sec recorded in the implementation log |
| AC8 | Reproducibility | Task 1.8 passes: `down -v` → `up --build` → count() ≥ 1 and increasing, fresh `connected` line, zero manual steps |
| AC9 | Parser tested to the bar | `PYTHONPATH=consumer pytest tests/ -v` green; `PYTHONPATH=consumer pytest --cov=src.sse --cov-report=term-missing` shows 100% line coverage on consumer/src/sse.py |
| AC10 | Docs consistent | coverage-boundary corrected; log populated with verification numbers |

## 8. Verification gate (master plan wording)

> Live data flows from Wikimedia to a visible panel, unattended, in a sustained
> local run. The walking skeleton can be destroyed and recreated locally
> (`docker compose down && up`) without manual intervention.

Phase exit criteria = AC1–AC10. There is no Go/No-Go gate after Phase 1
(master plan §4 — gates sit after Phases 2 and 4).

## 9. Troubleshooting notes

- **ClickHouse auth failures** — you're probably using `default` (localhost-only
  in 26.3, spike finding). Use `wikistream`/`wikistream_dev_password`. If the
  user doesn't exist, the volume wasn't empty on first boot — run `down -v`.
- **Consumer reconnects for the first ~30s** — expected: ClickHouse is still
  booting; the reconnect loop is the design. Only a reconnect *storm with
  tracebacks* is a bug.
- **Grafana shows "plugin not found"** — `GF_INSTALL_PLUGINS` env is the
  install path; restart grafana and check its logs for the plugin download.
- **Panel empty / datasource error** — check `secureJsonData` password matches
  the SQL user; check provisioning paths in `dashboards.yaml` against the
  mounted volume; grafana logs show provisioning lines on boot.
- **Port confusion** — 8123 = HTTP: Grafana datasource AND the consumer
  (clickhouse-connect is HTTP-only). 9000 = native protocol: only for ad-hoc
  `clickhouse-client` access. The consumer must use 8123 (review fix).
- **`count()` lags the stream by ~1-2s** — expected under `async_insert`
  (server-side buffer flushes async); it is not a stall. Do not shorten the
  AC4 sampling window below ~1 minute.
- **ClickHouse won't start** — the image requires `ulimits.nofile: 262144`;
  check Docker Desktop memory settings if it dies on a Mac.
- **`down` vs `down -v`** — plain `down` keeps the volume; a "recreate" that
  didn't wipe is not the Q3 gate.

## 10. Handoff to Phase 2 (what Phase 2 inherits)

- **The same compose file, verbatim**, on the GCP VM — Phase 2 changes
  `CONSUMER_IMAGE` to the Artifact Registry tag AND runs
  `docker compose pull && docker compose up -d --no-build` — the `build:` key
  on the consumer service is a local-only path; with it present, `up --build`
  would rebuild from the repo instead of pulling the registry image (review
  fix). Everything service-internal (`clickhouse` hostname, ports) already
  works unchanged on the VM.
- Consumer is env-var-only and every credential in the stack is already
  `${VAR:-default}` parameterized — Phase 2's Secret Manager swap is a pure
  environment change, no compose edits (review fix).
- No evidence is collected in Phase 1 (per Ahmed). Master plan §8: Phase 1's
  local proof does not survive teardown; if the README needs it at the end of
  the project, Phase 8 re-runs the local stack or drops it.
- `raw_events` schema is the Phase 1 skeleton shape; Phase 3A replaces it with
  the full versioned schema via migrations (initdb.d file is retired then).
- The parser tests, logging contract, and verification protocol carry forward
  unchanged — Phase 2's verification gate reuses AC2/AC3/AC4 shape.
