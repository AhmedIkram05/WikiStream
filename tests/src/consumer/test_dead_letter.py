"""Dead-letter batcher unit tests (plan §4A)."""

import asyncio
from datetime import datetime

from src.dead_letter import COLUMNS, SETTINGS, TABLE, DeadLetterBatcher


class FakeClient:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    async def insert(self, table, data, **kwargs):
        if self.fail:
            raise RuntimeError("ch down")
        self.calls.append({"table": table, "data": data, **kwargs})


def add_three(dl: DeadLetterBatcher) -> None:
    dl.add(reason="r1", wiki="w", title="t", event="e1", event_id="1")
    dl.add(reason="r2", wiki="w", title="t", event="e2", event_id="2")
    dl.add(reason="r3", wiki="w", title="t", event="e3", event_id="3")


def test_buffer_until_max_rows_then_flush():
    fake = FakeClient()
    dl = DeadLetterBatcher(max_rows=3)
    assert dl.pending_count == 0
    assert dl.add(reason="r1", wiki="w", title="t", event="e1", event_id="1") is False
    assert dl.add(reason="r2", wiki="w", title="t", event="e2", event_id="2") is False
    assert dl.pending_count == 2
    assert fake.calls == []  # nothing written before flush
    assert dl.add(reason="r3", wiki="w", title="t", event="e3", event_id="3") is True

    dropped, count, max_id = asyncio.run(dl.flush(fake))
    assert (dropped, count, max_id) == (False, 3, "3")
    assert dl.pending_count == 0
    call = fake.calls[0]
    assert call["table"] == TABLE == "default.dead_letter"
    assert (
        call["column_names"]
        == COLUMNS
        == [
            "inserted_at",
            "reason",
            "wiki",
            "title",
            "event",
        ]
    )
    assert (
        call["settings"]
        == SETTINGS
        == {
            "async_insert": 1,
            "wait_for_async_insert": 0,
        }
    )
    assert len(call["data"]) == 3
    assert isinstance(call["data"][0][0], datetime)
    assert call["data"][0][1:] == ["r1", "w", "t", "e1"]
    assert call["data"][2][1:] == ["r3", "w", "t", "e3"]


def test_flush_empty_is_noop():
    dropped, count, max_id = asyncio.run(DeadLetterBatcher().flush(FakeClient()))
    assert (dropped, count, max_id) == (False, 0, None)


def test_flush_failure_drops_batch_without_max_id(caplog):
    dl = DeadLetterBatcher()
    add_three(dl)
    with caplog.at_level("WARNING", logger="wikistream.dead_letter"):
        dropped, count, max_id = asyncio.run(dl.flush(FakeClient(fail=True)))
    assert (dropped, count, max_id) == (True, 3, None)
    assert dl.pending_count == 0  # consumed either way (at-most-once)
    assert "dead_letter_flush_failed" in caplog.text


def test_max_id_digit_compare():
    dl = DeadLetterBatcher()
    dl.add(reason="r", wiki="", title="", event="e", event_id="5")
    dl.add(reason="r", wiki="", title="", event="e", event_id="10")
    dropped, count, max_id = asyncio.run(dl.flush(FakeClient()))
    assert max_id == "10"


def test_max_id_composite_cursor_compare():
    dl = DeadLetterBatcher()
    dl.add(reason="r", wiki="", title="", event="e", event_id="999")
    dl.add(
        reason="r",
        wiki="",
        title="",
        event="e",
        event_id='[{"offset": -1}, {"timestamp": 1000}]',
    )
    dropped, count, max_id = asyncio.run(dl.flush(FakeClient()))
    assert max_id == '[{"offset": -1}, {"timestamp": 1000}]'


def test_rows_without_event_id_flush_with_none_max_id():
    """Frames without an SSE id: counted, but nothing to advance."""
    dl = DeadLetterBatcher()
    assert dl.add(reason="r", wiki="", title="", event="e", event_id=None) is False
    dropped, count, max_id = asyncio.run(dl.flush(FakeClient()))
    assert (dropped, count, max_id) == (False, 1, None)


def test_landed_marks_only_after_successful_flush():
    """A landed row's id is remembered; a dropped or never-flushed row is not."""
    dl = DeadLetterBatcher()
    dl.add(reason="r", wiki="", title="", event="e", event_id="7")
    assert dl.landed("7") is False  # buffered, not yet landed
    asyncio.run(dl.flush(FakeClient()))
    assert dl.landed("7") is True

    failed = DeadLetterBatcher()
    failed.add(reason="r", wiki="", title="", event="e", event_id="8")
    asyncio.run(failed.flush(FakeClient(fail=True)))
    assert failed.landed("8") is False  # dropped: replay must re-run it


def test_landed_none_and_idless_rows_never_mark():
    dl = DeadLetterBatcher()
    assert dl.landed(None) is False
    dl.add(reason="r", wiki="", title="", event="e", event_id=None)
    asyncio.run(dl.flush(FakeClient()))
    assert dl.landed("anything") is False


def test_landed_ring_evicts_oldest_and_ignores_duplicate_marks():
    dl = DeadLetterBatcher(dedup_capacity=2)
    for eid in ("1", "2", "3"):
        dl.add(reason="r", wiki="", title="", event="e", event_id=eid)
        asyncio.run(dl.flush(FakeClient()))
    assert dl.landed("1") is False  # evicted
    assert dl.landed("2") and dl.landed("3")
    # re-marking an existing id must not desync ring and set
    dl.add(reason="r", wiki="", title="", event="e", event_id="2")
    asyncio.run(dl.flush(FakeClient()))
    assert len(dl._landed) == len(dl._landed_set) == 2
