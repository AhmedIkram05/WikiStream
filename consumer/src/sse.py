"""Hand-rolled WHATWG SSE parser (ADR-004, plan §6.1).

Business-critical: this is the consumer's only source of events. Deliberately
dependency-free — stdlib only (dataclasses, codecs). No SSE library.
"""

import codecs
from dataclasses import dataclass


@dataclass
class SSEEvent:
    event: str = "message"     # default per spec when no `event:` field
    data: str = ""
    id: str | None = None      # for Last-Event-ID resume
    retry: int | None = None   # reconnect hint, in ms


class SSEParser:
    """Incremental SSE parser: feed() bytes in, complete SSEEvents out."""

    def __init__(self) -> None:
        # Streaming is UTF-8: one incremental decoder across feed() calls so a
        # multi-byte char split across chunks decodes cleanly.
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""
        self._reset_event()

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        self._buffer += self._decoder.decode(chunk)
        return self._parse()

    def flush(self) -> list[SSEEvent]:
        # EOF: discard any trailing partial frame (WHATWG: an incomplete event
        # at EOF is not dispatched — emitting it would insert truncated JSON
        # garbage on every reconnect) and reset for a fresh stream.
        self._buffer = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._reset_event()
        return []

    def _reset_event(self) -> None:
        self._event_type = ""
        self._data_lines: list[str] = []
        self._has_data = False
        self._event_id: str | None = None
        self._retry: int | None = None

    def _parse(self) -> list[SSEEvent]:
        events: list[SSEEvent] = []
        buf = self._buffer
        i = 0
        n = len(buf)
        while i < n:
            j = i
            while j < n and buf[j] not in "\r\n":
                j += 1
            if j == n:
                break  # no line terminator yet — hold the rest
            line = buf[i:j]
            if buf[j] == "\r" and j + 1 == n and (j == 0 or buf[j - 1] not in "\r\n"):
                break  # trailing \r may pair with a following \n — hold it
            if buf[j] == "\r" and j + 1 < n and buf[j + 1] == "\n":
                j += 1  # CRLF counts as one terminator
            i = j + 1
            if line:
                self._feed_line(line)
            else:
                event = self._dispatch()
                if event is not None:
                    events.append(event)
        self._buffer = buf[i:]
        return events

    def _feed_line(self, line: str) -> None:
        if line.startswith(":"):
            return  # comment / heartbeat — ignored
        colon = line.find(":")
        if colon == -1:
            return  # malformed line (no colon) — skipped
        field = line[:colon]
        value = line[colon + 1:]
        if value.startswith(" "):
            value = value[1:]  # strip exactly ONE leading space (WHATWG)
        if field == "event":
            self._event_type = value
        elif field == "data":
            self._data_lines.append(value)
            self._has_data = True
        elif field == "id":
            if "\x00" not in value:
                self._event_id = value
        elif field == "retry":
            if value.isdigit():
                self._retry = int(value)
        # any other field name is ignored per spec

    def _dispatch(self) -> SSEEvent | None:
        if not self._has_data:
            self._reset_event()  # WHATWG: empty data buffer → not dispatched
            return None
        event = SSEEvent(
            event=self._event_type or "message",
            data="\n".join(self._data_lines),
            id=self._event_id,
            retry=self._retry,
        )
        self._reset_event()
        return event
