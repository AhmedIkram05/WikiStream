"""consumer.py loop unit tests — hermetic, no ClickHouse, no network (plan 6.1).

The real ch integration suites (test_malformed_to_dead_letter, kill/resume)
already exercise consume_forever against live ClickHouse; these tests drive
the same code paths with fakes so the ~90% overall gate is measurable in the
hermetic unit job. httpx2.AsyncClient, get_async_client and heartbeat_loop
are all monkeypatched; the EventBatcher and DeadLetterBatcher are real (both
have their own 100%-covered suites) and land rows through the FakeCh client.
"""

import asyncio
import json
import logging
import runpy
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import consumer
from src import cursors

VALID = {
    "id": "1",
    "type": "edit",
    "title": "Test page",
    "user": "tester",
    "bot": False,
    "wiki": "enwiki",
    "timestamp": 1786626373,
}


def frame(eid: str, data: str) -> bytes:
    return f"id: {eid}\ndata: {data}\n\n".encode()


def payload(**overrides) -> str:
    p = dict(VALID)
    p.update(overrides)
    return json.dumps(p)


class FakeCh:
    """Duck-typed clickhouse async client used by EventBatcher/DeadLetterBatcher."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list = []
        self.rows = 0
        self.dl_rows: list = []

    async def insert(self, table, data, column_names=None, settings=None):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((table, len(data)))
        self.rows += len(data)
        if table == "default.dead_letter":
            self.dl_rows.extend(data)


class DLFailingCh(FakeCh):
    """CH up, but the dead_letter table rejects inserts (batch dropped)."""

    async def insert(self, table, data, column_names=None, settings=None):
        if table == "default.dead_letter":
            raise RuntimeError("dl down")
        await super().insert(table, data, column_names=column_names, settings=settings)


class FakeResponse:
    def __init__(self, status_code=200, headers=None, chunks=None, exc_after_n=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.chunks = chunks or []
        self.exc_after_n = exc_after_n

    async def aiter_bytes(self):
        for n, chunk in enumerate(self.chunks, start=1):
            yield chunk
            if self.exc_after_n == n:
                raise ConnectionError("boom")


class FakeHTTP:
    """Replaces httpx2.AsyncClient; stream() serves from a queue (last one reused)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.headers_seen: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def stream(self, method, url, headers=None):
        # SYNC: the consumer does `async with http.stream(...)` (no await on
        # the call itself), matching httpx2's API. An async def here returns
        # a coroutine, which would TypeError inside async-with — swallowed by
        # consume_forever's except and silently zeroing every assertion.
        self.headers_seen.append(headers or {})
        if len(self.responses) > 1:
            return _StreamCM(self.responses.pop(0))
        return _StreamCM(self.responses[0])


class _StreamCM:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *exc):
        return None


@pytest.fixture
def counters():
    return {"total": 0, "dead_lettered": 0, "insert_failed": 0, "duplicates_skipped": 0}


@pytest.fixture
def stop():
    return asyncio.Event()


def patch_sleep(monkeypatch):
    """Break the reconnect wait: loop exits after the first reconnect."""

    async def no_wait(stop_ev, seconds):
        return True

    monkeypatch.setattr(consumer, "_sleep_or_stop", no_wait)


def patch_http(monkeypatch, responses, client_kwargs=None, instances=None):
    def factory(**kw):
        if client_kwargs is not None:
            client_kwargs.append(kw)
        http = FakeHTTP(responses)
        if instances is not None:
            instances.append(http)
        return http

    monkeypatch.setattr(consumer.httpx2, "AsyncClient", factory)


