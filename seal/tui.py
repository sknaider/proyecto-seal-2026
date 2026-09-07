#!/usr/bin/env python3
"""
seal/tui.py — SEAL Terminal UI (curses, stdlib only)

Layout:
  ┌─ SEAL Studio v0.1 — profile: default ─────────────────────────────┐
  │ AGENTS         │ 12:34 JARVIS: Sprint completo. 188/188 verde.     │
  │ ● JARVIS       │ 12:35 ADA: upgrade.py listo.                      │
  │ ● ADA          │ 12:36 William: como va?                           │
  │ ● NEXUS        │ > _                                               │
  ├────────────────┴──────────────────────────────────────────────────┤
  │ ↑↓ scroll | Tab: pane | /steer /queue /interrupt | q: quit        │
  └───────────────────────────────────────────────────────────────────┘

Usage:
    python3 -m seal.tui --profile default --agent JARVIS
    seal tui --profile default
"""
from __future__ import annotations

import curses
import json
import textwrap
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

_VERSION = "0.1.0"
_CHAT_SERVER = "http://localhost:8765"
_POLL_SECS = 0.5
_SIDEBAR_W = 18
_MIN_COLS = 60
_MIN_ROWS = 10

# ── Skin (color pairs) ────────────────────────────────────────────────────────

@dataclass
class Skin:
    """Color theme. All values are curses color constants."""
    header_fg: int = curses.COLOR_BLACK
    header_bg: int = curses.COLOR_CYAN
    status_fg: int = curses.COLOR_BLACK
    status_bg: int = curses.COLOR_GREEN
    agent_colors: dict[str, int] = field(default_factory=lambda: {
        "JARVIS":  curses.COLOR_BLUE,
        "ADA":     curses.COLOR_CYAN,
        "NEXUS":   curses.COLOR_MAGENTA,
        "ALICE":   curses.COLOR_GREEN,
        "DUM":     curses.COLOR_YELLOW,
        "William": curses.COLOR_WHITE,
    })
    default_color: int = curses.COLOR_WHITE

    # Pair IDs — fixed layout:
    # 1..6  → agent-specific
    # 7     → default
    # 8     → header
    # 9     → status bar
    # 10    → active pane highlight

    def init(self) -> None:
        curses.start_color()
        curses.use_default_colors()
        for idx, color in enumerate(self.agent_colors.values(), start=1):
            curses.init_pair(idx, color, -1)
        curses.init_pair(7,  self.default_color,  -1)
        curses.init_pair(8,  self.header_fg, self.header_bg)
        curses.init_pair(9,  self.status_fg, self.status_bg)
        curses.init_pair(10, curses.COLOR_WHITE, curses.COLOR_BLUE)

    def agent_pair(self, sender: str) -> int:
        keys = list(self.agent_colors.keys())
        try:
            idx = keys.index(sender) + 1
        except ValueError:
            idx = 7
        try:
            return curses.color_pair(idx)
        except curses.error:
            return 0  # no terminal — safe fallback for tests


DEFAULT_SKIN = Skin()

# ── Message model ─────────────────────────────────────────────────────────────

@dataclass
class Message:
    id: str
    sender: str
    text: str
    timestamp: str
    mtype: str = "conversation"

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(
            id=d.get("id", ""),
            sender=d.get("from", "?"),
            text=d.get("message", ""),
            timestamp=d.get("timestamp", "")[:16].replace("T", " "),
            mtype=d.get("type", "conversation"),
        )

    def render_lines(self, prefix_w: int, chat_w: int) -> list[tuple[str, str]]:
        prefix = f"{self.timestamp} {self.sender}: "
        wrap_w = max(chat_w - len(prefix), 10)
        wrapped = textwrap.wrap(self.text, wrap_w) or [""]
        lines = [(self.sender, prefix + wrapped[0])]
        for cont in wrapped[1:]:
            lines.append((self.sender, " " * len(prefix) + cont))
        return lines


# ── Network ───────────────────────────────────────────────────────────────────

