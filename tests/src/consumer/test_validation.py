"""WikiEvent model + timestamp validation tests (plan §4A)."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.models import WikiEvent, validate_timestamp

PAST_TS = "2020-01-01T00:00:00Z"


def make_payload(**overrides):
    """Realistic captured Wikimedia recentchange payload (past timestamp)."""
    payload = {
        "$schema": "/mediawiki/recentchange/1.0.0",
        "id": 1234567890,
        "type": "edit",
        "namespace": 0,
        "title": "Example page",
        "comment": "test edit",
        "timestamp": PAST_TS,
        "user": "TestUser",
        "bot": False,
        "minor": False,
        "length": {"new": 100, "old": 50},
        "wiki": "enwiki",
        "server_url": "https://en.wikipedia.org",
    }
    payload.update(overrides)
    return payload


def test_good_event_parses():
    event = WikiEvent.model_validate(make_payload())
    assert event.wiki == "enwiki"
    assert event.title == "Example page"
    assert event.user == "TestUser"
    assert event.event_type == "edit"
    assert event.bot is False
    assert event.length.new == 100
    assert event.length.old == 50
    assert event.timestamp == datetime(2020, 1, 1, tzinfo=timezone.utc)


def test_missing_required_field():
    payload = make_payload()
    del payload["title"]  # drop the key: None would be a type error, not missing
    with pytest.raises(ValidationError) as exc:
        WikiEvent.model_validate(payload)
    assert exc.value.errors()[0]["type"] == "missing"


def test_wrong_type():
    with pytest.raises(ValidationError) as exc:
        WikiEvent.model_validate(make_payload(title=123))
    assert exc.value.errors()[0]["type"] in {"string_type", "int_parsing"}


def test_timestamp_unparseable():
    assert validate_timestamp("garbage") == "timestamp_unparseable"


def test_timestamp_missing():
    assert validate_timestamp(None) == "timestamp_missing"
    assert validate_timestamp("") == "timestamp_missing"


def test_timestamp_future():
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    assert validate_timestamp(future) == "timestamp_future"
    now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    assert validate_timestamp("2026-08-13T10:00:00Z", now=now) is None
    assert validate_timestamp("2026-08-13T10:04:00Z", now=now) is None
    assert validate_timestamp("2026-08-13T10:06:00Z", now=now) == "timestamp_future"


def test_timestamp_valid_past():
    assert validate_timestamp(PAST_TS) is None


def test_extra_fields_ignored():
    event = WikiEvent.model_validate(make_payload(unknown_field={"nested": 1}))
    assert event.title == "Example page"


@pytest.mark.parametrize("bot_value", [True, False, 1, 0, "true", "false", "yes", "no"])
def test_bot_coercion_matrix(bot_value):
    # Empirical finding (pydantic 2.13.4, lax mode): ALL of these coerce to
    # bool — true/false/yes/no/on/off/1/0/t/f/y/n, case-insensitive. None are
    # rejected, so every parametrized case asserts successful coercion.
    event = WikiEvent.model_validate(make_payload(bot=bot_value))
    assert isinstance(event.bot, bool)


def test_bad_timestamp_in_model():
    with pytest.raises(ValidationError):
        WikiEvent.model_validate(make_payload(timestamp="not-a-date"))


# --- real wire shape (captured 2026-08-13 from stream.wikimedia.org) ---
# The live stream sends "timestamp" as INTEGER epoch seconds and omits
# "length"/"minor" for categorize events. Verified empirically — the original
# ISO-string-only model dead-lettered every real event (validate_timestamp
# called .strip() on an int) and pydantic rejected int timestamps.


def test_real_captured_categorize_event_parses():
    """Exact captured frame: ru.wikipedia categorize, no length/minor."""
    payload = {
        "$schema": "/mediawiki/recentchange/1.0.0",
        "meta": {
            "uri": "https://ru.wikipedia.org/wiki/x",
            "id": "b587a960-7fa3-4806-a3f5-e08b5b34eb7a",
        },
        "id": 540724824,
        "type": "categorize",
        "namespace": 14,
        "title": "Категория:Другие значения",
        "title_url": "https://ru.wikipedia.org/wiki/x",
        "comment": "[[:Гиндза (станция)]] добавлена в категорию",
        "timestamp": 1786626373,  # int epoch seconds (real wire shape)
        "user": "Mocmuk",
        "bot": False,
        "notify_url": "https://ru.wikipedia.org/w/index.php?diff=1",
        "server_url": "https://ru.wikipedia.org",
        "server_name": "ru.wikipedia.org",
        "server_script_path": "/w",
        "wiki": "ruwiki",
        "parsedcomment": '<a href="/wiki/x">x</a>',
    }
    event = WikiEvent.model_validate(payload)
    assert event.wiki == "ruwiki"
    assert event.title == "Категория:Другие значения"
    assert event.user == "Mocmuk"
    assert event.event_type == "categorize"
    assert event.bot is False
    assert event.length is None  # absent for categorize events
    assert event.timestamp == datetime.fromtimestamp(1786626373, tz=timezone.utc)


def test_real_captured_edit_event_parses():
    """Exact captured frame: hy.wikipedia edit with length present."""
    payload = {
        "$schema": "/mediawiki/recentchange/1.0.0",
        "id": 86142319,
        "type": "edit",
        "namespace": 0,
        "title": "Աբխազիայի և Հարավային Օսիայի միջազգային ճանաչում",
        "comment": "",
        "timestamp": 1786626375,
        "user": "Iktsokh",
        "bot": False,
        "minor": False,
        "length": {"old": 10980, "new": 9584},
        "server_url": "https://hy.wikipedia.org",
        "server_name": "hy.wikipedia.org",
        "server_script_path": "/w",
        "wiki": "hywiki",
    }
    event = WikiEvent.model_validate(payload)
    assert event.event_type == "edit"
    assert event.length.new == 9584
    assert event.length.old == 10980
    assert event.bot is False


def test_timestamp_epoch_int():
    assert validate_timestamp(1786626373) is None
    assert validate_timestamp(0.0) is None  # 1970-01-01, valid past
    assert validate_timestamp(99999999999) == "timestamp_future"  # 5138-04-24
    assert validate_timestamp(["garbage"]) == "timestamp_unparseable"


def test_timestamp_total_no_escape():
    """validate_timestamp must NEVER raise (review B1): json.loads accepts
    NaN/Infinity by default, and fromtimestamp overflows on huge values —
    an exception here would tear down the live stream and livelock reconnect."""
    assert validate_timestamp(float("nan")) == "timestamp_unparseable"
    assert validate_timestamp(float("inf")) == "timestamp_unparseable"
    assert validate_timestamp(float("-inf")) == "timestamp_unparseable"
    assert validate_timestamp(10**30) == "timestamp_unparseable"
    assert validate_timestamp(1e300) == "timestamp_unparseable"
    assert validate_timestamp(10**18) == "timestamp_unparseable"


def test_timestamp_bool_rejected():
    """Review N3: bool is an int subclass — true/false must not parse as
    epoch 1/0 (1970-01-01 00:00:01)."""
    assert validate_timestamp(True) == "timestamp_unparseable"
    assert validate_timestamp(False) == "timestamp_unparseable"


def test_model_timestamp_epoch_int():
    event = WikiEvent.model_validate(make_payload(timestamp=1786626373))
    assert event.timestamp == datetime.fromtimestamp(1786626373, tz=timezone.utc)


def test_timestamp_naive_string_utc():
    """Naive ISO string has no tzinfo — must be coerced to UTC, not rejected
    (Phase 6 gap: models.py naive-datetime .replace path was uncovered)."""
    assert validate_timestamp("2026-08-13T12:00:00") is None


def test_timestamp_junk_types_unparseable():
    """Non-str/int/float types fall through to the final else branch."""
    assert validate_timestamp({}) == "timestamp_unparseable"
    assert validate_timestamp([]) == "timestamp_unparseable"
    assert validate_timestamp(datetime(2026, 8, 13)) == "timestamp_unparseable"