def patch_state(monkeypatch, tmp_path):
    monkeypatch.setattr(consumer, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(consumer, "STATE_FILE", str(tmp_path / "consumer_state.json"))


def run(coro):
    return asyncio.run(coro)


# ---- pure helpers ----


def test_wait_seconds():
    assert consumer._wait_seconds(None, None) == 1.0
    assert consumer._wait_seconds(5000, None) == 5.0
    assert consumer._wait_seconds(None, 20) == 20.0
    assert consumer._wait_seconds(2000, 20) == 20.0
    assert consumer._wait_seconds(60000, None) == 30.0
    assert consumer._wait_seconds(None, 60) == 30.0


def test_parse_retry_after():
    assert (
        consumer._parse_retry_after(SimpleNamespace(headers={"Retry-After": "5"})) == 5
    )
    assert consumer._parse_retry_after(SimpleNamespace(headers={})) is None
    assert (
        consumer._parse_retry_after(SimpleNamespace(headers={"Retry-After": "abc"}))
        is None
    )
    assert (
        consumer._parse_retry_after(SimpleNamespace(headers={"Retry-After": "-3"})) == 0
    )


def test_sleep_or_stop_real():
    async def check():
        stop = asyncio.Event()
        assert await consumer._sleep_or_stop(stop, 0.01) is False  # timeout, stop unset
        stop.set()
        assert await consumer._sleep_or_stop(stop, 0.01) is True  # stop during wait

    run(check())


def test_cursor_ts_edges():
    assert cursors._cursor_ts("[not json") == 0
    assert cursors._cursor_ts(123) == 0
    assert cursors._cursor_ts('{"topic": "eqiad"}') == 0
    assert cursors._cursor_ts('[1, 2, "x"]') == 0
    assert cursors._cursor_ts('[{"offset": -1}, {"timestamp": 5}]') == 5
    assert cursors._cursor_ts('[{"timestamp": 0}, {"offset": 1}]') == 1


def test_max_id_edges():
    assert cursors._max_id(None, "b") == "b"
    assert cursors._max_id("a", None) == "a"
    assert cursors._max_id("5", "10") == "10"
    assert cursors._max_id("10", "10") == "10"
    assert cursors._max_id("[", "[x") == "["
    assert cursors._max_id("b", "a") == "b"  # plain-string fallback
    assert cursors._max_id("a", "b") == "b"
    assert (
        cursors._max_id('[{"offset": -1}]', '[{"timestamp": 7}]')
        == '[{"timestamp": 7}]'
    )


def test_dl_fields():
    assert consumer._dl_fields({"wiki": "enwiki", "title": "T"}) == ("enwiki", "T")
    assert consumer._dl_fields({"wiki": None, "title": "T"}) == ("", "T")
    assert consumer._dl_fields(["not", "a", "dict"]) == ("", "")


# ---- load_state / save_state ----


def test_load_state_missing_file(monkeypatch, tmp_path):
    patch_state(monkeypatch, tmp_path)
    assert consumer.load_state() is None


def test_load_state_bad_json_and_non_dict(monkeypatch, tmp_path, caplog):
    patch_state(monkeypatch, tmp_path)
    path = Path(consumer.STATE_FILE)
    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        path.write_text("not json")
        assert consumer.load_state() is None
        assert "state_load_failed" in caplog.text
        path.write_text(json.dumps([1, 2, 3]))
        assert consumer.load_state() is None
        path.write_text(json.dumps("just a string"))
        assert consumer.load_state() is None


def test_load_state_type_coercion(monkeypatch, tmp_path):
    patch_state(monkeypatch, tmp_path)
    path = Path(consumer.STATE_FILE)
    path.write_text(json.dumps({"last_event_id": 42, "total": "many"}))
    state = consumer.load_state()
    assert state["last_event_id"] is None
    assert state["total"] == 0


def test_load_state_roundtrip(monkeypatch, tmp_path):
    patch_state(monkeypatch, tmp_path)
    consumer.save_state("7", 3)
    assert consumer.load_state() == {
        "last_event_id": "7",
        "total": 3,
        "updated_at": consumer.load_state()["updated_at"],
    }
    # updated_at round-trip separately: save then load preserves it
    state = consumer.load_state()
    assert state["last_event_id"] == "7" and state["total"] == 3
    assert "updated_at" in state


def test_save_state_failure_logs(monkeypatch, tmp_path, caplog):
    # STATE_DIR is an existing FILE → makedirs raises FileExistsError
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setattr(consumer, "STATE_DIR", str(blocker))
    monkeypatch.setattr(consumer, "STATE_FILE", str(blocker / "state.json"))
    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        consumer.save_state("1", 2)  # must not raise
    assert "state_save_failed" in caplog.text


# ---- consume_forever loop ----


def test_consume_happy_path_flush_and_state(
    monkeypatch, tmp_path, counters, stop, caplog
):
    patch_sleep(monkeypatch)
    patch_http(
        monkeypatch,
        [
            FakeResponse(
                chunks=[b"".join(frame(str(i), payload()) for i in range(1, 1002))]
            )
        ],
    )
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    with caplog.at_level(logging.INFO, logger="wikistream.consumer"):
        run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["total"] == 1001
    assert counters["dead_lettered"] == 0
    assert counters["insert_failed"] == 0
    assert ch.calls == [("default.raw_events", 1000), ("default.raw_events", 1)]
    state = consumer.load_state()
    assert state["last_event_id"] == "1001"
    assert state["total"] == 1001
    assert "inserted events=1000 total=1000" in caplog.text
    assert ch.dl_rows == []


def test_consume_bad_json_goes_dead_letter(monkeypatch, tmp_path, counters, stop):
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[frame("7", "{not json")])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["dead_lettered"] == 1
    assert counters["total"] == 0
    assert ch.dl_rows[0][1:] == ["validation:invalid_json", "", "", "{not json"]
    assert consumer.load_state()["last_event_id"] == "7"


