"""Tests for the pure logic: no model, no Langfuse server."""

import json
from pathlib import Path

import pytest

from fleet.app import answer_plain, build_messages
from fleet.evaluate import fact_present, facts_present, normalize
from fleet.llm import FakeChat
from fleet.masking import EMAIL_TOKEN, PHONE_TOKEN, redact, redact_text
from fleet.retrieval import retrieve, tokenize
from fleet.stats import percentile, summarize

QUESTIONS = json.loads((Path(__file__).resolve().parent.parent / "data" / "questions.json").read_text())


@pytest.mark.parametrize("q", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
def test_retrieval_returns_the_source_snippet(q):
    ids = [s["id"] for s in retrieve(q["question"])]
    if q["source"] is not None:
        assert q["source"] in ids


def test_tokenize_drops_stopwords_and_plural_s():
    assert tokenize("What are the vans' speed limits?") == ["van", "speed", "limit"]


def test_retrieve_never_returns_zero_score_snippets():
    assert retrieve("xyzzy plugh") == []


def test_normalize_and_fact_present():
    assert normalize("The payload is 1,200  KG.") == "the payload is 1200 kg."
    assert fact_present("Max payload: 1,200 kg", ["1200 kg"])
    assert not fact_present("Max payload: 1,100 kg", ["1200 kg", "1200kg"])
    assert facts_present("Every 20,000 km or 6 months", [["20000 km"], ["6 months", "six months"]]) == [True, True]


def test_redact_email_and_phone():
    text = "Call +44 20 7946 0958 or write to jane.doe@example.com about van 4410."
    out = redact_text(text)
    assert EMAIL_TOKEN in out and PHONE_TOKEN in out
    assert "jane.doe@example.com" not in out and "7946" not in out
    assert "4410" in out  # an extension number is not a phone number


def test_redact_leaves_policy_numbers_alone():
    text = "Service every 20,000 km; payload 1,200 kg; break after 4.5 hours; 2 to 8 degrees."
    assert redact_text(text) == text


def test_redact_nested_structures():
    data = {"messages": [{"role": "user", "content": "me@corp.example"}], "n": 3}
    assert redact(data) == {"messages": [{"role": "user", "content": EMAIL_TOKEN}], "n": 3}


def test_percentile_matches_linear_interpolation():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([10], 95) == 10
    assert percentile(list(range(101)), 95) == 95
    s = summarize([1.0, 2.0, 3.0])
    assert s["median"] == 2.0 and s["max"] == 3.0 and s["n"] == 3


def test_fake_model_answers_from_context():
    a = answer_plain("What is the maximum payload of a standard van?", FakeChat())
    assert "1,200 kg" in a.text
    assert a.sources[0] == "pol-loading"
    assert a.input_tokens and a.output_tokens


def test_messages_carry_question_and_context():
    msgs = build_messages("Q?", [{"id": "x", "title": "t", "text": "Body."}])
    assert msgs[0]["role"] == "system"
    assert "Q?" in msgs[1]["content"] and "[x] Body." in msgs[1]["content"]
