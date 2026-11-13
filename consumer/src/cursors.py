"""Shared event-id comparison helpers (single source for consumer + batcher).

Event ids come in two shapes: Wikimedia digit-strings ("12345") and Kafka
composite cursors (a JSON array of per-partition positions). Both sites that
advance a durable id over these — EventBatcher/DeadLetterBatcher and the
consume loop — must compare them identically, so the logic lives here once
instead of drifting between near-identical copies.
"""

import json
import logging

logger = logging.getLogger("wikistream.cursors")

# Once-per-category guard: the helpers below are called per event from the
# batchers, so a changed upstream id format would otherwise emit one warning
# per streamed event.
_warned_unparsable: set[str] = set()


def _warn_unparsable(s: str) -> None:
    """WARNING naming the unparsable id shape, at most once per category."""
    kind = "composite" if s.startswith("[") else "plain"
    if kind in _warned_unparsable:
        return
    _warned_unparsable.add(kind)
    logger.warning("cursor_id_unparsable kind=%s example=%r", kind, s[:64])


def _parse_cursor(s: str) -> list | None:
    """json.loads(s) when it is a cursor array, else None (one parse)."""
    try:
        cursors = json.loads(s)
    except (TypeError, ValueError):
        return None
    return cursors if isinstance(cursors, list) else None


def _composite_rank(s: str) -> int | None:
    """Highest partition position in a composite cursor array, else None.

    The single parse behind _cursor_ts, _is_composite and _rank, so one id is
    never decoded twice for the same comparison. Returns None when s is not a
    cursor array or carries no integer position; a parseable array whose
    positions are all <= 0 ranks as 0 (matching the historical _cursor_ts floor).
    """
    cursors = _parse_cursor(s)
    if cursors is None:
        return None
    best = 0
    found = False
    for c in cursors:
        if isinstance(c, dict):
            for key in ("timestamp", "offset"):
                v = c.get(key)
                if isinstance(v, int):
                    found = True
                    if v > best:
                        best = v
    return best if found else None


def _cursor_ts(s: str) -> int:
    """Highest partition position in a Kafka composite cursor id.

    The id is a JSON array of per-partition cursors like
    `[{"topic":"eqiad...","timestamp":1786...},{"topic":"codfw...","offset":-1}]`.
    The ARRAY ORDER varies between events (eqiad-first or codfw-first), so a
    raw string compare is meaningless — compare the max numeric position.
    Unparseable / non-cursor input ranks 0.
    """
    rank = _composite_rank(s)
    return 0 if rank is None else rank


def _is_composite(s: str) -> bool:
    """True when s is a parseable composite cursor array (a JSON list carrying
    at least one integer timestamp/offset position)."""
    return _composite_rank(s) is not None


def _is_ascii_digits(s: str) -> bool:
    """True for ASCII digits only; str.isdigit() also accepts '²' etc."""
    return s.isascii() and s.isdigit()


def _rank(s: str) -> int | None:
    """Numeric rank for a well-formed id, else None (unparsable shape).

    Well-formed = a Wikimedia digit-string (its value) or a parseable composite
    cursor (its highest partition position). A None rank must never win a
    comparison: treating an unknown id as the oldest value would silently rewind
    the cursor into mass reprocessing on an upstream id-format change.
    """
    if _is_ascii_digits(s):
        return int(s)
    return _composite_rank(s)


def _max_id(a: str | None, b: str | None) -> str | None:
    """Larger of two ids: None-handling; numeric compare for digit-strings;
    composite cursor ids (leading '[') compare by highest partition position.
    An unparsable id never wins: keep the parseable side (never move backwards).
    """
    if a is None:
        return b
    if b is None:
        return a
    if a.isdigit() and b.isdigit():
        a_ok, b_ok = _is_ascii_digits(a), _is_ascii_digits(b)
        if a_ok and b_ok:
            return a if int(a) >= int(b) else b
        if a_ok:
            _warn_unparsable(b)
            return a
        if b_ok:
            _warn_unparsable(a)
            return b
        _warn_unparsable(a)
        return a
    if a.startswith("[") or b.startswith("["):
        ra, rb = _rank(a), _rank(b)
        if ra is None and rb is None:
            return a
        if ra is None:
            _warn_unparsable(a)
            return b
        if rb is None:
            _warn_unparsable(b)
            return a
        return a if ra >= rb else b
    return a if a >= b else b


def _advance_max_id(
    current_id: str | None,
    current_rank: int | None,
    new_id: str | None,
) -> tuple[str | None, int | None]:
    """Larger of (current_id, new_id) plus its rank, parsing new_id at most once.

    Mirrors _max_id's selection exactly (digit, composite and lexical shapes,
    the unparsable-id guard and its once-per-kind warning) but carries the
    running maximum's already-computed rank, so the per-event caller parses only
    the incoming id instead of decoding both sides on every event.
    """
    if current_id is None:
        return new_id, (None if new_id is None else _rank(new_id))
    if new_id is None:
        return current_id, current_rank
    a, b = current_id, new_id
    if a.isdigit() and b.isdigit():
        a_ok, b_ok = _is_ascii_digits(a), _is_ascii_digits(b)
        if a_ok and b_ok:
            return (a, int(a)) if int(a) >= int(b) else (b, int(b))
        if a_ok:
            _warn_unparsable(b)
            return a, int(a)
        if b_ok:
            _warn_unparsable(a)
            return b, int(b)
        _warn_unparsable(a)
        return a, None
    if a.startswith("[") or b.startswith("["):
        ra = _rank(a) if current_rank is None else current_rank
        rb = _rank(b)
        if ra is None and rb is None:
            return a, None
        if ra is None:
            _warn_unparsable(a)
            return b, rb
        if rb is None:
            _warn_unparsable(b)
            return a, ra
        return (a, ra) if ra >= rb else (b, rb)
    return (a, current_rank) if a >= b else (b, None)


def _min_id(a: str | None, b: str | None) -> str | None:
    """Smaller of two ids, mirroring _max_id. None means "no id known", i.e.
    −∞, so any pair containing a None collapses to None. Used to clamp a
    cursor advance at an unlanded dead-letter row's watermark: a None hold
    means nothing was durable yet, so no advance is allowed."""
    if a is None or b is None:
        return None
    if a.isdigit() and b.isdigit():
        a_ok, b_ok = _is_ascii_digits(a), _is_ascii_digits(b)
        if a_ok and b_ok:
            return a if int(a) <= int(b) else b
        if a_ok:
            _warn_unparsable(b)
            return a
        if b_ok:
            _warn_unparsable(a)
            return b
        _warn_unparsable(a)
        return a
    if a.startswith("[") or b.startswith("["):
        ra, rb = _rank(a), _rank(b)
        if ra is None and rb is None:
            return a
        if ra is None:
            _warn_unparsable(a)
            return b
        if rb is None:
            _warn_unparsable(b)
            return a
        return a if ra <= rb else b
    return a if a <= b else b
