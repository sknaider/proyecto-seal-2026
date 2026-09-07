"""SEAL terminal UI — chat-style transcript + input pane via Python curses.

The transcript model and buffer logic are kept pure-Python so they can be
unit-tested without an actual terminal. The curses-driven runtime is the
thin shell on top.
"""

from __future__ import annotations

import curses
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Deque, Iterable, Optional


TUIInputCallback = Callable[[str], None]


@dataclass(frozen=True)
class TUIMessage:
    """One line in the transcript pane."""

    role: str  # "user" | "agent" | "system"
    text: str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def render(self) -> str:
        ts = self.received_at.strftime("%H:%M:%S")
        prefix = {"user": "you", "agent": "agent", "system": "sys"}.get(self.role, self.role)
        return f"[{ts}] {prefix}: {self.text}"


class TranscriptModel:
    """Thread-safe ring buffer of TUIMessages plus a dirty flag."""

    def __init__(self, max_lines: int = 500) -> None:
        self._lines: Deque[TUIMessage] = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._dirty = True

    def append(self, msg: TUIMessage) -> None:
        with self._lock:
            self._lines.append(msg)
            self._dirty = True

    def extend(self, msgs: Iterable[TUIMessage]) -> None:
        with self._lock:
            for m in msgs:
                self._lines.append(m)
            self._dirty = True

    def snapshot(self) -> list[TUIMessage]:
        with self._lock:
            return list(self._lines)

    def visible(self, max_rows: int) -> list[TUIMessage]:
        """Return the last `max_rows` lines, oldest first."""
        snap = self.snapshot()
        if max_rows <= 0:
            return []
        return snap[-max_rows:]

    def take_dirty(self) -> bool:
        with self._lock:
            was_dirty = self._dirty
            self._dirty = False
            return was_dirty

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()
            self._dirty = True

    def __len__(self) -> int:
        with self._lock:
            return len(self._lines)


