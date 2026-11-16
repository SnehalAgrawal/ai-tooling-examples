"""The deterministic check used in the experiment: is each expected fact present in the answer?

An expected fact is a list of acceptable spellings (e.g. ["30 minutes", "30-minute", "30 min"]).
A fact is present when any spelling appears in the normalized answer. This checks that a key fact is
stated; it does not check that the answer is correct, complete, or free of wrong statements.
"""

import re


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)  # 1,200 -> 1200
    text = text.replace("‑", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def fact_present(answer: str, spellings: list[str]) -> bool:
    a = normalize(answer)
    return any(normalize(s) in a for s in spellings)


def facts_present(answer: str, expected_facts: list[list[str]]) -> list[bool]:
    return [fact_present(answer, f) for f in expected_facts]
