"""EventBatcher unit tests (plan §4A)."""

import asyncio
from datetime import datetime, timezone

from src.batcher import EventBatcher, _cursor_ts, _max_id


class FakeClient:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    async def insert(self, table, **kwargs):
        if self.fail:
            raise RuntimeError("ch down")
        self.calls.append({"table": table, **kwargs})


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self) -> float:
        return self.t


def constant_clock() -> float:
    return 0.0


def test_1000th_row_triggers_flush():
    b = EventBatcher(max_rows=1000, max_age_s=9999, now=constant_clock)
    for _ in range(999):
        assert b.add(("2020-01-01", "{}"), None) is False
    assert b.add(("2020-01-01", "{}"), None) is True
    assert b.pending_count == 1000


def test_age_trigger_from_first_item():
    clock = FakeClock()
    b = EventBatcher(max_age_s=5.0, now=clock.now)
    assert b.add(("2020-01-01", "{}"), None) is False  # first row at t=0
    clock.t = 4.99
    assert b.add(("2020-01-01", "{}"), None) is False  # age from FIRST row
    clock.t = 5.0
    assert b.add(("2020-01-01", "{}"), None) is True


def test_flush_shape():
    fake = FakeClient()
    b = EventBatcher(now=constant_clock)
    rows = [
        (datetime(2020, 1, 1, tzinfo=timezone.utc), '{"a":1}'),
        (datetime(2020, 1, 2, tzinfo=timezone.utc), '{"b":2}'),
        (datetime(2020, 1, 3, tzinfo=timezone.utc), '{"c":3}'),
    ]
    for row, event_id in zip(rows, ["1", "2", "3"]):
        b.add(row, event_id)
    result = asyncio.run(b.flush(fake))
    assert result == ("3", 3)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["table"] == "default.raw_events"
    assert call["column_names"] == ["inserted_at", "event"]
    assert len(call["data"]) == 3
    assert isinstance(call["data"][0][0], datetime)
    assert isinstance(call["data"][0][1], str)
    assert call["settings"] == {"async_insert": 1, "wait_for_async_insert": 0}
    assert b.pending_count == 0


def test_flush_max_id_math():
    b = EventBatcher(now=constant_clock)
    for i in range(1, 11):
        b.add(("t", "d"), str(i))
    assert asyncio.run(b.flush(FakeClient())) == ("10", 10)

    b = EventBatcher(now=constant_clock)
    b.add(("t", "d"), "a")
    b.add(("t", "d"), "b")
    assert asyncio.run(b.flush(FakeClient())) == ("b", 2)

    # _max_id: numeric compare only when BOTH ids are digit-strings; "x" wins
    # over "5" and "12" by plain string comparison.
    b = EventBatcher(now=constant_clock)
    for event_id in ["5", "x", "12"]:
        b.add(("t", "d"), event_id)
    assert asyncio.run(b.flush(FakeClient())) == ("x", 3)


def test_flush_max_id_kafka_composite():
    """Kafka composite cursor ids: array order varies per event (eqiad-first
    vs codfw-first), so the max must be by partition timestamp, not string."""
    from src.batcher import _cursor_ts

    # noqa: E501 — JSON fixtures; splitting would break string literals
    old = '[{"topic":"eqiad.mediawiki.recentchange","partition":0,"timestamp":1786629538451},{"topic":"codfw.mediawiki.recentchange","partition":0,"offset":-1}]'  # noqa: E501
    new_eqiad_first = '[{"topic":"eqiad.mediawiki.recentchange","partition":0,"timestamp":1786692862935},{"topic":"codfw.mediawiki.recentchange","partition":0,"offset":-1}]'  # noqa: E501
    new_codfw_first = '[{"topic":"codfw.mediawiki.recentchange","partition":0,"offset":-1},{"topic":"eqiad.mediawiki.recentchange","partition":0,"timestamp":1786692862935}]'  # noqa: E501

    b = EventBatcher(now=constant_clock)
    b.add(("t", "d"), old)
    b.add(("t", "d"), new_codfw_first)
    assert _cursor_ts(asyncio.run(b.flush(FakeClient()))[0]) == 1786692862935

    b = EventBatcher(now=constant_clock)
    b.add(("t", "d"), new_codfw_first)
    b.add(("t", "d"), new_eqiad_first)
    assert _cursor_ts(asyncio.run(b.flush(FakeClient()))[0]) == 1786692862935

    b = EventBatcher(now=constant_clock)
    b.add(("t", "d"), new_eqiad_first)
    b.add(("t", "d"), old)
    assert _cursor_ts(asyncio.run(b.flush(FakeClient()))[0]) == 1786692862935


def test_failed_flush_drops_rows():
    fake = FakeClient(fail=True)
    b = EventBatcher(now=constant_clock)
    for event_id in ["1", "2", "3"]:
        b.add(("t", "d"), event_id)
    assert asyncio.run(b.flush(fake)) == (None, 3)
    assert b.pending_count == 0
    b.add(("t", "d"), "4")
    assert b.pending_count == 1


def test_flush_empty():
    fake = FakeClient()
    assert asyncio.run(EventBatcher().flush(fake)) == (None, 0)
    assert fake.calls == []


def test_dedup_ring_bounded():
    b = EventBatcher(dedup_capacity=50_000)
    for i in range(1, 60001):
        b.add(("t", "d"), f"{i}")
    seen_count = sum(1 for i in range(1, 60001) if b.seen(f"{i}"))
    assert seen_count == 50_000
    assert b.seen("1") is False  # oldest evicted
    assert b.seen("60000") is True  # newest kept


def test_seen_semantics():
    b = EventBatcher()
    assert b.seen("x") is False
    b.add(("t", "d"), "x")
    assert b.seen("x") is True
    assert b.seen(None) is False
    assert b.seen(None) is False  # never marks
    b.add(("t", "d"), None)  # None id doesn't pollute the ring
    assert b.seen("None") is False


def test_cursor_ts_edges():
    """Phase 6 gap: _cursor_ts except-branch and non-list inputs were
    uncovered — these are the malformed-cursor-id defensive paths."""
    assert _cursor_ts("[not json") == 0  # ValueError
    assert _cursor_ts(123) == 0  # TypeError
    assert _cursor_ts('{"topic":"eqiad"}') == 0  # dict, not a list
    assert _cursor_ts('[1, 2, "x"]') == 0  # no dict entries
    assert _cursor_ts('[{"offset": -1}, {"timestamp": 5}]') == 5
    assert _cursor_ts('[{"timestamp": 0}, {"offset": 1}]') == 1


def test_max_id_garbage_json_arrays():
    """Both ids unparseable as JSON — fall back to the left id (ties)."""
    assert _max_id("[", "[x") == "["
    assert _max_id('[{"offset":-1}]', '[{"timestamp":7}]') == '[{"timestamp":7}]'
