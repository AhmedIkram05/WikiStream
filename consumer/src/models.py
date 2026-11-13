"""Pydantic models + timestamp validation for Wikimedia recentchange events (plan §4A).

The consumer validates each raw JSON event against WikiEvent before it is
durable; validation failures are dead-lettered (default.dead_letter) with a
machine-readable reason instead of silently dropped.
"""

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

_FUTURE_TOLERANCE = timedelta(minutes=5)

# No real recentchange predates 2001; CH's parseDateTime64BestEffort cannot
# parse negative/very-small epoch digit-strings ("5" yes, "-5" no), and one
# unparseable value errors the MATERIALIZED column for the WHOLE insert
# block — so anything older is dead-lettered, never inserted.
_MIN_TIMESTAMP = datetime(2001, 1, 1, tzinfo=timezone.utc)


class EventLength(BaseModel):
    new: int = 0
    old: int = 0


class WikiEvent(BaseModel):
    """A Wikimedia recentchange event (the subset the pipeline reasons about).

    The JSON field is named 'type' (e.g. "edit" | "new" | "log"); the model
    field is event_type, mapped via validation_alias so model_validate()
    reads the wire key directly.
    """

    model_config = ConfigDict(extra="ignore")

    wiki: str
    title: str
    user: str
    event_type: str = Field(validation_alias="type")
    bot: bool
    length: EventLength | None = None
    timestamp: datetime  # fromisoformat (str) or epoch seconds (int/float)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_epoch(cls, value: object) -> object:
        # Real recentchange events carry "timestamp" as INTEGER epoch seconds
        # (e.g. 1786626323), not an ISO string; convert to datetime here so
        # pydantic's datetime parsing only ever sees str (or junk it rejects).
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        return value


def parse_timestamp(
    ts: str | int | float | None, now: datetime | None = None
) -> tuple[datetime | None, str | None]:
    """Parse and validate an event timestamp; returns (datetime, reason).

    reason is None on success; otherwise one of the exact dead-letter reasons
    ("timestamp_missing" | "timestamp_unparseable" | "timestamp_too_old" |
    "timestamp_future") and the datetime is None. The consumer hands the parsed
    datetime to Pydantic, so the wire timestamp is decoded once, not twice.

    Accepts ISO-8601 strings (fromisoformat; py3.13 handles trailing "Z") and
    integer/float epoch seconds (the real wire shape). now defaults to
    datetime.now(timezone.utc).
    """
    if ts is None or (isinstance(ts, str) and ts.strip() == ""):
        return None, "timestamp_missing"
    # bool is an int subclass: JSON true/false must NOT parse as epoch 1/0.
    if isinstance(ts, bool):
        return None, "timestamp_unparseable"
    if isinstance(ts, (int, float)):
        # fromtimestamp raises on NaN/Inf and overflows the time_t range;
        # json.loads accepts NaN/Infinity, so this must be total or a single
        # bad event wedges the consumer in a reconnect livelock.
        try:
            parsed = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, ValueError, OSError):
            return None, "timestamp_unparseable"
    elif isinstance(ts, str):
        try:
            parsed = datetime.fromisoformat(ts)
        except ValueError:
            return None, "timestamp_unparseable"
    else:
        return None, "timestamp_unparseable"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed < _MIN_TIMESTAMP:
        return None, "timestamp_too_old"
    if (
        parsed
        > (now if now is not None else datetime.now(timezone.utc)) + _FUTURE_TOLERANCE
    ):
        return None, "timestamp_future"
    return parsed, None


def validate_timestamp(
    ts: str | int | float | None, now: datetime | None = None
) -> str | None:
    """Validate an event timestamp; returns a dead-letter reason or None.

    Thin wrapper over parse_timestamp (same exact reasons) for callers that do
    not need the parsed datetime.
    """
    return parse_timestamp(ts, now)[1]