def fetch_messages(limit: int = 100) -> list[Message]:
    try:
        url = f"{_CHAT_SERVER}/api/agents/messages?limit={limit}"
        with urllib.request.urlopen(url, timeout=1) as r:
            data = json.loads(r.read())
            return [Message.from_dict(m) for m in data.get("messages", [])]
    except Exception:
        return []


def send_message(sender: str, text: str, mtype: str = "conversation") -> bool:
    try:
        payload = json.dumps(
            {"from": sender, "to": "equipo", "type": mtype,
             "channel": "web_chat", "message": text},
            ensure_ascii=False,
        ).encode()
        req = urllib.request.Request(
            f"{_CHAT_SERVER}/api/agents/send",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception:
        return False


# ── Input parser ──────────────────────────────────────────────────────────────

def parse_input(text: str) -> tuple[str, str]:
    """Return (message_type, payload) from raw input text."""
    if text.startswith("/steer "):
        return "steer", text[7:]
    if text.startswith("/queue "):
        return "queue", text[7:]
    if text.strip() == "/interrupt":
        return "interrupt", "interrupt"
    return "conversation", text


# ── TUI state machine ─────────────────────────────────────────────────────────

class SealTUI:
    def __init__(self, profile: str, agent: str, skin: Optional[Skin] = None):
        self.profile = profile
        self.agent = agent
        self.skin = skin or DEFAULT_SKIN
        self.messages: list[Message] = []
        self.scroll_offset: int = 0
        self.input_buf: str = ""
        self.input_cursor: int = 0
        self.active_pane: str = "chat"
        self._last_poll: float = 0.0
        self._status_msg: str = ""
        self._status_until: float = 0.0

    # ── drawing ───────────────────────────────────────────────────────────────

    def _draw_header(self, scr) -> None:
        h, w = scr.getmaxyx()
        title = f" SEAL Studio v{_VERSION} — profile: {self.profile} | agent: {self.agent} "[:w]
        try:
            scr.attron(curses.color_pair(8))
            scr.addstr(0, 0, title.ljust(w))
            scr.attroff(curses.color_pair(8))
        except curses.error:
            pass

    def _draw_status(self, scr) -> None:
        h, w = scr.getmaxyx()
        if self._status_msg and time.monotonic() < self._status_until:
            bar = f" {self._status_msg} "
        else:
            bar = " ↑↓ scroll | Tab: pane | /steer /queue /interrupt | q: quit "
        bar = bar[:w]
        try:
            scr.attron(curses.color_pair(9))
            scr.addstr(h - 2, 0, bar.ljust(w))
            scr.attroff(curses.color_pair(9))
        except curses.error:
            pass

    def _draw_sidebar(self, scr) -> None:
        h, w = scr.getmaxyx()
        agents_seen: dict[str, str] = {}
        for m in self.messages:
            agents_seen.setdefault(m.sender, m.timestamp)

        active = self.active_pane == "sidebar"
        hdr_attr = curses.color_pair(10) if active else curses.A_BOLD
        try:
            scr.addstr(1, 0, "AGENTS".ljust(_SIDEBAR_W), hdr_attr)
        except curses.error:
            pass

        row = 2
        for agent in sorted(agents_seen):
            if row >= h - 3:
                break
            label = f"● {agent}"[:_SIDEBAR_W]
            try:
                scr.addstr(row, 0, label.ljust(_SIDEBAR_W), self.skin.agent_pair(agent))
            except curses.error:
                pass
            row += 1

        for r in range(1, h - 2):
            try:
                scr.addch(r, _SIDEBAR_W, curses.ACS_VLINE)
            except curses.error:
                pass

    def _draw_chat(self, scr) -> None:
        h, w = scr.getmaxyx()
        cx = _SIDEBAR_W + 1
        cw = w - cx
        if cw < 10:
            return

        rendered: list[tuple[str, str]] = []
        for m in self.messages:
            rendered.extend(m.render_lines(0, cw))

        visible_h = h - 4
        total = len(rendered)
        start = max(0, total - visible_h - self.scroll_offset)
        end = start + visible_h

        for i, (sender, line) in enumerate(rendered[start:end]):
            row = 1 + i
            if row >= h - 3:
                break
            try:
                scr.addstr(row, cx, line[:cw], self.skin.agent_pair(sender))
            except curses.error:
                pass

    def _draw_input(self, scr) -> None:
        h, w = scr.getmaxyx()
        cx = _SIDEBAR_W + 1
        cw = w - cx
        row = h - 1
        prompt = "> "
        visible = (prompt + self.input_buf)[: cw - 1]
        try:
            scr.addstr(row, cx, visible.ljust(cw - 1))
            cursor_col = cx + len(prompt) + min(self.input_cursor, cw - len(prompt) - 2)
            scr.move(row, min(cursor_col, w - 1))
        except curses.error:
            pass

    def _flash(self, msg: str, secs: float = 2.0) -> None:
        self._status_msg = msg
        self._status_until = time.monotonic() + secs

    # ── input handling ────────────────────────────────────────────────────────

    def handle_key(self, ch: int) -> bool:
        """Process one keystroke. Returns False to exit."""
        if ch == ord("q") and not self.input_buf:
            return False
        elif ch == 9:  # Tab
            self.active_pane = "sidebar" if self.active_pane == "chat" else "chat"
        elif ch == curses.KEY_UP:
            self.scroll_offset += 1
        elif ch == curses.KEY_DOWN:
            self.scroll_offset = max(0, self.scroll_offset - 1)
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            if self.input_cursor > 0:
                pos = self.input_cursor - 1
                self.input_buf = self.input_buf[:pos] + self.input_buf[pos + 1:]
                self.input_cursor -= 1
        elif ch == curses.KEY_LEFT:
            self.input_cursor = max(0, self.input_cursor - 1)
        elif ch == curses.KEY_RIGHT:
            self.input_cursor = min(len(self.input_buf), self.input_cursor + 1)
        elif ch == curses.KEY_HOME:
            self.input_cursor = 0
        elif ch == curses.KEY_END:
            self.input_cursor = len(self.input_buf)
        elif ch in (10, 13):  # Enter
            self._submit()
        elif 32 <= ch < 256:
            pos = self.input_cursor
            self.input_buf = self.input_buf[:pos] + chr(ch) + self.input_buf[pos:]
            self.input_cursor += 1
        return True

    def _submit(self) -> None:
        text = self.input_buf.strip()
        self.input_buf = ""
        self.input_cursor = 0
        if not text:
            return
        mtype, payload = parse_input(text)
        ok = send_message(self.agent, payload, mtype)
        if ok:
            self._flash(f"sent [{mtype}]")
            self.scroll_offset = 0
        else:
            self._flash("send failed — server unreachable", 3.0)

    # ── main loop ─────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        now = time.monotonic()
        if now - self._last_poll >= _POLL_SECS:
            msgs = fetch_messages(150)
            if msgs:
                self.messages = msgs
            self._last_poll = now

    def run(self, scr) -> None:
        self.skin.init()
        curses.curs_set(1)
        curses.halfdelay(5)  # blocks max 0.5s per getch
        scr.keypad(True)
        self._poll()

        while True:
            h, w = scr.getmaxyx()
            scr.erase()

            if h < _MIN_ROWS or w < _MIN_COLS:
                try:
                    scr.addstr(0, 0, f"Terminal too small ({w}×{h}). Need {_MIN_COLS}×{_MIN_ROWS}.")
                except curses.error:
                    pass
            else:
                self._poll()
                self._draw_header(scr)
                self._draw_sidebar(scr)
                self._draw_chat(scr)
                self._draw_status(scr)
                self._draw_input(scr)

            scr.refresh()

            try:
                ch = scr.getch()
            except curses.error:
                continue
            if ch == -1:
                continue
            if not self.handle_key(ch):
                break


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="SEAL Terminal UI")
    ap.add_argument("--profile", default="default", help="Profile name")
    ap.add_argument("--agent", default="JARVIS", help="Agent identity")
    args = ap.parse_args()
    tui = SealTUI(profile=args.profile, agent=args.agent)
    curses.wrapper(tui.run)


if __name__ == "__main__":
    main()
