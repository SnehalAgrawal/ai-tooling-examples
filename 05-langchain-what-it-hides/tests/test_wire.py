"""The stub and the proxy, and LangChain's wire behaviour against them (no model)."""

import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_ollama import ChatOllama

import fleet
from common import plain_chat
from wire import LoggingProxy, StubOllama, chat_lines, chat_single


@pytest.fixture
def stub():
    with StubOllama() as s:
        yield s


@pytest.fixture
def proxy(stub):
    with LoggingProxy(stub.url) as p:
        yield p


def test_chat_lines_join_to_the_content_and_end_with_stats():
    lines = chat_lines("one two three four five", chunks=2, eval_count=5)
    assert "".join(x["message"]["content"] for x in lines) == "one two three four five"
    assert lines[-1]["done"] is True and lines[-1]["eval_count"] == 5
    assert all(x["done"] is False for x in lines[:-1])
    assert chat_single(lines)["message"]["content"] == "one two three four five"


def test_stub_streams_unless_told_not_to(stub):
    with httpx.Client() as c:
        streamed = c.post(f"{stub.url}/api/chat", json={"model": "m", "messages": []})
        single = c.post(f"{stub.url}/api/chat", json={"model": "m", "messages": [], "stream": False})
    assert len(streamed.text.splitlines()) > 1
    assert single.json()["done"] is True
    assert stub.requests == 2


def test_proxy_records_request_and_every_response_line(stub, proxy, tmp_path):
    proxy.log_path = tmp_path / "wire.jsonl"
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    with httpx.Client() as c:
        reply = plain_chat(c, proxy.url, body)
    [ex] = proxy.take()
    assert ex.request_body == body
    assert ex.status == 200
    assert len(ex.response_lines) == len(chat_lines())
    assert reply["message"]["content"] == "".join(x["message"]["content"] for x in ex.response_lines)
    assert json.loads((tmp_path / "wire.jsonl").read_text())["request_body"] == body


def last_body(proxy):
    return [x for x in proxy.take() if x.path == "/api/chat"][-1].request_body


# The findings below are pinned as tests: if a LangChain or ollama release changes them, these fail.


def test_invoke_streams_on_the_wire_and_sends_empty_options_and_tools(proxy):
    ChatOllama(model="m", base_url=proxy.url).invoke("hi")
    body = last_body(proxy)
    assert body["stream"] is True
    assert body["options"] == {} and body["tools"] == []
    assert "keep_alive" not in body and "think" not in body and "format" not in body


def test_call_time_options_replace_the_constructor_options(proxy):
    llm = ChatOllama(model="m", base_url=proxy.url, temperature=0, seed=42, num_ctx=8192)
    llm.invoke("hi", options={"num_predict": 64})
    assert last_body(proxy)["options"] == {"num_predict": 64}


def test_unknown_constructor_arguments_are_ignored(proxy):
    ChatOllama(model="m", base_url=proxy.url, max_tokens=64, max_retries=2, timeout=5).invoke("hi")
    assert last_body(proxy)["options"] == {}


def test_per_call_sampling_kwarg_raises_before_sending(proxy):
    with pytest.raises(TypeError, match="temperature"):
        ChatOllama(model="m", base_url=proxy.url).invoke("hi", temperature=0)
    assert proxy.take() == []


def test_tool_choice_and_tool_call_ids_do_not_reach_the_wire(proxy):
    llm = ChatOllama(model="m", base_url=proxy.url).bind_tools(
        [fleet.get_package_status], tool_choice="get_package_status"
    )
    history = [
        HumanMessage("status?"),
        AIMessage(content="", tool_calls=[{"id": "call_1", "name": "get_package_status", "args": {"package_id": "P"}}]),
        ToolMessage(content="delayed", tool_call_id="call_1"),
    ]
    llm.invoke(history)
    body = last_body(proxy)
    assert "tool_choice" not in body
    assert "id" not in body["messages"][1]["tool_calls"][0]
    assert body["messages"][2] == {"role": "tool", "content": "delayed"}


def test_optional_field_loses_its_type_in_a_tool_schema(proxy):
    # The stub replies with prose, no tool call: function_calling then returns None (tested below).
    ChatOllama(model="m", base_url=proxy.url).with_structured_output(
        fleet.DeliveryException, method="function_calling"
    ).invoke("x")
    props = last_body(proxy)["tools"][0]["function"]["parameters"]["properties"]
    assert set(props["delay_hours"]) == {"description"}
    assert props["reason"]["enum"][0] == "vehicle_breakdown"


def test_default_structured_method_sends_the_json_schema_as_format(stub, proxy):
    stub.content = json.dumps({"package_id": "P", "city": "C", "reason": "weather"})
    ChatOllama(model="m", base_url=proxy.url).with_structured_output(fleet.DeliveryException).invoke("x")
    assert last_body(proxy)["format"] == fleet.DeliveryException.model_json_schema()


def test_function_calling_without_a_tool_call_returns_none_silently(stub):
    stub.content = json.dumps({"package_id": "P", "city": "C", "reason": "weather"})
    llm = ChatOllama(model="m", base_url=stub.url)
    assert llm.with_structured_output(fleet.DeliveryException, method="function_calling").invoke("x") is None
    assert stub.requests == 1


def test_no_retry_by_default(stub):
    stub.reset("http500")
    llm = ChatOllama(model="m", base_url=stub.url)
    with pytest.raises(Exception, match="500"):
        llm.invoke("x")
    assert stub.requests == 1
