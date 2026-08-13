"""State-IO, resume, and durable-id tests for the consumer (plan §4A).

Covers: save/load_state atomicity + corruption handling, Last-Event-ID
resume from durable state, the kill-mid-batch invariant (the persisted id
never advances past the last durable event), flush-failure semantics
(dropped, never dead-lettered), and counter progression end-to-end.

Integration mechanics (verified against the real consumer):
- durable_id advances ONLY on a successful flush or a dead-letter write,
  never per event — so with < max_rows events and no age trigger there is no
  mid-run flush and the state file only ever shows the resume id while the
  batch sits pending. The state file is therefore the ready signal: it
  appears when the first event is processed, and all fixture events arrive
  in one burst processed in the same synchronous loop iteration.
- The fixture runs WITHOUT hold_open: after the events it closes the
  connection, the consumer falls into the 1s reconnect sleep, and stop.set()
  there exits gracefully (final flush + unconditional save). With hold_open
  the consumer's stop check only fires per parsed event (comment heartbeats
  never dispatch) and SSEFixture.stop() deadlocks on wait_closed() while a
  live heartbeat handler holds the connection.
"""

import asyncio
import json
import time
from contextlib import suppress

from src.batcher import EventBatcher
import src.consumer as consumer
from sse_fixture import SSEFixture

EVENT = json.dumps(
    {
        "type": "edit",
        "title": "Test page",
        "user": "TestUser",
        "wiki": "enwiki",
        "timestamp": "2020-01-01T00:00:00Z",
        "bot": False,
    }
)


class FakeClient:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    async def insert(self, table, data, **kwargs):
        if self.fail:
            raise RuntimeError("ch down")
        self.calls.append({"table": table, "data": data, **kwargs})


def _constant_clock() -> float:
    return 0.0


def _counters() -> dict:
    return {"total": 0, "dead_lettered": 0, "insert_failed": 0, "duplicates_skipped": 0}