def test_consume_bad_ts_and_non_dict_go_dead_letter(
    monkeypatch, tmp_path, counters, stop
):
    patch_sleep(monkeypatch)
    patch_http(
        monkeypatch,
        [
            FakeResponse(
                chunks=[
                    frame("8", payload(timestamp=None)),
                    # valid JSON but an array: ts lookup falls back to None
                    frame("9", '["no", "dict", "here"]'),
                ]
            )
        ],
    )
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["dead_lettered"] == 2
    assert ch.dl_rows[0][1:] == [
        "timestamp_missing",
        "enwiki",
        "Test page",
        json.dumps(dict(VALID, timestamp=None)),
    ]
    assert ch.dl_rows[1][2:4] == ["", ""]  # _dl_fields non-dict branch (wiki, title)


def test_consume_validation_error_goes_dead_letter(
    monkeypatch, tmp_path, counters, stop
):
    patch_sleep(monkeypatch)
    # title ABSENT (not None): pydantic reports "missing" only for a
    # genuinely absent required key — None yields "string_type".
    bad = dict(VALID)
    bad.pop("title")
    patch_http(monkeypatch, [FakeResponse(chunks=[frame("10", json.dumps(bad))])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["dead_lettered"] == 1
    assert ch.dl_rows[0][1] == "validation:missing"
    assert ch.dl_rows[0][2] == "enwiki"


def test_consume_dl_flush_failure_keeps_cursor(monkeypatch, tmp_path, counters, stop):
    """DL flush dropped (at-most-once): no counter, no cursor advance — the
    server replay re-runs the events (regenerates the DL rows)."""
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[frame("7", "{not json")])])
    patch_state(monkeypatch, tmp_path)
    ch = DLFailingCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["dead_lettered"] == 0
    assert ch.dl_rows == []
    assert consumer.load_state()["last_event_id"] is None  # cursor did NOT advance


def test_consume_dl_row_without_id_advances_nothing(
    monkeypatch, tmp_path, counters, stop
):
    """SSE frame without an id: DL row counted, but there is no cursor to
    advance (covers the event_id=None branch + max_eid-None flush return)."""
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[b"data: {bad\n\n"])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["dead_lettered"] == 1
    assert consumer.load_state()["last_event_id"] is None


def test_consume_dl_burst_flushes_in_batches(monkeypatch, tmp_path, counters, stop):
    """M1: a schema-drift burst must not await one insert per event — rows
    buffer and flush max_rows at a time, fire-and-forget (250 rows → two
    100-row task flushes + the 50-row reconnect-boundary drain)."""
    patch_sleep(monkeypatch)
    patch_http(
        monkeypatch,
        [FakeResponse(chunks=[b"".join(frame(str(i), "{bad") for i in range(1, 251))])],
    )
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["dead_lettered"] == 250
    dl_sizes = sorted(n for table, n in ch.calls if table == "default.dead_letter")
    assert dl_sizes == [50, 100, 100]
    assert len(ch.dl_rows) == 250
    assert consumer.load_state()["last_event_id"] == "250"


