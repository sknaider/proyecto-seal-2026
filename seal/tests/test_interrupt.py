"""Tests for seal/interrupt.py CANCEL and QUEUE modes.

Run:
    python3 -m pytest seal/tests/test_interrupt.py -v
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from seal.interrupt import (
    _cancel_path,
    _consume_flag_file,
    _consume_queue_file,
    _queue_path,
    check_cancel,
    pop_queued,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_cancel_flag(agent: str, value: bool = True) -> str:
    path = _cancel_path(agent)
    with open(path, "w") as f:
        json.dump({"interrupt": value}, f)
    return path


def _write_queue_file(agent: str, message: str) -> str:
    path = _queue_path(agent)
    with open(path, "w") as f:
        json.dump({"message": message}, f)
    return path


def _mock_404(url, timeout=None):
    import urllib.error
    raise urllib.error.HTTPError(url=url, code=404, msg="Not Found", hdrs=None, fp=None)


def _mock_no_interrupt(url, timeout=None):
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.read.return_value = json.dumps({"interrupt": False}).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = unittest.mock.MagicMock(return_value=False)
    return resp


def _mock_interrupt_true(url, timeout=None):
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.read.return_value = json.dumps({"interrupt": True}).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = unittest.mock.MagicMock(return_value=False)
    return resp


def _mock_queue_message(message: str):
    def _handler(url, timeout=None):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.read.return_value = json.dumps({"message": message}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = unittest.mock.MagicMock(return_value=False)
        return resp
    return _handler


def _mock_no_queue(url, timeout=None):
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.read.return_value = json.dumps({}).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = unittest.mock.MagicMock(return_value=False)
    return resp


# ── _consume_flag_file ────────────────────────────────────────────────────────

class ConsumeFlagFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def _path(self, name: str) -> str:
        return os.path.join(self._tmp, name)

    def test_returns_true_when_interrupt_set(self):
        p = self._path("flag.json")
        with open(p, "w") as f:
            json.dump({"interrupt": True}, f)
        self.assertTrue(_consume_flag_file(p))

    def test_returns_false_when_interrupt_false(self):
        p = self._path("flag.json")
        with open(p, "w") as f:
            json.dump({"interrupt": False}, f)
        self.assertFalse(_consume_flag_file(p))

    def test_deletes_file_after_reading(self):
        p = self._path("flag.json")
        with open(p, "w") as f:
            json.dump({"interrupt": True}, f)
        _consume_flag_file(p)
        self.assertFalse(os.path.exists(p))

    def test_returns_false_when_missing(self):
        self.assertFalse(_consume_flag_file(self._path("ghost.json")))

    def test_one_shot_second_read_returns_false(self):
        p = self._path("oneshot.json")
        with open(p, "w") as f:
            json.dump({"interrupt": True}, f)
        _consume_flag_file(p)
        self.assertFalse(_consume_flag_file(p))


# ── _consume_queue_file ───────────────────────────────────────────────────────

class ConsumeQueueFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def _path(self, name: str) -> str:
        return os.path.join(self._tmp, name)

    def test_returns_message_string(self):
        p = self._path("queue.json")
        with open(p, "w") as f:
            json.dump({"message": "agrega validación RUC"}, f)
        result = _consume_queue_file(p)
        self.assertEqual(result, "agrega validación RUC")

    def test_deletes_file_after_reading(self):
        p = self._path("queue.json")
        with open(p, "w") as f:
            json.dump({"message": "test"}, f)
        _consume_queue_file(p)
        self.assertFalse(os.path.exists(p))

    def test_returns_none_when_missing(self):
        self.assertIsNone(_consume_queue_file(self._path("ghost.json")))

    def test_one_shot_second_read_returns_none(self):
        p = self._path("q2.json")
        with open(p, "w") as f:
            json.dump({"message": "hello"}, f)
        _consume_queue_file(p)
        self.assertIsNone(_consume_queue_file(p))

    def test_returns_none_when_message_empty(self):
        p = self._path("empty.json")
        with open(p, "w") as f:
            json.dump({"message": ""}, f)
        self.assertIsNone(_consume_queue_file(p))


# ── check_cancel via server ───────────────────────────────────────────────────

class CheckCancelServerTests(unittest.TestCase):
    def test_raises_system_exit_when_server_returns_interrupt(self):
        with patch("urllib.request.urlopen", side_effect=_mock_interrupt_true):
            with self.assertRaises(SystemExit) as cm:
                check_cancel("ADA")
        self.assertEqual(cm.exception.code, 0)

    def test_no_exit_when_server_returns_no_interrupt(self):
        with patch("urllib.request.urlopen", side_effect=_mock_no_interrupt):
            check_cancel("ADA")  # must not raise

    def test_no_exit_when_server_returns_404(self):
        with patch("urllib.request.urlopen", side_effect=_mock_404):
            check_cancel("ADA")  # 404 = no interrupt pending

    def test_no_exit_when_server_unreachable(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
            check_cancel("NEXUS")  # must not raise


# ── check_cancel via flag file ────────────────────────────────────────────────

class CheckCancelFlagFileTests(unittest.TestCase):
    def tearDown(self):
        for agent in ("FlagAgent",):
            path = _cancel_path(agent)
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_raises_system_exit_from_flag_file(self):
        _write_cancel_flag("FlagAgent")
        with patch("urllib.request.urlopen", side_effect=_mock_404):
            with self.assertRaises(SystemExit) as cm:
                check_cancel("FlagAgent")
        self.assertEqual(cm.exception.code, 0)

    def test_flag_file_deleted_after_cancel(self):
        path = _write_cancel_flag("FlagAgent")
        with patch("urllib.request.urlopen", side_effect=_mock_404):
            try:
                check_cancel("FlagAgent")
            except SystemExit:
                pass
        self.assertFalse(os.path.exists(path))

    def test_no_exit_when_no_flag_file_and_server_unreachable(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
            check_cancel("FlagAgent")  # must not raise


# ── pop_queued via server ─────────────────────────────────────────────────────

class PopQueuedServerTests(unittest.TestCase):
    def test_returns_message_from_server(self):
        with patch("urllib.request.urlopen", side_effect=_mock_queue_message("fix the output")):
            result = pop_queued("ADA")
        self.assertEqual(result, "fix the output")

    def test_returns_none_when_server_has_no_queue(self):
        with patch("urllib.request.urlopen", side_effect=_mock_no_queue):
            result = pop_queued("ADA")
        self.assertIsNone(result)

    def test_returns_none_on_404(self):
        with patch("urllib.request.urlopen", side_effect=_mock_404):
            result = pop_queued("JARVIS")
        self.assertIsNone(result)

    def test_returns_none_when_server_unreachable(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
            result = pop_queued("NEXUS")
        self.assertIsNone(result)


# ── pop_queued via queue file ─────────────────────────────────────────────────

class PopQueuedFileTests(unittest.TestCase):
    def tearDown(self):
        for agent in ("QAgent",):
            try:
                os.unlink(_queue_path(agent))
            except FileNotFoundError:
                pass

    def test_returns_message_from_queue_file(self):
        _write_queue_file("QAgent", "también valida el peso")
        with patch("urllib.request.urlopen", side_effect=_mock_404):
            result = pop_queued("QAgent")
        self.assertEqual(result, "también valida el peso")

    def test_queue_file_deleted_after_pop(self):
        path = _write_queue_file("QAgent", "test message")
        with patch("urllib.request.urlopen", side_effect=_mock_404):
            pop_queued("QAgent")
        self.assertFalse(os.path.exists(path))

    def test_one_shot_second_pop_returns_none(self):
        _write_queue_file("QAgent", "once")
        with patch("urllib.request.urlopen", side_effect=_mock_404):
            first = pop_queued("QAgent")
            second = pop_queued("QAgent")
        self.assertEqual(first, "once")
        self.assertIsNone(second)


# ── path helpers ──────────────────────────────────────────────────────────────

class PathHelperTests(unittest.TestCase):
    def test_cancel_path_uses_lowercase(self):
        self.assertIn("ada", _cancel_path("ADA"))

    def test_queue_path_uses_lowercase(self):
        self.assertIn("jarvis", _queue_path("JARVIS"))

    def test_cancel_and_queue_paths_differ(self):
        self.assertNotEqual(_cancel_path("ADA"), _queue_path("ADA"))


if __name__ == "__main__":
    unittest.main()
