"""Direct unit tests for the shared cursor-id helpers (src.cursors)."""

from src.cursors import _cursor_ts, _max_id


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