def test_consume_dl_spawn_fires_for_ts_and_schema_failures(
    monkeypatch, tmp_path, counters, stop
):
    """Steps 2-3 spawn DL flushes too: 100 timestamp-failures fill the buffer
    on the step-2 path, then 100 schema-failures on the step-3 path."""
    patch_sleep(monkeypatch)
    bad_ts = b"".join(frame(str(i), payload(timestamp=None)) for i in range(1, 101))
    bad = dict(VALID)
    bad.pop("title")
    bad_schema = b"".join(frame(str(i), json.dumps(bad)) for i in range(101, 201))
    patch_http(monkeypatch, [FakeResponse(chunks=[bad_ts + bad_schema])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["dead_lettered"] == 200
    dl_sizes = [n for table, n in ch.calls if table == "default.dead_letter"]
    assert dl_sizes == [100, 100]
    reasons = [r[1] for r in ch.dl_rows]
    assert reasons[:100] == ["timestamp_missing"] * 100
    assert reasons[100:] == ["validation:missing"] * 100


def test_consume_dl_timer_flushes_while_stream_holds_open(
    monkeypatch, tmp_path, counters, stop
):
    """M1: DL rows must land even when no further event arrives (quiet stream
    tail) — the timer flushes the buffer within DL_FLUSH_INTERVAL. Also pins
    M3: the DL cursor advance saves state IMMEDIATELY, before any shutdown."""
    monkeypatch.setattr(consumer, "DL_FLUSH_INTERVAL", 0.01)
    patch_state(monkeypatch, tmp_path)

    class HoldOpen(FakeResponse):
        async def aiter_bytes(self):
            yield frame("7", "{not json")
            await stop.wait()  # stream stays open, silent: no chunk boundaries

    patch_http(monkeypatch, [HoldOpen(chunks=[])])
    ch = FakeCh()

    async def scenario():
        task = asyncio.create_task(consumer.consume_forever(ch, stop, None, counters))
        try:
            deadline = time.monotonic() + 5.0
            while counters["dead_lettered"] == 0:
                assert time.monotonic() < deadline, "timer never flushed the DL row"
                await asyncio.sleep(0.01)
            # M3: state persisted by the flush itself — no shutdown needed.
            assert consumer.load_state()["last_event_id"] == "7"
        finally:
            stop.set()
            await asyncio.wait_for(task, 5.0)

    asyncio.run(scenario())


def test_consume_dl_flush_job_exception_logs(
    monkeypatch, tmp_path, counters, stop, caplog
):
    # _do_dl_flush's except: dl_batcher.flush() itself raising (never crash
    # the consumer on a DL plumbing bug).
    async def boom(self, client):
        raise RuntimeError("dl exploded")

    monkeypatch.setattr(consumer.DeadLetterBatcher, "flush", boom)
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[frame("7", "{not json")])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        run(consumer.consume_forever(ch, stop, None, counters))

    assert "dl_flush_failed reason=dl exploded" in caplog.text


def test_consume_dl_advance_reaches_next_reconnect_header(
    monkeypatch, tmp_path, counters, stop
):
    """M1+M3: a DL flush that lands rows advances the cursor BEFORE the
    reconnect, so the next connect's Last-Event-ID covers the DL'd event and
    the server never replays it back into the dead-letter table."""
    waits = {"n": 0}

    async def reconnect_then_stop(stop_ev, seconds):
        waits["n"] += 1
        return waits["n"] > 1  # 1st reconnect opens pass 2; 2nd exits

    monkeypatch.setattr(consumer, "_sleep_or_stop", reconnect_then_stop)
    instances = []
    patch_http(
        monkeypatch,
        [FakeResponse(chunks=[frame("7", "{bad")]), FakeResponse(chunks=[])],
        instances=instances,
    )
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    first, second = instances[0].headers_seen
    assert "Last-Event-ID" not in first
    assert second["Last-Event-ID"] == "7"
    assert counters["dead_lettered"] == 1  # landed once, not replayed


def test_consume_pre_epoch_timestamp_dead_lettered(
    monkeypatch, tmp_path, counters, stop
):
    """M5: a negative epoch parses in Python but CH's
    parseDateTime64BestEffort cannot parse it — one such event used to error
    the MATERIALIZED column and void the whole 1000-row insert block. The
    floor rejects it at the validation gate."""
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[frame("7", payload(timestamp=-5))])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["dead_lettered"] == 1
    assert counters["total"] == 0
    assert [c for c in ch.calls if c[0] == "default.raw_events"] == []
    assert ch.dl_rows[0][1] == "timestamp_too_old"
    assert consumer.load_state()["last_event_id"] == "7"