class InputBuffer:
    """Simple text buffer for the input pane — line editing without curses."""

    def __init__(self) -> None:
        self._chars: list[str] = []
        self._cursor: int = 0

    def insert(self, ch: str) -> None:
        if not ch:
            return
        self._chars.insert(self._cursor, ch)
        self._cursor += 1

    def backspace(self) -> None:
        if self._cursor > 0:
            del self._chars[self._cursor - 1]
            self._cursor -= 1

    def delete(self) -> None:
        if self._cursor < len(self._chars):
            del self._chars[self._cursor]

    def left(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1

    def right(self) -> None:
        if self._cursor < len(self._chars):
            self._cursor += 1

    def home(self) -> None:
        self._cursor = 0

    def end(self) -> None:
        self._cursor = len(self._chars)

    def text(self) -> str:
        return "".join(self._chars)

    def cursor(self) -> int:
        return self._cursor

    def submit(self) -> str:
        """Return current text and clear the buffer."""
        out = self.text()
        self._chars.clear()
        self._cursor = 0
        return out


ContextProvider = Callable[[], Optional[dict]]  # returns {"pct": float, "tokens": int, "age_str": str} or None


class TUIApp:
    """The runtime around the transcript and input. The curses loop is in run().

    Tests use the model/buffer directly without invoking run(); the curses
    portion is a thin glue layer.
    """

    def __init__(
        self,
        on_submit: Optional[TUIInputCallback] = None,
        title: str = "SEAL",
        max_transcript_lines: int = 500,
        context_provider: Optional[ContextProvider] = None,
    ) -> None:
        self.transcript = TranscriptModel(max_lines=max_transcript_lines)
        self.input = InputBuffer()
        self.title = title
        self._on_submit = on_submit
        self._stop = threading.Event()
        self._context_provider = context_provider

    def push_agent(self, text: str) -> None:
        self.transcript.append(TUIMessage(role="agent", text=text))

    def push_user(self, text: str) -> None:
        self.transcript.append(TUIMessage(role="user", text=text))

    def push_system(self, text: str) -> None:
        self.transcript.append(TUIMessage(role="system", text=text))

    def submit_input(self) -> Optional[str]:
        text = self.input.submit()
        if not text.strip():
            return None
        self.push_user(text)
        if self._on_submit is not None:
            self._on_submit(text)
        return text

    def stop(self) -> None:
        self._stop.set()

    def is_stopped(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:
        """Blocking curses loop. Tests should not call this."""
        curses.wrapper(self._curses_main)

    def _curses_main(self, stdscr) -> None:
        curses.curs_set(1)
        stdscr.nodelay(True)
        try:
            while not self._stop.is_set():
                stdscr.erase()
                rows, cols = stdscr.getmaxyx()
                self._draw(stdscr, rows, cols)
                stdscr.refresh()
                ch = stdscr.getch()
                if ch == -1:
                    curses.napms(50)
                    continue
                self._handle_key(ch)
        finally:
            curses.curs_set(0)

    def _build_status_bar(self, cols: int) -> str:
        """Build Hermes-style context status bar: AGENT │ tokens/200K │ [bar] % │ age."""
        if self._context_provider is None:
            return ""
        try:
            ctx = self._context_provider()
        except Exception:
            ctx = None
        if not ctx:
            return ""

        pct = ctx.get("pct", 0.0)
        tokens = ctx.get("tokens", 0)
        age_str = ctx.get("age_str", "—")
        agent = ctx.get("agent", self.title)
        limit = ctx.get("limit", 200_000)

        bar_width = max(10, min(20, (cols - 60) // 2))
        filled = int(bar_width * pct / 100)
        bar = "█" * filled + "░" * (bar_width - filled)

        limit_k = f"{limit // 1000}K"
        status = f" {agent} │ {tokens:,} / {limit_k} │ [{bar}] {pct:.1f}% │ {age_str} "
        return status[:cols - 1]

    def _draw(self, stdscr, rows: int, cols: int) -> None:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)    # header
        curses.init_pair(2, curses.COLOR_GREEN, -1)   # status bar ok
        curses.init_pair(3, curses.COLOR_YELLOW, -1)  # status bar warn
        curses.init_pair(4, curses.COLOR_RED, -1)     # status bar danger

        header = f" {self.title} ".center(cols, "─")[:cols]
        try:
            stdscr.addstr(0, 0, header, curses.color_pair(1))
        except curses.error:
            pass

        has_status = self._context_provider is not None
        reserved = 3 if not has_status else 4
        body_rows = max(0, rows - reserved)
        for i, msg in enumerate(self.transcript.visible(body_rows)):
            try:
                stdscr.addstr(1 + i, 0, msg.render()[: cols - 1])
            except curses.error:
                pass

        if has_status:
            status_row = rows - 2
            status_text = self._build_status_bar(cols)
            if status_text:
                try:
                    ctx = self._context_provider()
                    pct = (ctx or {}).get("pct", 0)
                    color = curses.color_pair(4 if pct >= 85 else 3 if pct >= 60 else 2)
                    stdscr.addstr(status_row, 0, status_text.ljust(cols - 1)[:cols - 1], color)
                except curses.error:
                    pass

        prompt = "› " + self.input.text()
        try:
            stdscr.addstr(rows - 1, 0, prompt[: cols - 1])
            stdscr.move(rows - 1, min(2 + self.input.cursor(), cols - 1))
        except curses.error:
            pass

    def _handle_key(self, ch: int) -> None:
        if ch in (10, 13):  # enter
            self.submit_input()
        elif ch in (127, 8, curses.KEY_BACKSPACE):
            self.input.backspace()
        elif ch == curses.KEY_DC:
            self.input.delete()
        elif ch == curses.KEY_LEFT:
            self.input.left()
        elif ch == curses.KEY_RIGHT:
            self.input.right()
        elif ch == curses.KEY_HOME:
            self.input.home()
        elif ch == curses.KEY_END:
            self.input.end()
        elif ch == 3:  # ctrl-c
            self.stop()
        elif 0 <= ch < 256:
            try:
                self.input.insert(chr(ch))
            except ValueError:
                pass
