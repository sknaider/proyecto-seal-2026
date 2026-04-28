"""Tests for seal/tui.py — non-curses logic only."""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import curses  # constants only, no terminal needed

from seal.tui import (
    Message,
    SealTUI,
    Skin,
    fetch_messages,
    parse_input,
    send_message,
)


# ── parse_input ───────────────────────────────────────────────────────────────

class ParseInputTests(unittest.TestCase):
    def test_plain_text(self):
        mtype, payload = parse_input("hello team")
        self.assertEqual(mtype, "conversation")
        self.assertEqual(payload, "hello team")

    def test_steer_command(self):
        mtype, payload = parse_input("/steer focus on CLI")
        self.assertEqual(mtype, "steer")
        self.assertEqual(payload, "focus on CLI")

    def test_queue_command(self):
        mtype, payload = parse_input("/queue run tests after")
        self.assertEqual(mtype, "queue")
        self.assertEqual(payload, "run tests after")

    def test_interrupt_command(self):
        mtype, payload = parse_input("/interrupt")
        self.assertEqual(mtype, "interrupt")
        self.assertEqual(payload, "interrupt")

    def test_steer_no_space_is_plain(self):
        # /steer without trailing space is plain text
        mtype, payload = parse_input("/steer")
        self.assertEqual(mtype, "conversation")

    def test_empty_string(self):
        mtype, payload = parse_input("")
        self.assertEqual(mtype, "conversation")
        self.assertEqual(payload, "")


# ── Message ───────────────────────────────────────────────────────────────────

class MessageTests(unittest.TestCase):
    def _make(self, **kwargs) -> dict:
        base = {
            "id": "msg_001",
            "from": "ADA",
            "message": "seal/upgrade.py listo",
            "timestamp": "2026-04-28T12:15:37",
            "type": "conversation",
        }
        base.update(kwargs)
        return base

    def test_from_dict_basic(self):
        m = Message.from_dict(self._make())
        self.assertEqual(m.sender, "ADA")
        self.assertEqual(m.text, "seal/upgrade.py listo")
        self.assertEqual(m.timestamp, "2026-04-28 12:15")  # T replaced by space

    def test_from_dict_missing_fields(self):
        m = Message.from_dict({})
        self.assertEqual(m.sender, "?")
        self.assertEqual(m.text, "")
        self.assertEqual(m.id, "")

    def test_render_lines_single(self):
        m = Message.from_dict(self._make(message="hello"))
        lines = m.render_lines(0, 80)
        self.assertEqual(len(lines), 1)
        sender, text = lines[0]
        self.assertEqual(sender, "ADA")
        self.assertIn("hello", text)
        self.assertIn("ADA:", text)

    def test_render_lines_wraps(self):
        long_text = "word " * 20
        m = Message.from_dict(self._make(message=long_text))
        lines = m.render_lines(0, 40)
        self.assertGreater(len(lines), 1)
        # continuation lines have same sender
        for sender, _ in lines:
            self.assertEqual(sender, "ADA")

    def test_render_lines_empty_message(self):
        m = Message.from_dict(self._make(message=""))
        lines = m.render_lines(0, 80)
        self.assertEqual(len(lines), 1)


# ── Skin ──────────────────────────────────────────────────────────────────────

class SkinTests(unittest.TestCase):
    def test_default_skin_has_all_agents(self):
        skin = Skin()
        for agent in ("JARVIS", "ADA", "NEXUS", "ALICE", "DUM", "William"):
            self.assertIn(agent, skin.agent_colors)

    def test_agent_pair_returns_int(self):
        skin = Skin()
        # Without a real terminal, color_pair returns 0 — just check it doesn't raise
        pair = skin.agent_pair("ADA")
        self.assertIsInstance(pair, int)

    def test_agent_pair_unknown_returns_default(self):
        skin = Skin()
        pair_unknown = skin.agent_pair("UNKNOWN_AGENT")
        pair_default = skin.agent_pair("UNKNOWN_AGENT")
        self.assertEqual(pair_unknown, pair_default)


# ── SealTUI state machine ─────────────────────────────────────────────────────