def test_consume_caps_concurrent_flush_tasks_at_two(
    monkeypatch, tmp_path, counters, stop
):
    """M4: asyncio.Semaphore around flush task creation — a CH that is slow
    (not dead) must not pile up unbounded flush tasks (3 flush-due batches →
    at most 2 inserts in flight; the loop backpressures at acquire)."""
    patch_sleep(monkeypatch)
    patch_http(
        monkeypatch,
        [
            FakeResponse(
                chunks=[b"".join(frame(str(i), payload()) for i in range(1, 3001))]
            )
        ],
    )
    patch_state(monkeypatch, tmp_path)

    class SlowCh(FakeCh):
        def __init__(self):
            super().__init__()
            self.in_flight = 0
            self.max_in_flight = 0

        async def insert(self, table, data, column_names=None, settings=None):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.05)
            self.in_flight -= 1
            self.calls.append((table, len(data)))
            self.rows += len(data)

    ch = SlowCh()
    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["total"] == 3000
    assert ch.max_in_flight == 2


def test_stream_client_has_read_timeout(monkeypatch, tmp_path, counters, stop):
    """M4: explicit read timeout — a silent upstream stall unblocks into a
    reconnect instead of wedging the read (and shutdown) forever."""
    client_kwargs = []
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[])], client_kwargs=client_kwargs)
    patch_state(monkeypatch, tmp_path)

    run(consumer.consume_forever(FakeCh(), stop, None, counters))

    timeout = client_kwargs[0]["timeout"]
    assert timeout.connect == 10.0
    assert timeout.read == 120.0
    assert timeout.write is None  # unchanged: only connect/read are bounded
    assert timeout.pool is None


def test_consume_duplicate_ids_skipped(monkeypatch, tmp_path, counters, stop):
    patch_sleep(monkeypatch)
    patch_http(
        monkeypatch,
        [
            FakeResponse(
                chunks=[
                    frame("5", payload()),
                    frame("5", payload()),
                    frame("6", payload()),
                ]
            )
        ],
    )
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["duplicates_skipped"] == 1
    assert counters["total"] == 2  # final flush on exit
    assert ch.rows == 2


def test_consume_flush_failure_counts_insert_failed(
    monkeypatch, tmp_path, counters, stop, caplog
):
    patch_sleep(monkeypatch)
    # No in-place retry: a permanent outage abandons on the first failure,
    # exercising the insert_failed + reconnect-replay fallback.
    monkeypatch.setattr(consumer, "FLUSH_RETRY_SCHEDULE", ())
    patch_http(
        monkeypatch,
        [
            FakeResponse(
                chunks=[b"".join(frame(str(i), payload()) for i in range(1, 1001))]
            )
        ],
    )
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh(fail=True)

    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["insert_failed"] == 1000
    assert counters["total"] == 0
    assert "insert_failed events=1000 reason=batch dropped" in caplog.text
    assert consumer.load_state()["last_event_id"] is None


def test_consume_replays_dropped_batch_after_flush_failure(
    monkeypatch, tmp_path, counters, stop
):
    """H1 regression: after a failed flush the dropped ids are unmarked, so a
    server replay (same Last-Event-ID window, since the cursor never advanced)
    re-inserts them instead of being skipped as duplicates for the process
    lifetime. CH blips must not cost ~23s of stream permanently."""
    waits = {"n": 0}

    async def reconnect_then_stop(stop_ev, seconds):
        waits["n"] += 1
        return waits["n"] > 1  # 1st reconnect opens pass 2; 2nd exits

    monkeypatch.setattr(consumer, "_sleep_or_stop", reconnect_then_stop)
    # No in-place retry: the first-pass failure is dropped (unmarked) and the
    # reconnect replay re-lands the ids.
    monkeypatch.setattr(consumer, "FLUSH_RETRY_SCHEDULE", ())

    class OnceFailingCh(FakeCh):
        """CH blip: first insert fails, later inserts succeed."""

        async def insert(self, table, data, column_names=None, settings=None):
            if self.fail:
                self.fail = False
                raise RuntimeError("boom")
            self.calls.append((table, len(data)))
            self.rows += len(data)

    replay = b"".join(frame(str(i), payload()) for i in range(1, 1001))
    patch_http(
        monkeypatch,
        [FakeResponse(chunks=[replay]), FakeResponse(chunks=[replay])],
    )
    patch_state(monkeypatch, tmp_path)
    ch = OnceFailingCh(fail=True)  # inherits FakeCh.__init__; fails first insert only

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["insert_failed"] == 1000
    assert counters["duplicates_skipped"] == 0  # replay not eaten by the ring
    assert counters["total"] == 1000
    assert ch.rows == 1000
    assert consumer.load_state()["last_event_id"] == "1000"  # cursor: success only


