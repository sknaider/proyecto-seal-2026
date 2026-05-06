"""Tests for the SEAL TUI — model/buffer logic without curses."""

import sys
import pathlib
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.tui.app import TUIApp, TUIMessage, TranscriptModel, InputBuffer


def test_message_render_includes_role_and_text():
    msg = TUIMessage(role="agent", text="hola", received_at=datetime(2026, 4, 30, 21, 30, 0, tzinfo=timezone.utc))
    rendered = msg.render()
    assert "21:30:00" in rendered
    assert "agent" in rendered
    assert "hola" in rendered


def test_transcript_append_and_snapshot():
    t = TranscriptModel()
    t.append(TUIMessage(role="user", text="hi"))
    t.append(TUIMessage(role="agent", text="hello"))
    snap = t.snapshot()
    assert len(snap) == 2
    assert snap[0].text == "hi"
    assert snap[1].text == "hello"


def test_transcript_ring_buffer_evicts_oldest():
    t = TranscriptModel(max_lines=3)
    for i in range(5):
        t.append(TUIMessage(role="system", text=f"m{i}"))
    snap = t.snapshot()
    assert len(snap) == 3
    assert [m.text for m in snap] == ["m2", "m3", "m4"]


def test_transcript_visible_returns_last_n():
    t = TranscriptModel()
    for i in range(10):
        t.append(TUIMessage(role="agent", text=f"line{i}"))
    visible = t.visible(3)
    assert [m.text for m in visible] == ["line7", "line8", "line9"]


def test_transcript_dirty_flag_lifecycle():
    t = TranscriptModel()
    t.append(TUIMessage(role="user", text="x"))
    assert t.take_dirty() is True
    assert t.take_dirty() is False
    t.append(TUIMessage(role="user", text="y"))
    assert t.take_dirty() is True


def test_input_buffer_basic_typing():
    b = InputBuffer()
    for ch in "hola":
        b.insert(ch)
    assert b.text() == "hola"
    assert b.cursor() == 4


def test_input_buffer_backspace_and_arrows():
    b = InputBuffer()
    for ch in "hello":
        b.insert(ch)
    b.left()
    b.left()
    b.backspace()  # remove 'l' before cursor (after 2 lefts cursor at 3 → backspace removes 'l' at index 2)
    assert b.text() == "helo"
    b.home()
    assert b.cursor() == 0
    b.end()
    assert b.cursor() == 4


def test_input_buffer_delete_forward():
    b = InputBuffer()
    for ch in "abc":
        b.insert(ch)
    b.home()
    b.delete()  # remove 'a'
    assert b.text() == "bc"


def test_input_buffer_submit_clears():
    b = InputBuffer()
    for ch in "ping":
        b.insert(ch)
    out = b.submit()
    assert out == "ping"
    assert b.text() == ""
    assert b.cursor() == 0


def test_tuiapp_push_agent_appends_message():
    app = TUIApp()
    app.push_agent("respuesta")
    snap = app.transcript.snapshot()
    assert len(snap) == 1
    assert snap[0].role == "agent"
    assert snap[0].text == "respuesta"


def test_tuiapp_submit_input_invokes_callback():
    received: list[str] = []
    app = TUIApp(on_submit=received.append)
    for ch in "hello":
        app.input.insert(ch)
    out = app.submit_input()
    assert out == "hello"
    assert received == ["hello"]
    snap = app.transcript.snapshot()
    assert snap[-1].role == "user"
    assert snap[-1].text == "hello"


def test_tuiapp_submit_empty_returns_none():
    app = TUIApp(on_submit=lambda _: None)
    assert app.submit_input() is None
    # whitespace only
    app.input.insert(" ")
    app.input.insert(" ")
    assert app.submit_input() is None


def test_tuiapp_stop_flag():
    app = TUIApp()
    assert not app.is_stopped()
    app.stop()
    assert app.is_stopped()


def test_transcript_clear():
    t = TranscriptModel()
    t.append(TUIMessage(role="user", text="x"))
    t.append(TUIMessage(role="agent", text="y"))
    t.clear()
    assert len(t) == 0
    assert t.snapshot() == []


def main() -> int:
    tests = [
        test_message_render_includes_role_and_text,
        test_transcript_append_and_snapshot,
        test_transcript_ring_buffer_evicts_oldest,
        test_transcript_visible_returns_last_n,
        test_transcript_dirty_flag_lifecycle,
        test_input_buffer_basic_typing,
        test_input_buffer_backspace_and_arrows,
        test_input_buffer_delete_forward,
        test_input_buffer_submit_clears,
        test_tuiapp_push_agent_appends_message,
        test_tuiapp_submit_input_invokes_callback,
        test_tuiapp_submit_empty_returns_none,
        test_tuiapp_stop_flag,
        test_transcript_clear,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"[OK] {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
