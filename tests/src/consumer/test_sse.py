"""SSE parser unit tests (plan §6.5).

Run from repo root: PYTHONPATH=consumer pytest tests/ -v
"""

from src.sse import SSEParser


def test_single_complete_frame():
    parser = SSEParser()
    events = parser.feed(b'event: update\ndata: {"rev": 1}\nid: 42\n\n')
    assert len(events) == 1
    ev = events[0]
    assert ev.event == "update"
    assert ev.data == '{"rev": 1}'
    assert ev.id == "42"


def test_frame_split_across_two_chunks():
    parser = SSEParser()
    # trailing \r held: it may pair with a following \n
    assert parser.feed(b"data: hello\r") == []
    events = parser.feed(b"\n\n")
    assert len(events) == 1
    assert events[0].data == "hello"


def test_three_frames_in_one_chunk():
    parser = SSEParser()
    events = parser.feed(b"data: a\n\ndata: b\n\ndata: c\n\n")
    assert [ev.data for ev in events] == ["a", "b", "c"]


def test_crlf_line_endings():
    parser = SSEParser()
    events = parser.feed(b"data: a\r\ndata: b\r\n\r\n")
    assert len(events) == 1
    assert events[0].data == "a\nb"


def test_bare_cr_line_terminator():
    parser = SSEParser()
    events = parser.feed(b"data: a\rdata: b\r\r")
    assert len(events) == 1
    assert events[0].data == "a\nb"


def test_comment_lines_ignored():
    parser = SSEParser()
    assert parser.feed(b": health check\n: another comment\n\n") == []


def test_multiline_data_joined_with_newline():
    parser = SSEParser()
    events = parser.feed(b"data: line1\ndata: line2\ndata: line3\n\n")
    assert len(events) == 1
    assert events[0].data == "line1\nline2\nline3"


def test_id_captured_for_resume():
    parser = SSEParser()
    events = parser.feed(b"id: abc-123\ndata: x\n\n")
    assert events[0].id == "abc-123"


def test_id_strips_exactly_one_leading_space():
    parser = SSEParser()
    events = parser.feed(b"id: 1234\ndata: x\n\n")
    assert events[0].id == "1234"
    # exactly ONE space stripped: two leading spaces keep one
    events = parser.feed(b"id:  1234\ndata: x\n\n")
    assert events[0].id == " 1234"


def test_data_only_frame_defaults_to_message():
    parser = SSEParser()
    events = parser.feed(b"data: hello\n\n")
    assert len(events) == 1
    assert events[0].event == "message"


def test_retry_captured_as_int_ms():
    parser = SSEParser()
    events = parser.feed(b"retry: 5000\ndata: x\n\n")
    assert events[0].retry == 5000


def test_retry_non_digits_ignored():
    parser = SSEParser()
    events = parser.feed(b"retry: 30s\ndata: x\n\n")
    assert events[0].retry is None


def test_id_with_nul_ignored():
    parser = SSEParser()
    events = parser.feed(b"id: bad\x00id\ndata: x\n\n")
    assert events[0].id is None


def test_unknown_field_ignored():
    parser = SSEParser()
    events = parser.feed(b"foo: bar\ndata: x\n\n")
    assert len(events) == 1
    assert events[0].data == "x"


def test_malformed_line_skipped():
    parser = SSEParser()
    events = parser.feed(b"this line has no colon\ndata: x\n\n")
    assert len(events) == 1
    assert events[0].data == "x"


def test_empty_data_value_still_emitted():
    parser = SSEParser()
    events = parser.feed(b"data:\n\n")
    assert len(events) == 1
    assert events[0].data == ""
    assert events[0].event == "message"


def test_flush_discards_trailing_partial_frame():
    parser = SSEParser()
    assert parser.feed(b"data: partial") == []
    assert parser.flush() == []


def test_flush_after_clean_frame_resets():
    parser = SSEParser()
    assert len(parser.feed(b"data: a\n\n")) == 1
    assert parser.flush() == []
    # parser is usable after flush — no leftover state
    events = parser.feed(b"data: b\n\n")
    assert len(events) == 1
    assert events[0].data == "b"


def test_multibyte_utf8_split_across_chunks():
    parser = SSEParser()
    raw = "data: café ☕\n\n".encode()
    assert parser.feed(raw[:10]) == []  # split inside the é (2-byte char)
    events = parser.feed(raw[10:])
    assert len(events) == 1
    assert events[0].data == "café ☕"