def test_consume_retry_in_place_recovers_without_reconnect(
    monkeypatch, tmp_path, counters, stop, caplog
):
    """P0: a transient insert failure is retried in place (rows re-queued), so
    the batch lands without waiting for a reconnect."""
    monkeypatch.setattr(consumer, "FLUSH_RETRY_SCHEDULE", (0.0,))  # one retry
    calls = {"n": 0}

    async def backoff_then_stop(stop_ev, seconds):
        calls["n"] += 1
        return calls["n"] > 1  # 1st = retry backoff (proceed); 2nd = reconnect exit

    monkeypatch.setattr(consumer, "_sleep_or_stop", backoff_then_stop)

    class OnceFailingCh(FakeCh):
        async def insert(self, table, data, column_names=None, settings=None):
            if self.fail:
                self.fail = False
                raise RuntimeError("boom")
            self.calls.append((table, len(data)))
            self.rows += len(data)

    patch_http(
        monkeypatch,
        [
            FakeResponse(
                chunks=[b"".join(frame(str(i), payload()) for i in range(1, 1001))]
            )
        ],
    )
    patch_state(monkeypatch, tmp_path)
    ch = OnceFailingCh(fail=True)

    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["insert_failed"] == 0  # recovered in place, nothing dropped
    assert counters["total"] == 1000
    assert ch.rows == 1000
    assert "insert_retry events=1000 attempt=1" in caplog.text
    assert consumer.load_state()["last_event_id"] == "1000"


def test_dl_flush_does_not_advance_past_pending_main(
    monkeypatch, tmp_path, counters, stop
):
    """P1: a landed DL row (id 5) must not drag the cursor past a main batch
    (ids 1-3) that is still pending — a reconnect would then skip 1-3."""
    monkeypatch.setattr(consumer, "DL_FLUSH_INTERVAL", 0.01)
    patch_state(monkeypatch, tmp_path)

    class HoldOpen(FakeResponse):
        async def aiter_bytes(self):
            for i in (1, 2, 3):
                yield frame(str(i), payload())
            yield frame("5", "{bad")
            await stop.wait()

    patch_http(monkeypatch, [HoldOpen(chunks=[])])
    ch = FakeCh()

    async def scenario():
        task = asyncio.create_task(consumer.consume_forever(ch, stop, None, counters))
        try:
            deadline = time.monotonic() + 5.0
            while counters["dead_lettered"] == 0:
                assert time.monotonic() < deadline, "DL timer never flushed"
                await asyncio.sleep(0.01)
            # DL row landed, but main 1-3 is unflushed: no cursor was persisted
            # at all (a jump to "5" — the old behaviour — would have saved it).
            assert consumer.load_state() is None
        finally:
            stop.set()
            await asyncio.wait_for(task, 5.0)
        # Final flush lands main 1-3 → cursor 3, never 5.
        assert consumer.load_state()["last_event_id"] == "3"

    asyncio.run(scenario())


