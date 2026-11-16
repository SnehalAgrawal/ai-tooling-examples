"""The comparison logic (no Ollama needed): python -m unittest discover -s tests"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compare import first_difference, summarise, word_index  # noqa: E402


class CompareTest(unittest.TestCase):
    def test_identical_text_has_no_difference(self):
        self.assertIsNone(first_difference("same text", "same text"))

    def test_first_differing_character(self):
        self.assertEqual(first_difference("The truck left", "The train left"), 6)

    def test_one_text_is_a_prefix_of_the_other(self):
        self.assertEqual(first_difference("abc", "abcdef"), 3)

    def test_word_index_counts_words_up_to_the_difference(self):
        text = "The truck left Pune"
        self.assertEqual(word_index(text, 0), 1)
        self.assertEqual(word_index(text, 4), 2)
        self.assertEqual(word_index(text, 6), 2)
        self.assertEqual(word_index(text, 10), 3)

    def test_summary_counts_identical_replies(self):
        reference = {"p1": "alpha beta", "p2": "gamma delta"}
        summary = summarise(reference, {"p1": "alpha beta", "p2": "gamma omega"})
        self.assertEqual((summary["identical"], summary["total"]), (1, 2))
        self.assertEqual(summary["differences"], {"p2": {"char": 6, "word": 2}})


if __name__ == "__main__":
    unittest.main()
