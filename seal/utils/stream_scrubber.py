"""Streaming context scrubber — strips <seal-context>…</seal-context> fences.

LLM output may carry a context fence that internal agents use to pass
shared state without exposing it to the end user.  This module is a
stateful filter: feed it streaming chunks and it returns only the
user-visible portion.

The fence may split across chunk boundaries; the scrubber holds any
partial tag in `_pending` until the next chunk resolves it.

Usage:
    scrubber = StreamScrubber()
    for chunk in response.stream():
        visible = scrubber.feed(chunk)
        if visible:
            send_to_user(visible)
    leftover = scrubber.flush()   # emit any dangling partial tag
    scrubber.reset()              # reuse for the next response
"""
from __future__ import annotations


class StreamScrubber:
    """Filter <seal-context>…</seal-context> fences from streaming text.

    Not thread-safe — one instance per concurrent stream.
    """

    OPEN = "<seal-context>"
    CLOSE = "</seal-context>"

    def __init__(self) -> None:
        self._inside = False
        self._pending = ""

    # ------------------------------------------------------------------

    def feed(self, chunk: str) -> str:
        """Feed one chunk, return the user-visible portion."""
        text = self._pending + chunk
        self._pending = ""
        out: list[str] = []

        while text:
            if not self._inside:
                idx = text.find(self.OPEN)
                if idx == -1:
                    partial = _longest_prefix_suffix(text, self.OPEN)
                    if partial:
                        out.append(text[: len(text) - len(partial)])
                        self._pending = partial
                    else:
                        out.append(text)
                    break
                out.append(text[:idx])
                self._inside = True
                text = text[idx + len(self.OPEN) :]
            else:
                idx = text.find(self.CLOSE)
                if idx == -1:
                    self._pending = _longest_prefix_suffix(text, self.CLOSE)
                    break
                self._inside = False
                text = text[idx + len(self.CLOSE) :]

        return "".join(out)

    def flush(self) -> str:
        """Emit any buffered partial tag; call at end of stream.

        A partial open tag is emitted as visible text (stream ended before
        the fence opened).  An unclosed fence is silently discarded.
        """
        pending, inside = self._pending, self._inside
        self._pending = ""
        self._inside = False
        return pending if not inside else ""

    def reset(self) -> None:
        """Reset state for reuse on the next response."""
        self._inside = False
        self._pending = ""


# ---------------------------------------------------------------------------


def _longest_prefix_suffix(text: str, tag: str) -> str:
    """Return the longest suffix of *text* that is a strict prefix of *tag*."""
    limit = min(len(text), len(tag) - 1)
    for length in range(limit, 0, -1):
        if tag.startswith(text[-length:]):
            return text[-length:]
    return ""