def test_shutdown_lands_buffered_dl_rows_before_state(
    monkeypatch, tmp_path, counters, stop
):
    """P1: a DL row buffered at shutdown still lands and the cursor advances,
    so save_state never strands it (a restart's replay would skip it)."""
    monkeypatch.setattr(consumer, "DL_FLUSH_INTERVAL", 3600.0)  # timer parked

    class StopAfterBad(FakeResponse):
        async def aiter_bytes(self):
            yield frame("7", "{bad")
            stop.set()  # observed at the next chunk boundary → clean exit
            yield frame("8", payload())

    patch_http(monkeypatch, [StopAfterBad(chunks=[])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["dead_lettered"] == 1  # buffered row landed at exit
    assert consumer.load_state()["last_event_id"] == "7"


def test_consume_flush_job_exception_logs_flush_failed(
    monkeypatch, tmp_path, counters, stop, caplog
):
    # _flush_job's except (lines 202-203): flush() itself raising (not the
    # batch-dropped None-mid path the batcher handles internally).
    real_flush = consumer.EventBatcher.flush
    calls = {"n": 0}

    async def boom_once(self, client):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("ch exploded")
        return await real_flush(self, client)

    patch_sleep(monkeypatch)
    patch_http(
        monkeypatch,
        [
            FakeResponse(
                chunks=[b"".join(frame(str(i), payload()) for i in range(1, 1001))]
            )
        ],
    )
    patch_state(monkeypatch, tmp_path)
    monkeypatch.setattr(consumer.EventBatcher, "flush", boom_once)
    ch = FakeCh()

    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        run(consumer.consume_forever(ch, stop, None, counters))

    assert "flush_failed reason=ch exploded" in caplog.text
    assert counters["total"] == 1000  # final flush still lands everything


def test_consume_non_200_retry_after_reconnect(
    monkeypatch, tmp_path, counters, stop, caplog
):
    patch_sleep(monkeypatch)
    patch_http(
        monkeypatch,
        [
            FakeResponse(status_code=503, headers={"Retry-After": "7"}),
            FakeResponse(chunks=[]),
        ],
    )
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        run(consumer.consume_forever(ch, stop, None, counters))

    assert "reconnect reason=http 503" in caplog.text


def test_consume_transport_exception_reconnects(
    monkeypatch, tmp_path, counters, stop, caplog
):
    patch_sleep(monkeypatch)
    patch_http(
        monkeypatch,
        [
            FakeResponse(
                chunks=[frame("1", payload()), b""],
                exc_after_n=2,
            )
        ],
    )
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        run(consumer.consume_forever(ch, stop, None, counters))

    assert "reconnect reason=ConnectionError: boom" in caplog.text
    assert counters["total"] == 1  # event 1 flushed at final flush


def test_consume_stop_at_chunk_boundary_final_flush(
    monkeypatch, tmp_path, counters, stop
):
    patch_sleep(monkeypatch)

    class StopAfterFirst(FakeResponse):
        async def aiter_bytes(self):
            for chunk in self.chunks:
                yield chunk
                stop.set()  # stop observed at the next chunk boundary
                break

    patch_http(
        monkeypatch,
        [StopAfterFirst(chunks=[frame("1", payload()), frame("2", payload())])],
    )
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["total"] == 1  # event 1 added, final flush on exit
    assert consumer.load_state()["last_event_id"] == "1"


def test_consume_stop_before_next_chunk_breaks_loop(
    monkeypatch, tmp_path, counters, stop
):
    # Line 229: stop observed AT a chunk boundary (generator keeps yielding,
    # unlike test_consume_stop_at_chunk_boundary_final_flush which ends).
    patch_sleep(monkeypatch)

    class StopMidStream(FakeResponse):
        async def aiter_bytes(self):
            yield self.chunks[0]
            stop.set()
            yield self.chunks[1]  # never consumed — loop breaks first

    patch_http(
        monkeypatch,
        [StopMidStream(chunks=[frame("1", payload()), frame("2", payload())])],
    )
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["total"] == 1  # only event 1 reached the parser
    assert consumer.load_state()["last_event_id"] == "1"


def test_consume_retry_frame_sets_retry_ms(
    monkeypatch, tmp_path, counters, stop, caplog
):
    patch_sleep(monkeypatch)
    patch_http(
        monkeypatch,
        [
            FakeResponse(
                chunks=[
                    b"retry: 500\nid: 2\ndata: " + payload().encode() + b"\n\n",
                    frame("3", payload()),
                ]
            )
        ],
    )
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["total"] == 2  # both events landed (final flush)
    assert "reconnect reason=stream ended" in caplog.text


def test_consume_60s_idle_stats_line(monkeypatch, tmp_path, counters, stop, caplog):
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[frame("1", payload())])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    # Zero the idle interval instead of faking time.monotonic (which asyncio
    # and the batcher also read — a clock patch makes the loop timing
    # nondeterministic). With interval=0 the n=0 stats line fires on the
    # first event; the test then only asserts the line's shape.
    monkeypatch.setattr(consumer, "IDLE_STATS_INTERVAL", 0.0)

    with caplog.at_level(logging.INFO, logger="wikistream.consumer"):
        run(consumer.consume_forever(ch, stop, None, counters))

    assert "inserted events=0 total=0 dead_lettered=0 insert_failed=0" in caplog.text


# ---- main() ----


def test_main_cold_start_returns_when_stop_requested(monkeypatch, caplog):
    async def boom(*args, **kwargs):
        raise RuntimeError("ch down")

    async def true_wait(stop_ev, seconds):
        return True

    monkeypatch.setattr(consumer, "get_async_client", boom)
    monkeypatch.setattr(consumer, "_sleep_or_stop", true_wait)

    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        run(consumer.main())

    assert "clickhouse_unavailable reason=ch down" in caplog.text


def test_main_happy_path_graceful_shutdown(monkeypatch, tmp_path):
    patch_state(monkeypatch, tmp_path)

    async def fake_consume(client, stop_ev, resumed_from, counters):
        stop_ev.set()
        return None

    async def fake_heartbeat(client, counters, stop_ev):
        return None

    class FakeCh:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    async def fake_get_client(**kw):
        # main() does `await get_async_client(...)` — must be a real coroutine
        # (a lambda returning FakeCh would TypeError in the cold-start retry
        # loop, which then retries forever).
        return FakeCh()

    monkeypatch.setattr(consumer, "get_async_client", fake_get_client)
    monkeypatch.setattr(consumer, "consume_forever", fake_consume)
    monkeypatch.setattr(consumer, "heartbeat_loop", fake_heartbeat)

    run(consumer.main())  # must return without hanging


def test_main_resumes_from_saved_state(monkeypatch, tmp_path, caplog):
    # Line 402: main() logs resumed_from when durable state exists.
    patch_state(monkeypatch, tmp_path)
    consumer.save_state("42", 5)
    seen = {}

    async def fake_consume(client, stop_ev, resumed_from, counters):
        seen["resumed_from"] = resumed_from
        seen["total"] = counters["total"]
        stop_ev.set()
        return None

    async def fake_heartbeat(client, counters, stop_ev):
        return None

    class FakeCh:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    async def fake_get_client(**kw):
        return FakeCh()

    monkeypatch.setattr(consumer, "get_async_client", fake_get_client)
    monkeypatch.setattr(consumer, "consume_forever", fake_consume)
    monkeypatch.setattr(consumer, "heartbeat_loop", fake_heartbeat)

    with caplog.at_level(logging.INFO, logger="wikistream.consumer"):
        run(consumer.main())

    assert seen == {"resumed_from": "42", "total": 5}
    assert "resumed_from=42" in caplog.text


def test_main_timeout_cancels_tasks(monkeypatch):
    async def hang(client, stop_ev, resumed_from, counters):
        # main() blocks on stop.wait() BEFORE the gather timeout can fire —
        # set stop as the SIGTERM signal would, then hang on the stream read
        # (chunk never arrives), so wait_for's timeout is the only way out.
        stop_ev.set()
        await asyncio.sleep(3600)

    async def noop(client, counters, stop_ev):
        return None

    class FakeCh:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    async def fake_get_client(**kw):
        # main() does `await get_async_client(...)` — must be a real coroutine
        # (a lambda returning FakeCh would TypeError in the cold-start retry
        # loop, which then retries forever).
        return FakeCh()

    async def fake_wait_for(coro, timeout):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(consumer, "get_async_client", fake_get_client)
    monkeypatch.setattr(consumer, "consume_forever", hang)
    monkeypatch.setattr(consumer, "heartbeat_loop", noop)
    monkeypatch.setattr(consumer.asyncio, "wait_for", fake_wait_for)

    run(consumer.main())


def test_module_main_guard(monkeypatch):
    """__main__ guard via in-process runpy (subprocesses are invisible to
    pytest-cov). runpy re-executes the module body in a FRESH namespace, so
    consumer-module monkeypatches don't reach it — instead asyncio.run (the
    same cached module object) is stubbed, and the fresh main() never touches
    ClickHouse or the SSE stream."""
    monkeypatch.setattr(asyncio, "run", lambda coro: None)
    saved_main = sys.modules.get("__main__")
    try:
        runpy.run_module("src.consumer", run_name="__main__")
    finally:
        # runpy leaves its fresh namespace under sys.modules["__main__"];
        # restore whatever pytest had there so later files are unaffected.
        if saved_main is None:
            sys.modules.pop("__main__", None)
        else:
            sys.modules["__main__"] = saved_main
