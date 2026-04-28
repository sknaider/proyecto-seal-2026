"""Contract tests for seal/utils/stream_scrubber.

Run:
    python3 -m pytest seal/utils/tests/test_stream_scrubber.py -v

Or standalone:
    python3 -m unittest seal.utils.tests.test_stream_scrubber -v
"""
from __future__ import annotations

import unittest

from seal.utils.stream_scrubber import StreamScrubber, _longest_prefix_suffix


class LongestPrefixSuffixTests(unittest.TestCase):
    def test_exact_prefix(self) -> None:
        self.assertEqual(_longest_prefix_suffix("hello <seal", "<seal-context>"), "<seal")

    def test_no_match(self) -> None:
        self.assertEqual(_longest_prefix_suffix("hello world", "<seal-context>"), "")

    def test_full_tag_not_returned(self) -> None:
        # Full tag should not be returned (strict prefix only)
        tag = "<seal-context>"
        self.assertEqual(_longest_prefix_suffix(tag, tag), "")

    def test_single_char_match(self) -> None:
        self.assertEqual(_longest_prefix_suffix("abc<", "<seal-context>"), "<")


class StreamScrubberTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = StreamScrubber()

    def _feed_all(self, chunks: list[str]) -> str:
        return "".join(self.s.feed(c) for c in chunks) + self.s.flush()

    def test_passthrough_no_fence(self) -> None:
        result = self._feed_all(["hello ", "world"])
        self.assertEqual(result, "hello world")

    def test_fence_in_single_chunk(self) -> None:
        result = self._feed_all(["before<seal-context>hidden</seal-context>after"])
        self.assertEqual(result, "beforeafter")

    def test_fence_split_across_chunks(self) -> None:
        result = self._feed_all([
            "before<seal-",
            "context>hidden content</seal-",
            "context>after",
        ])
        self.assertEqual(result, "beforeafter")

    def test_partial_open_tag_at_stream_end_emitted(self) -> None:
        # Stream ends mid-open-tag — partial tag is user-visible text
        result = self._feed_all(["text<seal-co"])
        self.assertEqual(result, "text<seal-co")

    def test_unclosed_fence_discarded(self) -> None:
        result = self._feed_all(["before<seal-context>hidden no close"])
        self.assertEqual(result, "before")

    def test_multiple_fences_in_one_chunk(self) -> None:
        result = self._feed_all([
            "a<seal-context>X</seal-context>b<seal-context>Y</seal-context>c"
        ])
        self.assertEqual(result, "abc")

    def test_multiple_fences_across_chunks(self) -> None:
        result = self._feed_all([
            "a<seal-context>X</seal-",
            "context>b<seal-context>Y</seal-context>c",
        ])
        self.assertEqual(result, "abc")

    def test_reset_reuses_instance(self) -> None:
        self.s.feed("before<seal-context>hidden</seal-context>after")
        self.s.flush()
        self.s.reset()
        result = self._feed_all(["clean pass"])
        self.assertEqual(result, "clean pass")

    def test_empty_chunks_pass_through(self) -> None:
        result = self._feed_all(["", "hello", "", " world", ""])
        self.assertEqual(result, "hello world")

    def test_fence_content_entirely_stripped(self) -> None:
        result = self._feed_all(["<seal-context>all hidden</seal-context>"])
        self.assertEqual(result, "")

    def test_close_tag_split_across_chunks(self) -> None:
        result = self._feed_all([
            "before<seal-context>hidden</seal-",
            "context>after",
        ])
        self.assertEqual(result, "beforeafter")


if __name__ == "__main__":
    unittest.main()
