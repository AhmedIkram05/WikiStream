"""consumer.py loop unit tests — hermetic, no ClickHouse, no network (plan 6.1).

The real ch integration suites (test_malformed_to_dead_letter, kill/resume)
already exercise consume_forever against live ClickHouse; these tests drive
the same code paths with fakes so the ~90% overall gate is measurable in the
hermetic unit job. httpx2.AsyncClient, get_async_client, write_dead_letter and
heartbeat_loop are all monkeypatched; the EventBatcher is real (it has its own
100%-covered suite).
"""

import asyncio
import json
import logging
import runpy
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import consumer

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
    """Duck-typed clickhouse async client used by EventBatcher.flush."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list = []
        self.rows = 0

    async def insert(self, table, data, column_names=None, settings=None):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((table, len(data)))
        self.rows += len(data)


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

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def stream(self, method, url, headers=None):
        # SYNC: the consumer does `async with http.stream(...)` (no await on
        # the call itself), matching httpx2's API. An async def here returns
        # a coroutine, which would TypeError inside async-with — swallowed by
        # consume_forever's except and silently zeroing every assertion.
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


@pytest.fixture
def fake_dl(monkeypatch):
    """write_dead_letter → configurable async fake (default: row lands)."""

    async def dl(client, *, reason, wiki, title, event):
        dl.calls.append((reason, wiki, title, event))
        return dl.land

    dl.calls = []
    dl.land = True
    monkeypatch.setattr(consumer, "write_dead_letter", dl)
    return dl


def patch_sleep(monkeypatch):
    """Break the reconnect wait: loop exits after the first reconnect."""
    async def no_wait(stop_ev, seconds):
        return True

    monkeypatch.setattr(consumer, "_sleep_or_stop", no_wait)


def patch_http(monkeypatch, responses):
    monkeypatch.setattr(consumer.httpx2, "AsyncClient", lambda **kw: FakeHTTP(responses))


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
    assert consumer._parse_retry_after(SimpleNamespace(headers={"Retry-After": "5"})) == 5
    assert consumer._parse_retry_after(SimpleNamespace(headers={})) is None
    assert consumer._parse_retry_after(SimpleNamespace(headers={"Retry-After": "abc"})) is None
    assert consumer._parse_retry_after(SimpleNamespace(headers={"Retry-After": "-3"})) == 0


def test_sleep_or_stop_real():
    async def check():
        stop = asyncio.Event()
        assert await consumer._sleep_or_stop(stop, 0.01) is False  # timeout, stop unset
        stop.set()
        assert await consumer._sleep_or_stop(stop, 0.01) is True  # stop during wait

    run(check())


def test_cursor_ts_edges_consumer_copy():
    assert consumer._cursor_ts("[not json") == 0
    assert consumer._cursor_ts(123) == 0
    assert consumer._cursor_ts('{"topic": "eqiad"}') == 0
    assert consumer._cursor_ts('[1, 2, "x"]') == 0
    assert consumer._cursor_ts('[{"offset": -1}, {"timestamp": 5}]') == 5
    assert consumer._cursor_ts('[{"timestamp": 0}, {"offset": 1}]') == 1


def test_max_id_edges_consumer_copy():
    assert consumer._max_id(None, "b") == "b"
    assert consumer._max_id("a", None) == "a"
    assert consumer._max_id("5", "10") == "10"
    assert consumer._max_id("10", "10") == "10"
    assert consumer._max_id("[", "[x") == "["
    assert consumer._max_id('[{"offset": -1}]', '[{"timestamp": 7}]') == '[{"timestamp": 7}]'


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
    path.write_text(
        json.dumps({"last_event_id": 42, "total": "many"})
    )
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
    monkeypatch, tmp_path, counters, stop, fake_dl, caplog
):
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[b"".join(
        frame(str(i), payload()) for i in range(1, 1002)
    )])])
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
    assert fake_dl.calls == []


def test_consume_bad_json_goes_dead_letter(
    monkeypatch, tmp_path, counters, stop, fake_dl
):
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[frame("7", "{not json")])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["dead_lettered"] == 1
    assert counters["total"] == 0
    assert fake_dl.calls == [("validation:invalid_json", "", "", "{not json")]
    assert consumer.load_state()["last_event_id"] == "7"


def test_consume_bad_ts_and_non_dict_go_dead_letter(
    monkeypatch, tmp_path, counters, stop, fake_dl
):
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[
        frame("8", payload(timestamp=None)),
        # valid JSON but an array: ts lookup falls back to None
        frame("9", '["no", "dict", "here"]'),
    ])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["dead_lettered"] == 2
    assert fake_dl.calls[0] == ("timestamp_missing", "enwiki", "Test page", json.dumps(
        dict(VALID, timestamp=None)
    ))
    assert fake_dl.calls[1][0] == "timestamp_missing"
    assert fake_dl.calls[1][1:3] == ("", "")  # _dl_fields non-dict branch


def test_consume_validation_error_goes_dead_letter(
    monkeypatch, tmp_path, counters, stop, fake_dl
):
    patch_sleep(monkeypatch)
    # title ABSENT (not None): pydantic reports "missing" only for a
    # genuinely absent required key — None yields "string_type".
    bad = dict(VALID)
    bad.pop("title")
    patch_http(monkeypatch, [FakeResponse(chunks=[
        frame("10", json.dumps(bad))
    ])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["dead_lettered"] == 1
    assert fake_dl.calls[0][0] == "validation:missing"
    assert fake_dl.calls[0][1] == "enwiki"


def test_consume_dl_write_failure_keeps_cursor(
    monkeypatch, tmp_path, counters, stop, fake_dl
):
    fake_dl.land = False
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[frame("7", "{not json")])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["dead_lettered"] == 0
    assert consumer.load_state()["last_event_id"] is None  # cursor did NOT advance


def test_consume_duplicate_ids_skipped(monkeypatch, tmp_path, counters, stop, fake_dl):
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[
        frame("5", payload()),
        frame("5", payload()),
        frame("6", payload()),
    ])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["duplicates_skipped"] == 1
    assert counters["total"] == 2  # final flush on exit
    assert ch.rows == 2


def test_consume_flush_failure_counts_insert_failed(
    monkeypatch, tmp_path, counters, stop, fake_dl, caplog
):
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[b"".join(
        frame(str(i), payload()) for i in range(1, 1001)
    )])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh(fail=True)

    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["insert_failed"] == 1000
    assert counters["total"] == 0
    assert "insert_failed events=1000 reason=batch dropped" in caplog.text
    assert consumer.load_state()["last_event_id"] is None


def test_consume_non_200_retry_after_reconnect(
    monkeypatch, tmp_path, counters, stop, fake_dl, caplog
):
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [
        FakeResponse(status_code=503, headers={"Retry-After": "7"}),
        FakeResponse(chunks=[]),
    ])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        run(consumer.consume_forever(ch, stop, None, counters))

    assert "reconnect reason=http 503" in caplog.text


def test_consume_transport_exception_reconnects(
    monkeypatch, tmp_path, counters, stop, fake_dl, caplog
):
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(
        chunks=[frame("1", payload()), b""],
        exc_after_n=2,
    )])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        run(consumer.consume_forever(ch, stop, None, counters))

    assert "reconnect reason=ConnectionError: boom" in caplog.text
    assert counters["total"] == 1  # event 1 flushed at final flush


def test_consume_stop_at_chunk_boundary_final_flush(
    monkeypatch, tmp_path, counters, stop, fake_dl
):
    patch_sleep(monkeypatch)

    class StopAfterFirst(FakeResponse):
        async def aiter_bytes(self):
            for chunk in self.chunks:
                yield chunk
                stop.set()  # stop observed at the next chunk boundary
                break

    patch_http(monkeypatch, [StopAfterFirst(
        chunks=[frame("1", payload()), frame("2", payload())]
    )])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["total"] == 1  # event 1 added, final flush on exit
    assert consumer.load_state()["last_event_id"] == "1"


def test_consume_retry_frame_sets_retry_ms(
    monkeypatch, tmp_path, counters, stop, fake_dl, caplog
):
    patch_sleep(monkeypatch)
    patch_http(monkeypatch, [FakeResponse(chunks=[
        b"retry: 500\nid: 2\ndata: " + payload().encode() + b"\n\n",
        frame("3", payload()),
    ])])
    patch_state(monkeypatch, tmp_path)
    ch = FakeCh()

    with caplog.at_level(logging.WARNING, logger="wikistream.consumer"):
        run(consumer.consume_forever(ch, stop, None, counters))

    assert counters["total"] == 2  # both events landed (final flush)
    assert "reconnect reason=stream ended" in caplog.text


def test_consume_60s_idle_stats_line(monkeypatch, tmp_path, counters, stop, fake_dl, caplog):
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
