"""Dead-letter writer unit tests (plan §4A)."""

import asyncio
from datetime import datetime

from src.dead_letter import write_dead_letter


class FakeClient:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    async def insert(self, table, data, **kwargs):
        if self.fail:
            raise RuntimeError("ch down")
        self.calls.append({"table": table, "data": data, **kwargs})


def test_success_path():
    fake = FakeClient()
    ok = asyncio.run(
        write_dead_letter(
            fake, reason="validation:invalid_json", wiki="w", title="t", event="e"
        )
    )
    assert ok is True
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["table"] == "default.dead_letter"
    assert call["column_names"] == ["inserted_at", "reason", "wiki", "title", "event"]
    assert len(call["data"]) == 1
    assert isinstance(call["data"][0][0], datetime)
    assert call["data"][0][1:] == ["validation:invalid_json", "w", "t", "e"]
    assert call["settings"] == {"async_insert": 0}


def test_failure_never_raises(caplog):
    fake = FakeClient(fail=True)
    ok = asyncio.run(
        write_dead_letter(fake, reason="r", wiki="w", title="t", event="e")
    )
    assert ok is False
    assert "dead_letter_write_failed" in caplog.text
