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


def _cursor_ts(s: str) -> int:
    """Highest partition position in a Kafka composite cursor id.

    The id is a JSON array of per-partition cursors like
    `[{"topic":"eqiad...","timestamp":1786...},{"topic":"codfw...","offset":-1}]`.
    The ARRAY ORDER varies between events (eqiad-first or codfw-first), so a
    raw string compare is meaningless — compare the max numeric position.
    """
    try:
        cursors = json.loads(s)
    except (TypeError, ValueError):
        return 0
    best = 0
    if isinstance(cursors, list):
        for c in cursors:
            if isinstance(c, dict):
                for key in ("timestamp", "offset"):
                    v = c.get(key)
                    if isinstance(v, int) and v > best:
                        best = v
    return best


def _is_composite(s: str) -> bool:
    """True when s is a parseable composite cursor array (a JSON list carrying
    at least one integer timestamp/offset position)."""
    try:
        cursors = json.loads(s)
    except (TypeError, ValueError):
        return False
    if not isinstance(cursors, list):
        return False
    return any(
        isinstance(c, dict)
        and any(isinstance(c.get(k), int) for k in ("timestamp", "offset"))
        for c in cursors
    )


def _rank(s: str) -> int | None:
    """Numeric rank for a well-formed id, else None (unparsable shape).

    Well-formed = a Wikimedia digit-string (its value) or a parseable composite
    cursor (its highest partition position). A None rank must never win a
    comparison: treating an unknown id as the oldest value would silently rewind
    the cursor into mass reprocessing on an upstream id-format change.
    """
    if s.isdigit():
        return int(s)
    if _is_composite(s):
        return _cursor_ts(s)
    return None


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
        return a if int(a) >= int(b) else b
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


def _min_id(a: str | None, b: str | None) -> str | None:
    """Smaller of two ids, mirroring _max_id. None means "no id known", i.e.
    −∞, so any pair containing a None collapses to None. Used to clamp a
    cursor advance at an unlanded dead-letter row's watermark: a None hold
    means nothing was durable yet, so no advance is allowed."""
    if a is None or b is None:
        return None
    if a.isdigit() and b.isdigit():
        return a if int(a) <= int(b) else b
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
