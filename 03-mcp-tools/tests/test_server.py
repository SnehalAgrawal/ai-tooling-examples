"""The MCP server, in process (no subprocess, no model): tools agree with the gold answers, validation, read-only."""

import sqlite3

import anyio
import pytest
from mcp import Client

from fleet_data import DB_PATH, INJECTED_DB_PATH, INJECTED_NOTE, connect_readonly
from fleet_server import build_server
from gold import compute_gold
from host import tool_result_text


def _run(coro_fn, *args):
    return anyio.run(coro_fn, *args)


async def _call_all(server, calls):
    async with Client(server) as client:
        return [(await client.call_tool(name, args)).model_dump(mode="json", by_alias=True, exclude_none=True)
                for name, args in calls]


def test_tools_agree_with_gold():
    """For every question with exact expected arguments, the tool's result contains the gold values."""
    questions = [q for q in compute_gold() if q.calls and all(e.args is not None for e in q.calls)]
    calls = [(e.name, e.args) for q in questions for e in q.calls]
    results = iter(_run(_call_all, build_server(), calls))
    for q in questions:
        text = "\n".join(tool_result_text(next(results)) for _ in q.calls)
        for value in q.values:
            assert value in text, (q.qid, value)


def test_exception_tools_agree_with_gold():
    gold = {q.qid: q.values for q in compute_gold()}
    r12, r13, r14 = _run(_call_all, build_server(), [
        ("list_exceptions", {"since_date": "2026-10-25"}),
        ("list_exceptions", {"since_date": "2026-10-28", "limit": 50}),
        ("list_exceptions", {"since_date": "2026-10-01", "limit": 1}),
    ])
    assert r12["structuredContent"]["total"] == int(gold["q12"][0])
    assert {e["package_id"] for e in r13["structuredContent"]["exceptions"]} == set(gold["q13"])
    assert r14["structuredContent"]["exceptions"][0]["package_id"] == gold["q14"][0]


@pytest.mark.parametrize(("name", "args", "fragment"), [
    ("get_package_status", {"package_id": "pkg-42"}, "string_pattern_mismatch"),
    ("get_package_status", {"package_id": "PKG-99999"}, "No package with ID PKG-99999"),
    ("count_packages", {"origin_city": "Paris"}, "Input should be"),
    ("list_exceptions", {"since_date": "2026-02-30"}, "real date"),
    ("list_exceptions", {"since_date": "2026-10-01", "limit": 500}, "less than or equal to 50"),
    ("get_device", {"device_id": "DEV-999"}, "No device with ID DEV-999"),
])
def test_invalid_input_is_a_tool_error(name, args, fragment):
    (result,) = _run(_call_all, build_server(), [(name, args)])
    assert result["isError"] is True
    assert fragment in tool_result_text(result)


def test_descriptions_are_the_only_difference():
    async def listed(style):
        async with Client(build_server(descriptions=style)) as client:
            return [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in (await client.list_tools()).tools]

    def strip(obj):
        if isinstance(obj, dict):
            return {k: strip(v) for k, v in obj.items() if k != "description"}
        if isinstance(obj, list):
            return [strip(v) for v in obj]
        return obj

    descriptive, terse = _run(listed, "descriptive"), _run(listed, "terse")
    assert strip(descriptive) == strip(terse)
    assert descriptive != terse


def test_decoy_only_with_flag_and_changes_nothing(tmp_path):
    async def names(server):
        async with Client(server) as client:
            return [t.name for t in (await client.list_tools()).tools]

    assert "delete_package" not in _run(names, build_server())
    log = tmp_path / "decoy.jsonl"
    (result,) = _run(_call_all, build_server(decoy=True, decoy_log=log), [("delete_package", {"package_id": "PKG-00042"})])
    assert not result.get("isError")
    assert log.read_text().count("PKG-00042") == 1
    con = connect_readonly(DB_PATH)
    assert con.execute("SELECT COUNT(*) FROM packages WHERE package_id = 'PKG-00042'").fetchone()[0] == 1


def test_database_connection_is_read_only():
    con = connect_readonly(DB_PATH)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("DELETE FROM packages")


def test_injected_note_reaches_the_tool_result():
    (result,) = _run(_call_all, build_server(INJECTED_DB_PATH), [("get_package_status", {"package_id": "PKG-00042"})])
    assert result["structuredContent"]["last_event"]["note"] == INJECTED_NOTE


def test_resources_and_prompt():
    async def go():
        async with Client(build_server()) as client:
            uris = [str(r.uri) for r in (await client.list_resources()).resources]
            schema = await client.read_resource("fleet://schema")
            prompt = await client.get_prompt("triage_exceptions", {"since_date": "2026-10-28"})
            return uris, schema, prompt
    uris, schema, prompt = _run(go)
    assert uris == ["fleet://schema", "fleet://runbook/exceptions"]
    assert "CREATE TABLE packages" in schema.contents[0].text
    assert "2026-10-28" in prompt.messages[0].content.text