def _read_state(path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return state if isinstance(state, dict) else None


def _state_has_id(path, event_id: str) -> bool:
    state = _read_state(path)
    return state is not None and state.get("last_event_id") == event_id


async def _wait_for(predicate, timeout: float, message: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timeout: {message}")


async def _run_to_exit(fixture, client, resumed_from, counters, ready, timeout=15.0):
    """Run consume_forever until `ready()` holds, then stop gracefully and
    await the task (final flush + unconditional save). Always cleans up (stop
    signal + fixture close + cancel) so a failed assertion can never hang
    the suite."""
    stop = asyncio.Event()
    task = asyncio.create_task(
        consumer.consume_forever(client, stop, resumed_from, counters)
    )
    try:
        await _wait_for(ready, timeout, "consumer did not reach ready condition")
        stop.set()
        await fixture.stop()
        await asyncio.wait_for(task, timeout)
    finally:
        stop.set()
        await fixture.stop()
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(task, 5.0)
        await fixture.stop()


def test_save_state_atomic_no_partial(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_file = state_dir / "consumer_state.json"
    monkeypatch.setattr(consumer, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(consumer, "STATE_FILE", str(state_file))

    consumer.save_state("42", 7)

    state = json.loads(state_file.read_text())
    assert set(state) == {"last_event_id", "total", "updated_at"}
    assert state["last_event_id"] == "42"
    assert state["total"] == 7
    assert list(state_dir.glob("*.tmp")) == []


def test_load_state_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(consumer, "STATE_FILE", str(tmp_path / "missing.json"))

    assert consumer.load_state() is None


def test_save_load_roundtrip(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(consumer, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(consumer, "STATE_FILE", str(state_dir / "consumer_state.json"))

    consumer.save_state("42", 7)

    state = consumer.load_state()
    assert state is not None
    assert state["last_event_id"] == "42"
    assert state["total"] == 7
    assert isinstance(state["updated_at"], str) and state["updated_at"]


def test_load_state_corrupt_returns_none(tmp_path, monkeypatch, caplog):
    state_file = tmp_path / "consumer_state.json"
    state_file.write_text("{this is not json")
    monkeypatch.setattr(consumer, "STATE_FILE", str(state_file))

    assert consumer.load_state() is None
    assert "state_load_failed" in caplog.text


def test_resume_from_id_drives_initial_header(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_file = state_dir / "consumer_state.json"
    monkeypatch.setattr(consumer, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(consumer, "STATE_FILE", str(state_file))
    consumer.save_state("5", 0)
    initial_ts = _read_state(state_file)["updated_at"]
    events: list[tuple[str | None, str]] = [(str(i), EVENT) for i in range(6, 11)]

    async def scenario():
        fixture = SSEFixture(events)
        await fixture.start()
        monkeypatch.setattr(consumer, "STREAM_URL", fixture.url)
        counters = _counters()
        await _run_to_exit(
            fixture,
            FakeClient(),
            "5",
            counters,
            lambda: (
                _state_has_id(state_file, "5")
                and _read_state(state_file)["updated_at"] != initial_ts
            ),
        )
        assert fixture.last_event_id_received == "5"
        assert counters["total"] == 5
        assert counters["insert_failed"] == 0
        assert counters["duplicates_skipped"] == 0

    asyncio.run(scenario())

    state = _read_state(state_file)
    assert state is not None
    assert int(state["last_event_id"]) >= 6


def test_kill_mid_batch_persists_last_durable_not_last_seen(tmp_path, monkeypatch):
    batcher = EventBatcher(now=_constant_clock)
    for i in range(11, 21):
        batcher.add(("t", "d"), str(i))
    assert asyncio.run(batcher.flush(FakeClient(fail=True))) == (None, 10)
    assert batcher.pending_count == 0

    state_dir = tmp_path / "state"
    state_file = state_dir / "consumer_state.json"
    monkeypatch.setattr(consumer, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(consumer, "STATE_FILE", str(state_file))
    events: list[tuple[str | None, str]] = [(str(i), EVENT) for i in range(11, 21)]
    counters = _counters()

    async def scenario():
        fixture = SSEFixture(events)
        await fixture.start()
        monkeypatch.setattr(consumer, "STREAM_URL", fixture.url)
        await _run_to_exit(
            fixture,
            FakeClient(fail=True),
            "10",
            counters,
            lambda: _state_has_id(state_file, "10"),
        )

    asyncio.run(scenario())

    assert counters["insert_failed"] == 10
    assert counters["total"] == 0
    state = _read_state(state_file)
    assert state is not None
    assert state["last_event_id"] == "10"


def test_flush_failure_no_dead_letter(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_file = state_dir / "consumer_state.json"
    monkeypatch.setattr(consumer, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(consumer, "STATE_FILE", str(state_file))
    events: list[tuple[str | None, str]] = [(str(i), EVENT) for i in range(1, 6)]
    counters = _counters()
    calls = []

    async def scenario():
        async def spy(*args, **kwargs):
            calls.append((args, kwargs))

        monkeypatch.setattr(consumer, "write_dead_letter", spy)
        fixture = SSEFixture(events)
        await fixture.start()
        monkeypatch.setattr(consumer, "STREAM_URL", fixture.url)
        await _run_to_exit(
            fixture,
            FakeClient(fail=True),
            "0",
            counters,
            lambda: _state_has_id(state_file, "0"),
        )

    asyncio.run(scenario())

    assert calls == []
    assert counters["insert_failed"] == 5
    assert counters["dead_lettered"] == 0


def test_counters_advance(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_file = state_dir / "consumer_state.json"
    monkeypatch.setattr(consumer, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(consumer, "STATE_FILE", str(state_file))
    events: list[tuple[str | None, str]] = [(str(i), EVENT) for i in range(1, 51)]
    counters = _counters()

    async def scenario():
        fixture = SSEFixture(events)
        await fixture.start()
        monkeypatch.setattr(consumer, "STREAM_URL", fixture.url)
        await _run_to_exit(
            fixture,
            FakeClient(),
            "0",
            counters,
            lambda: _state_has_id(state_file, "0"),
        )

    asyncio.run(scenario())

    assert counters["total"] == 50
    assert counters["dead_lettered"] == 0
    assert counters["insert_failed"] == 0
    assert counters["duplicates_skipped"] == 0
    state = _read_state(state_file)
    assert state is not None
    assert int(state["last_event_id"]) >= 50
