"""Comparing replies: identical or not, and where they first differ."""


def first_difference(a: str, b: str) -> int | None:
    """Index of the first character where a and b differ, or None if they are identical."""
    if a == b:
        return None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def word_index(text: str, char_index: int) -> int:
    """The number of words that start at or before char_index (from 1), for reporting "differs at word N"."""
    return max(1, len(text[: char_index + 1].split()))


def summarise(reference: dict[str, str], replies: dict[str, str]) -> dict:
    """Compare replies to reference replies, keyed by request id."""
    differences = {}
    for key, text in replies.items():
        index = first_difference(reference[key], text)
        if index is not None:
            differences[key] = {"char": index, "word": word_index(reference[key], index)}
    return {"identical": len(replies) - len(differences), "total": len(replies), "differences": differences}
