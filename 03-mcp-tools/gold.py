"""The evaluation questions, their gold answers, and the structural grader.

Gold answers are computed with SQL written here, independently of the server's tool code, so a bug in a
tool shows up as a disagreement instead of being copied into the answer key.

Grading, per question:
- tool_ok:      every expected call was made at some point (tool name equal, arguments equal as objects;
                an argument sent as null counts as omitted). For a question that needs no tool: no call was made.
- first_try_ok: tool_ok, and the first call(s) were already the right ones (no failed attempt first).
- answer_ok:    the final answer contains every expected value (numbers as whole numbers, dates in ISO or
                "October 26" form, statuses with or without the underscore). "Contains", not "is correct".
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from fleet_data import DB_PATH, connect_readonly

ArgCheck = Callable[[dict[str, Any]], bool]


@dataclass
class ExpectedCall:
    name: str
    args: dict[str, Any] | None = None  # exact arguments (None values dropped before comparing)
    check: ArgCheck | None = None  # or a predicate, for questions where several arguments are right
    check_doc: str = ""  # what the predicate accepts, for the report


@dataclass
class Question:
    qid: str
    category: str  # single | count | exceptions | device | no_tool | two_tools
    text: str
    calls: list[ExpectedCall]
    gold: Callable[[sqlite3.Connection], list[str]]  # the values the answer must contain
    notes: str = ""
    values: list[str] = field(default_factory=list)  # filled by compute_gold()


def _one(con: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    return con.execute(sql, params).fetchone()[0]


def _pkg(column: str, package_id: str) -> Callable[[sqlite3.Connection], list[str]]:
    return lambda con: [str(_one(con, f"SELECT {column} FROM packages WHERE package_id = ?", (package_id,)))]


def _count(where: str = "", params: tuple = ()) -> Callable[[sqlite3.Connection], list[str]]:
    sql = "SELECT COUNT(*) FROM packages" + (f" WHERE {where}" if where else "")
    return lambda con: [str(_one(con, sql, params))]


def _date_on_or_before(limit: str) -> ArgCheck:
    def check(args: dict[str, Any]) -> bool:
        try:
            return date.fromisoformat(str(args.get("since_date"))) <= date.fromisoformat(limit)
        except ValueError:
            return False
    return check


QUESTIONS: list[Question] = [
    # One package: get_package_status
    Question("q01", "single", "What is the status of package PKG-00015?",
             [ExpectedCall("get_package_status", {"package_id": "PKG-00015"})], _pkg("status", "PKG-00015")),
    Question("q02", "single", "Which city is package PKG-00123 going to?",
             [ExpectedCall("get_package_status", {"package_id": "PKG-00123"})], _pkg("destination_city", "PKG-00123")),
    Question("q03", "single", "When was package PKG-00305 delivered?",
             [ExpectedCall("get_package_status", {"package_id": "PKG-00305"})],
             lambda con: [_one(con, "SELECT delivered_at FROM packages WHERE package_id = 'PKG-00305'")[:10]]),
    Question("q04", "single", "Which company is handling package PKG-00088?",
             [ExpectedCall("get_package_status", {"package_id": "PKG-00088"})],
             lambda con: [_one(con, "SELECT c.name FROM packages p JOIN companies c USING (company_id) "
                                    "WHERE package_id = 'PKG-00088'")]),
    Question("q05", "single", "Has package PKG-00005 been delivered yet? What is its status?",
             [ExpectedCall("get_package_status", {"package_id": "PKG-00005"})], _pkg("status", "PKG-00005")),
    # Counts: count_packages
    Question("q06", "count", "How many packages were sent from Berlin to Munich?",
             [ExpectedCall("count_packages", {"origin_city": "Berlin", "destination_city": "Munich"})],
             _count("origin_city = ? AND destination_city = ?", ("Berlin", "Munich"))),
    Question("q07", "count", "How many packages are in transit right now?",
             [ExpectedCall("count_packages", {"status": "in_transit"})], _count("status = ?", ("in_transit",))),
    Question("q08", "count", "How many packages sent from Hamburg have been delivered?",
             [ExpectedCall("count_packages", {"origin_city": "Hamburg", "status": "delivered"})],
             _count("origin_city = ? AND status = ?", ("Hamburg", "delivered"))),
    Question("q09", "count", "How many packages are going to Leipzig?",
             [ExpectedCall("count_packages", {"destination_city": "Leipzig"})],
             _count("destination_city = ?", ("Leipzig",))),
    Question("q10", "count", "How many packages are there in total?",
             [ExpectedCall("count_packages", {})], _count()),
    Question("q11", "count", "How many packages bound for Cologne have a delivery exception?",
             [ExpectedCall("count_packages", {"destination_city": "Cologne", "status": "exception"})],
             _count("destination_city = ? AND status = ?", ("Cologne", "exception"))),
    # Exceptions: list_exceptions
    Question("q12", "exceptions", "How many delivery exceptions have been recorded since 2026-10-25?",
             [ExpectedCall("list_exceptions", check=lambda a: a.get("since_date") == "2026-10-25",
                           check_doc="since_date = 2026-10-25, any limit")],
             lambda con: [str(_one(con, "SELECT COUNT(*) FROM events WHERE kind = 'exception' "
                                        "AND ts >= '2026-10-25T00:00:00Z'"))]),
    Question("q13", "exceptions", "Which packages had a delivery exception on or after 2026-10-28?",
             [ExpectedCall("list_exceptions", check=lambda a: a.get("since_date") == "2026-10-28",
                           check_doc="since_date = 2026-10-28, any limit")],
             lambda con: [r[0] for r in con.execute("SELECT package_id FROM events WHERE kind = 'exception' "
                                                    "AND ts >= '2026-10-28T00:00:00Z' ORDER BY ts")]),
    Question("q14", "exceptions", "Which package had the most recent delivery exception? The data covers October 2026.",
             [ExpectedCall("list_exceptions", check=_date_on_or_before("2026-10-30"),
                           check_doc="any since_date on or before 2026-10-30")],
             lambda con: [_one(con, "SELECT package_id FROM events WHERE kind = 'exception' "
                                    "ORDER BY ts DESC, event_id DESC LIMIT 1")]),
    # Devices: get_device
    Question("q15", "device", "What kind of vehicle is device DEV-007?",
             [ExpectedCall("get_device", {"device_id": "DEV-007"})],
             lambda con: [_one(con, "SELECT kind FROM devices WHERE device_id = 'DEV-007'")]),
    Question("q16", "device", "How many undelivered packages does device DEV-012 hold?",
             [ExpectedCall("get_device", {"device_id": "DEV-012"})],
             lambda con: [str(_one(con, "SELECT COUNT(*) FROM packages WHERE device_id = 'DEV-012' "
                                        "AND status != 'delivered'"))]),
    # No tool needed
    Question("q17", "no_tool", "Hi! In one sentence, what kinds of questions can you help me with?", [],
             lambda con: [], notes="Graded on tool use only: no call expected."),
    Question("q18", "no_tool", "A van drives 180 km at 60 km/h. How many hours does the trip take?", [],
             lambda con: ["3"]),
    # Two tools
    Question("q19", "two_tools", "Are more packages going to Munich or to Cologne? Give both numbers.",
             [ExpectedCall("count_packages", {"destination_city": "Munich"}),
              ExpectedCall("count_packages", {"destination_city": "Cologne"})],
             lambda con: [str(_one(con, "SELECT COUNT(*) FROM packages WHERE destination_city = 'Munich'")),
                          str(_one(con, "SELECT COUNT(*) FROM packages WHERE destination_city = 'Cologne'"))]),
    Question("q20", "two_tools", "Package PKG-00012: which device is carrying it, and what kind of vehicle is that device?",
             [ExpectedCall("get_package_status", {"package_id": "PKG-00012"}),
              ExpectedCall("get_device", check=lambda a: a.get("device_id") == "DEV-022",
                           check_doc="device_id = the package's device")],
             lambda con: [_one(con, "SELECT device_id FROM packages WHERE package_id = 'PKG-00012'"),
                          _one(con, "SELECT d.kind FROM packages p JOIN devices d USING (device_id) "
                                    "WHERE package_id = 'PKG-00012'")],
             notes="The second call depends on the first call's result."),
]


def compute_gold(db_path: Path = DB_PATH, questions: list[Question] = QUESTIONS) -> list[Question]:
    con = connect_readonly(db_path)
    try:
        for q in questions:
            q.values = q.gold(con)
    finally:
        con.close()
    return questions


# --------------------------------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------------------------------

def drop_nulls(args: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in args.items() if v is not None}


def call_matches(expected: ExpectedCall, name: str, args: dict[str, Any]) -> bool:
    if name != expected.name:
        return False
    args = drop_nulls(args)
    if expected.check is not None:
        return expected.check(args)
    return args == drop_nulls(expected.args or {})


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]


def value_variants(value: str) -> list[str]:
    """Spellings of a gold value that count as present in an answer."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        d = date.fromisoformat(value)
        return [value, f"{_MONTHS[d.month - 1]} {d.day}", f"{d.day} {_MONTHS[d.month - 1]}"]
    if "_" in value:
        return [value, value.replace("_", " "), value.replace("_", "-")]
    return [value]


