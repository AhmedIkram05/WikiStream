"""Stdlib-only asyncio SSE server fixture for testing the consumer.

Serves just enough WHATWG Server-Sent-Events (CRLF framing) to exercise
:mod:`consumer.src.sse`'s SSEParser and the streaming GET + reconnect loop
in :mod:`consumer.src.consumer` — no third-party HTTP server.

Usage (async test)::

    fixture = SSEFixture([("1", '{"wiki": "en"}'), ("2", '{"wiki": "de"}')])
    await fixture.start()
    try:
        ...  # point STREAM_URL at fixture.url and run the consumer
    finally:
        await fixture.stop()

Behaviour notes:

- Each event ``(id, data)`` is framed as ``id: <id>\\r\\ndata: <data>\\r\\n\\r\\n``;
  the ``id:`` line is omitted when id is None.
- A request's ``Last-Event-ID`` header resumes at the first event whose
  numeric id is strictly greater than the header value (ids are assumed
  digit strings; None counts as 0). No header means replay from event 0.
- ``retry_ms`` sends one ``retry:`` line per connection; ``disconnect_after``
  drops the connection after N events; ``hold_open`` keeps a drained stream
  alive with ``: ping`` comments (the parser ignores comments);
  ``pause()``/``resume()`` gate every event write.
"""

import asyncio

__all__ = ["SSEFixture"]


class SSEFixture:
    """Minimal SSE origin server: one connection handler per accepted socket."""

    def __init__(
        self,
        events: list[tuple[str | None, str]] | None = None,
        *,
        retry_ms: int | None = None,
        disconnect_after: int | None = None,
        hold_open: bool = False,
        event_interval_s: float = 0.0,
        port: int = 0,
    ) -> None:
        self.events = events or []
        self.retry_ms = retry_ms
        self.disconnect_after = disconnect_after
        self.hold_open = hold_open
        self.event_interval_s = event_interval_s
        self._port = port  # requested; replaced with the bound port on start()
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task] = set()
        self._gate = asyncio.Event()
        self._gate.set()  # initially resumed
        self.connections = 0
        self.last_event_id_received: str | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("SSEFixture not started")
        return f"http://127.0.0.1:{self._port}/"

    async def start(self) -> None:
        """Bind on 127.0.0.1 (port 0 -> ephemeral) and start serving."""
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self._port)
        self._port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Close every open connection and the listener."""
        for writer in self._writers:
            writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def pause(self) -> None:
        """Block event writes until resume()."""
        self._gate.clear()

    async def resume(self) -> None:
        """Unblock event writes."""
        self._gate.set()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        self._writers.add(writer)
        task = asyncio.current_task()
        assert task is not None  # server callbacks always run in a Task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        try:
            last_id = await self._read_last_event_id(reader)
            self.last_event_id_received = last_id
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Cache-Control: no-cache\r\n"
                b"\r\n"
            )
            if self.retry_ms is not None:
                writer.write(f"retry: {self.retry_ms}\r\n".encode())
            await writer.drain()
            await self._send_events(writer, self._replay_start(last_id))
        except OSError:
            pass  # peer vanished mid-write; just end this connection
        finally:
            self._writers.discard(writer)
            writer.close()

    async def _send_events(self, writer: asyncio.StreamWriter, start: int) -> None:
        events = self.events[start:]
        if not events and not self.hold_open:
            return  # empty replay range: close immediately
        sent = 0
        for event_id, data in events:
            await self._gate.wait()
            if self.event_interval_s:
                await asyncio.sleep(self.event_interval_s)
            if self.disconnect_after is not None and sent >= self.disconnect_after:
                await self._drop(writer)
                return
            frame = bytearray()
            if event_id is not None:
                frame += f"id: {event_id}\r\n".encode()
            frame += f"data: {data}\r\n\r\n".encode()
            writer.write(frame)
            await writer.drain()
            sent += 1
            if sent == self.disconnect_after:
                await self._drop(writer)
                return
        if self.hold_open:
            await self._heartbeat(writer)

    async def _heartbeat(self, writer: asyncio.StreamWriter) -> None:
        """Keep a drained connection open with comment frames until closed."""
        while True:
            await asyncio.sleep(1.0)
            await self._gate.wait()
            writer.write(b": ping\r\n")
            await writer.drain()

    async def _drop(self, writer: asyncio.StreamWriter) -> None:
        """Abrupt close: no graceful finish, no further bytes."""
        writer.close()
        await writer.wait_closed()

    def _replay_start(self, header: str | None) -> int:
        """First event index: first id strictly greater than Last-Event-ID."""
        if header is None or not header.isdigit():
            return 0
        last = int(header)
        for i, (event_id, _data) in enumerate(self.events):
            value = int(event_id) if event_id and event_id.isdigit() else 0
            if value > last:
                return i
        return len(self.events)  # everything already seen: empty range

    async def _read_last_event_id(self, reader: asyncio.StreamReader) -> str | None:
        """Parse the Last-Event-ID request header; None when absent/closed."""
        try:
            request = await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError:
            return None
        for line in request.decode("latin-1", "replace").split("\r\n")[1:]:
            if line.lower().startswith("last-event-id:"):
                return line.split(":", 1)[1].strip() or None
        return None
