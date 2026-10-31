"""Shared event-id comparison helpers (single source for consumer + batcher).

Event ids come in two shapes: Wikimedia digit-strings ("12345") and Kafka
composite cursors (a JSON array of per-partition positions). Both sites that
advance a durable id over these — EventBatcher/DeadLetterBatcher and the
consume loop — must compare them identically, so the logic lives here once
instead of drifting between near-identical copies.
"""

import json


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


def _max_id(a: str | None, b: str | None) -> str | None:
    """Larger of two ids: None-handling; numeric compare for digit-strings;
    composite cursor ids (leading '[') compare by highest partition position."""
    if a is None:
        return b
    if b is None:
        return a
    if a.isdigit() and b.isdigit():
        return a if int(a) >= int(b) else b
    if a.startswith("[") or b.startswith("["):
        return a if _cursor_ts(a) >= _cursor_ts(b) else b
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
        return a if _cursor_ts(a) <= _cursor_ts(b) else b
    return a if a <= b else b
