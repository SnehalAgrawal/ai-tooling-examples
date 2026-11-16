"""Pure logic: the request-body diff, the grader, and the percentile."""

from analysis import MISSING, grade, json_diff, percentile, schema_valid
from fleet import CASES, DeliveryException, Reason


def test_identical_bodies_have_no_diff():
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "options": {"temperature": 0}}
    assert json_diff(body, body) == []


def test_added_removed_and_changed_keys():
    a = {"stream": True, "tools": [], "model": "m"}
    b = {"stream": False, "model": "m", "format": "json"}
    diff = {d["path"]: d for d in json_diff(a, b)}
    assert diff["stream"]["kind"] == "changed"
    assert diff["tools"] == {"path": "tools", "kind": "only_in_a", "a": [], "b": MISSING}
    assert diff["format"]["kind"] == "only_in_b"
    assert "model" not in diff


def test_nested_paths_and_list_items():
    a = {"messages": [{"role": "user"}, {"role": "tool", "content": "x"}]}
    b = {"messages": [{"role": "user"}, {"role": "tool", "content": "x", "tool_name": "t"}, {"role": "user"}]}
    paths = {d["path"]: d["kind"] for d in json_diff(a, b)}
    assert paths == {"messages[1].tool_name": "only_in_b", "messages[2]": "only_in_b"}


def test_numbers_compare_by_value_but_bools_do_not():
    assert json_diff({"t": 0.0}, {"t": 0}) == []
    assert json_diff({"s": True}, {"s": 1})[0]["kind"] == "changed"
    assert json_diff({"s": "1"}, {"s": 1})[0]["kind"] == "changed"


def test_grade_all_correct_with_normalisation():
    expected = {"package_id": "PKG-14555", "city": "York", "reason": "customs_hold", "delay_hours": 12}
    parsed = DeliveryException(package_id=" pkg-14555", city="york", reason=Reason.customs_hold, delay_hours=12)
    g = grade(parsed, expected)
    assert g["all_correct"] and g["correct"] == 4


def test_grade_field_by_field():
    expected = {"package_id": "PKG-1", "city": "Leeds", "reason": "weather", "delay_hours": None}
    g = grade({"package_id": "PKG-1", "city": "Hull", "reason": "weather", "delay_hours": 0}, expected)
    assert g["fields"] == {"package_id": True, "city": False, "reason": True, "delay_hours": False}
    assert g["correct"] == 2 and not g["all_correct"]


def test_grade_nothing_parsed_and_numeric_strings():
    expected = {"package_id": "PKG-1", "delay_hours": 36}
    assert grade(None, expected)["correct"] == 0
    assert grade({"package_id": "PKG-1", "delay_hours": 36.0}, expected)["all_correct"]
    assert grade({"package_id": "PKG-1", "delay_hours": "36"}, expected)["all_correct"]
    assert not grade({"package_id": "PKG-1", "delay_hours": 36.5}, expected)["all_correct"]


def test_schema_valid():
    ok, why = schema_valid(DeliveryException, {"package_id": "P", "city": "C", "reason": "weather"})
    assert ok and why is None
    ok, why = schema_valid(DeliveryException, {"package_id": "P", "city": "C", "reason": "flat_tyre"})
    assert not ok and why.startswith("reason")
    assert schema_valid(DeliveryException, None) == (False, "nothing parsed")


def test_every_expected_value_is_schema_valid():
    for _cid, _text, expected in CASES:
        assert schema_valid(DeliveryException, expected)[0]


def test_percentile_matches_linear_interpolation():
    values = list(range(1, 101))
    assert percentile(values, 50) == 50.5
    assert abs(percentile(values, 95) - 95.05) < 1e-9
    assert percentile([3.0], 95) == 3.0
