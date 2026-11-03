"""Direct unit tests for the shared cursor-id helpers (src.cursors)."""

import logging

import pytest

from src import cursors
from src.cursors import _cursor_ts, _is_composite, _max_id, _min_id, _rank


@pytest.fixture(autouse=True)
def _reset_unparsable_warnings():
    cursors._warned_unparsable.clear()
    yield
    cursors._warned_unparsable.clear()


def test_cursor_ts_takes_max_position_regardless_of_array_order():
    eqiad_first = '[{"topic":"eqiad","timestamp":100},{"topic":"codfw","offset":7}]'
    codfw_first = '[{"topic":"codfw","timestamp":7},{"topic":"eqiad","offset":100}]'
    assert _cursor_ts(eqiad_first) == 100
    assert _cursor_ts(codfw_first) == 100


def test_cursor_ts_ignores_non_dict_entries_and_non_integer_fields():
    mixed = '[1,{"timestamp":"x"},{"offset":null},{"offset":3}]'
    assert _cursor_ts(mixed) == 3


def test_cursor_ts_returns_zero_for_unparseable_or_non_list_json():
    assert _cursor_ts("not json") == 0
    assert _cursor_ts("[") == 0
    assert _cursor_ts('"42"') == 0  # valid JSON, not a cursor array


def test_max_id_none_handling():
    assert _max_id(None, "5") == "5"
    assert _max_id("5", None) == "5"
    assert _max_id(None, None) is None


def test_max_id_digit_strings_compare_numerically():
    assert _max_id("9", "10") == "10"  # lexical compare would pick "9"
    assert _max_id("10", "9") == "10"
    assert _max_id("7", "7") == "7"


def test_max_id_composite_cursors_compare_by_position():
    low = '[{"topic":"eqiad","timestamp":100}]'
    high = '[{"topic":"codfw","timestamp":200}]'
    assert _max_id(low, high) == high
    assert _max_id(high, low) == high


def test_max_id_mixed_shapes_fall_back_to_cursor_ts():
    composite = '[{"topic":"eqiad","timestamp":100}]'
    assert _max_id(composite, "5") == composite  # one starts with '['
    assert _max_id("5", composite) == composite


def test_max_id_plain_strings_compare_lexically():
    assert _max_id("abc", "abd") == "abd"


# ---- unparsable ids must never rewind the cursor (id-format-change guard) ----


def test_is_composite_accepts_only_parseable_cursor_arrays():
    assert _is_composite('[{"timestamp": 5}]')
    assert _is_composite('[{"offset": -1}]')
    assert not _is_composite("[not json")  # not JSON
    assert not _is_composite('"42"')  # JSON, not a list
    assert not _is_composite('[1, 2, "x"]')  # list, no integer positions


def test_rank_digit_composite_and_unparsable():
    assert _rank("42") == 42
    assert _rank('[{"offset": -1}]') == 0
    assert _rank("not-an-id") is None


def test_max_id_unparsable_candidate_keeps_existing(caplog):
    existing = '[{"timestamp": 500}]'
    with caplog.at_level(logging.WARNING, logger="wikistream.cursors"):
        assert _max_id(existing, "[{bad") == existing
    assert "cursor_id_unparsable" in caplog.text


def test_max_id_unparsable_existing_keeps_candidate(caplog):
    candidate = '[{"timestamp": 500}]'
    with caplog.at_level(logging.WARNING, logger="wikistream.cursors"):
        assert _max_id("[{bad", candidate) == candidate
    assert "cursor_id_unparsable" in caplog.text


def test_max_id_both_unparsable_keeps_first():
    assert _max_id("[{bad", "[also bad") == "[{bad"


def test_max_id_digit_vs_composite_does_not_rewind():
    # An id-format change (digit-string -> composite) must not drag the cursor
    # from 1000 down to the composite's position 50.
    assert _max_id("1000", '[{"timestamp": 50}]') == "1000"
    assert _max_id('[{"timestamp": 50}]', "1000") == "1000"


def test_max_id_plain_string_vs_composite_keeps_known(caplog):
    composite = '[{"timestamp": 5}]'
    with caplog.at_level(logging.WARNING, logger="wikistream.cursors"):
        assert _max_id(composite, "x") == composite
    assert "cursor_id_unparsable" in caplog.text


def test_min_id_unparsable_hold_is_ignored(caplog):
    composite = '[{"timestamp": 5}]'
    with caplog.at_level(logging.WARNING, logger="wikistream.cursors"):
        assert _min_id(composite, "[{bad") == composite
        assert _min_id("[{bad", composite) == composite
    assert "cursor_id_unparsable" in caplog.text


def test_min_id_both_unparsable_keeps_first():
    assert _min_id("[{bad", "[also bad") == "[{bad"


def test_unparsable_warning_logged_once_per_kind(caplog):
    with caplog.at_level(logging.WARNING, logger="wikistream.cursors"):
        _max_id("1000", "[{bad")  # composite kind
        _max_id('[{"timestamp": 1}]', "x-str")  # plain kind
        _max_id("1000", "[{other")  # duplicate composite: suppressed
    assert cursors._warned_unparsable == {"composite", "plain"}
    assert caplog.text.count("cursor_id_unparsable") == 2