class SealTUIStateTests(unittest.TestCase):
    def setUp(self):
        self.tui = SealTUI(profile="test", agent="JARVIS")

    def test_initial_state(self):
        self.assertEqual(self.tui.input_buf, "")
        self.assertEqual(self.tui.input_cursor, 0)
        self.assertEqual(self.tui.scroll_offset, 0)
        self.assertEqual(self.tui.active_pane, "chat")

    def test_type_character(self):
        self.tui.handle_key(ord("h"))
        self.tui.handle_key(ord("i"))
        self.assertEqual(self.tui.input_buf, "hi")
        self.assertEqual(self.tui.input_cursor, 2)

    def test_backspace_removes_char(self):
        self.tui.input_buf = "hello"
        self.tui.input_cursor = 5
        self.tui.handle_key(127)
        self.assertEqual(self.tui.input_buf, "hell")
        self.assertEqual(self.tui.input_cursor, 4)

    def test_backspace_at_start_noop(self):
        self.tui.input_buf = "hi"
        self.tui.input_cursor = 0
        self.tui.handle_key(127)
        self.assertEqual(self.tui.input_buf, "hi")
        self.assertEqual(self.tui.input_cursor, 0)

    def test_arrow_left_right(self):
        self.tui.input_buf = "abc"
        self.tui.input_cursor = 3
        self.tui.handle_key(curses.KEY_LEFT)
        self.assertEqual(self.tui.input_cursor, 2)
        self.tui.handle_key(curses.KEY_RIGHT)
        self.assertEqual(self.tui.input_cursor, 3)

    def test_left_at_start_noop(self):
        self.tui.input_cursor = 0
        self.tui.handle_key(curses.KEY_LEFT)
        self.assertEqual(self.tui.input_cursor, 0)

    def test_right_at_end_noop(self):
        self.tui.input_buf = "hi"
        self.tui.input_cursor = 2
        self.tui.handle_key(curses.KEY_RIGHT)
        self.assertEqual(self.tui.input_cursor, 2)

    def test_home_end_keys(self):
        self.tui.input_buf = "hello"
        self.tui.input_cursor = 3
        self.tui.handle_key(curses.KEY_HOME)
        self.assertEqual(self.tui.input_cursor, 0)
        self.tui.handle_key(curses.KEY_END)
        self.assertEqual(self.tui.input_cursor, 5)

    def test_tab_switches_pane(self):
        self.assertEqual(self.tui.active_pane, "chat")
        self.tui.handle_key(9)
        self.assertEqual(self.tui.active_pane, "sidebar")
        self.tui.handle_key(9)
        self.assertEqual(self.tui.active_pane, "chat")

    def test_scroll_up_down(self):
        self.tui.handle_key(curses.KEY_UP)
        self.assertEqual(self.tui.scroll_offset, 1)
        self.tui.handle_key(curses.KEY_DOWN)
        self.assertEqual(self.tui.scroll_offset, 0)

    def test_scroll_down_clamps_at_zero(self):
        self.tui.scroll_offset = 0
        self.tui.handle_key(curses.KEY_DOWN)
        self.assertEqual(self.tui.scroll_offset, 0)

    def test_q_with_empty_buf_exits(self):
        result = self.tui.handle_key(ord("q"))
        self.assertFalse(result)

    def test_q_with_nonempty_buf_types(self):
        self.tui.input_buf = "que"
        result = self.tui.handle_key(ord("q"))
        self.assertTrue(result)
        self.assertIn("q", self.tui.input_buf)

    def test_insert_at_cursor_middle(self):
        self.tui.input_buf = "ac"
        self.tui.input_cursor = 1
        self.tui.handle_key(ord("b"))
        self.assertEqual(self.tui.input_buf, "abc")
        self.assertEqual(self.tui.input_cursor, 2)

    def test_enter_clears_input(self):
        self.tui.input_buf = "hello"
        self.tui.input_cursor = 5
        with patch("seal.tui.send_message", return_value=True):
            self.tui.handle_key(10)
        self.assertEqual(self.tui.input_buf, "")
        self.assertEqual(self.tui.input_cursor, 0)

    def test_enter_empty_noop(self):
        self.tui.input_buf = ""
        with patch("seal.tui.send_message") as mock_send:
            self.tui.handle_key(10)
            mock_send.assert_not_called()

    def test_submit_calls_correct_type(self):
        self.tui.input_buf = "/steer focus on speed"
        self.tui.input_cursor = len(self.tui.input_buf)
        with patch("seal.tui.send_message", return_value=True) as mock_send:
            self.tui.handle_key(10)
            mock_send.assert_called_once_with("JARVIS", "focus on speed", "steer")

    def test_submit_interrupt(self):
        self.tui.input_buf = "/interrupt"
        self.tui.input_cursor = 10
        with patch("seal.tui.send_message", return_value=True) as mock_send:
            self.tui.handle_key(10)
            mock_send.assert_called_once_with("JARVIS", "interrupt", "interrupt")

    def test_flash_sets_status(self):
        self.tui._flash("test message", 10.0)
        self.assertEqual(self.tui._status_msg, "test message")
        self.assertGreater(self.tui._status_until, 0)


# ── Network (mock HTTP server) ────────────────────────────────────────────────

class MockChatServer(BaseHTTPRequestHandler):
    messages = [{"id": "1", "from": "ADA", "message": "hello", "timestamp": "2026-04-28T12:00:00"}]

    def do_GET(self):
        body = json.dumps({"messages": self.messages}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({"ok": True, "id": "msg_test"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass  # suppress test output


class NetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import seal.tui as tui_mod
        cls._orig_server = tui_mod._CHAT_SERVER
        cls.server = HTTPServer(("127.0.0.1", 0), MockChatServer)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        tui_mod._CHAT_SERVER = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        import seal.tui as tui_mod
        cls.server.shutdown()
        tui_mod._CHAT_SERVER = cls._orig_server

    def test_fetch_messages_returns_list(self):
        msgs = fetch_messages(10)
        self.assertIsInstance(msgs, list)
        self.assertGreater(len(msgs), 0)
        self.assertIsInstance(msgs[0], Message)

    def test_fetch_messages_parses_sender(self):
        msgs = fetch_messages(10)
        self.assertEqual(msgs[0].sender, "ADA")

    def test_send_message_returns_true(self):
        ok = send_message("JARVIS", "test message")
        self.assertTrue(ok)

    def test_fetch_messages_server_down(self):
        import seal.tui as tui_mod
        orig = tui_mod._CHAT_SERVER
        tui_mod._CHAT_SERVER = "http://127.0.0.1:1"
        msgs = fetch_messages()
        self.assertEqual(msgs, [])
        tui_mod._CHAT_SERVER = orig

    def test_send_message_server_down(self):
        import seal.tui as tui_mod
        orig = tui_mod._CHAT_SERVER
        tui_mod._CHAT_SERVER = "http://127.0.0.1:1"
        ok = send_message("JARVIS", "test")
        self.assertFalse(ok)
        tui_mod._CHAT_SERVER = orig


if __name__ == "__main__":
    unittest.main()