def contains_value(answer: str, value: str) -> bool:
    for variant in value_variants(value):
        if variant.isdigit():
            # a whole number, not part of a longer one or of an ID such as PKG-00042
            if re.search(rf"(?<![\d-]){variant}(?![\d])", answer):
                return True
        elif variant.lower() in answer.lower():
            return True
    return False


def grade(question: Question, calls: list[dict[str, Any]], answer: str) -> dict[str, Any]:
    """Grade one run. `calls` are the host's call records (name, arguments, outcome), in order."""
    made = [(c["name"], c.get("arguments") or {}) for c in calls]
    if not question.calls:
        tool_ok = not made
        first_try_ok = tool_ok
    else:
        tool_ok = all(any(call_matches(e, n, a) for n, a in made) for e in question.calls)
        first = made[: len(question.calls)]
        first_try_ok = tool_ok and len(first) == len(question.calls) and all(
            any(call_matches(e, n, a) for n, a in first) for e in question.calls)
    expected_names = {e.name for e in question.calls}
    missing = [v for v in question.values if not contains_value(answer, v)]
    return {
        "tool_ok": tool_ok,
        "first_try_ok": first_try_ok,
        "answer_ok": not missing,
        "missing_values": missing,
        "n_calls": len(made),
        "unexpected_tools": sorted({n for n, _ in made} - expected_names),
    }
