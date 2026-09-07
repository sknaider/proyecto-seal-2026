"""Tests for seal/channels/matrix.py.

All network IO is mocked — no real Matrix server required.

Run:
    python3 -m pytest seal/channels/tests/test_matrix.py -v
"""
from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

from seal.channels.matrix import MatrixAdapter, _MATRIX_CS


# ── fixtures ──────────────────────────────────────────────────────────────────

_CFG_PASSWORD = {
    "homeserver": "http://localhost:8008",
    "user_id": "@nexus:localhost",
    "password": "secret",
    "room_ids": ["!room1:localhost"],
    "device_id": "SEAL_TEST",
}

_CFG_TOKEN = {
    "homeserver": "http://localhost:8008",
    "user_id": "@nexus:localhost",
    "access_token": "tok_test",
    "room_ids": ["!room1:localhost"],
}

_LOGIN_RESP = {
    "access_token": "tok_from_login",
    "device_id": "SEAL_TEST",
    "user_id": "@nexus:localhost",
}

_SYNC_EMPTY = {"next_batch": "s1"}

_SYNC_ONE_MSG = {
    "next_batch": "s2",
    "rooms": {
        "join": {
            "!room1:localhost": {
                "timeline": {
                    "events": [
                        {
                            "type": "m.room.message",
                            "event_id": "$evt1",
                            "sender": "@william:localhost",
                            "content": {"msgtype": "m.text", "body": "hola NEXUS"},
                            "origin_server_ts": 1_700_000_000_000,
                        }
                    ]
                }
            }
        }
    },
}

_SEND_RESP = {"event_id": "$sent1"}


def _mock_urlopen(responses: list[dict]):
    """Returns a side_effect callable that yields each dict as a urllib response."""
    idx = [0]

    def _handler(req, timeout=None):
        resp_body = json.dumps(responses[idx[0]]).encode()
        idx[0] = min(idx[0] + 1, len(responses) - 1)
        mock = MagicMock()
        mock.read.return_value = resp_body
        mock.__enter__ = lambda s: s
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    return _handler


def _run(coro):
    return asyncio.run(coro)


# ── init ──────────────────────────────────────────────────────────────────────

class InitTests(unittest.TestCase):
    def test_channel_name(self):
        a = MatrixAdapter(_CFG_TOKEN)
        self.assertEqual(a.channel, "matrix")

    def test_homeserver_stripped(self):
        a = MatrixAdapter({**_CFG_TOKEN, "homeserver": "http://localhost:8008/"})
        self.assertFalse(a._homeserver.endswith("/"))

    def test_access_token_stored(self):
        a = MatrixAdapter(_CFG_TOKEN)
        self.assertEqual(a._access_token, "tok_test")

    def test_no_connected_on_init(self):
        a = MatrixAdapter(_CFG_TOKEN)
        self.assertFalse(a._connected)


# ── connect / login ───────────────────────────────────────────────────────────

class ConnectTests(unittest.TestCase):
    def test_connect_with_token_skips_login(self):
        a = MatrixAdapter(_CFG_TOKEN)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen([_SYNC_EMPTY])):
            _run(a.connect())
        self.assertTrue(a._connected)
        self.assertEqual(a._next_batch, "s1")

    def test_connect_with_password_does_login(self):
        a = MatrixAdapter(_CFG_PASSWORD)
        responses = [_LOGIN_RESP, _SYNC_EMPTY]
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen(responses)):
            _run(a.connect())
        self.assertTrue(a._connected)
        self.assertEqual(a._access_token, "tok_from_login")

    def test_connect_idempotent(self):
        a = MatrixAdapter(_CFG_TOKEN)
        calls = [0]
        orig = _mock_urlopen([_SYNC_EMPTY, _SYNC_EMPTY])

        def counting(*args, **kwargs):
            calls[0] += 1
            return orig(*args, **kwargs)

        with patch("urllib.request.urlopen", side_effect=counting):
            _run(a.connect())
            _run(a.connect())  # second call must be no-op
        self.assertEqual(calls[0], 1)

    def test_disconnect_sets_not_connected(self):
        a = MatrixAdapter(_CFG_TOKEN)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen([_SYNC_EMPTY, {}])):
            _run(a.connect())
            _run(a.disconnect())
        self.assertFalse(a._connected)


# ── send ─────────────────────────────────────────────────────────────────────

class SendTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MatrixAdapter(_CFG_TOKEN)
        self.adapter._connected = True
        self.adapter._access_token = "tok_test"

    def test_send_returns_success(self):
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen([_SEND_RESP])):
            result = _run(self.adapter.send("!room1:localhost", "hello"))
        self.assertTrue(result.success)
        self.assertEqual(result.message_id, "$sent1")
        self.assertEqual(result.channel, "matrix")

    def test_send_uses_correct_room(self):
        captured_url = []

        def _handler(req, timeout=None):
            captured_url.append(req.full_url)
            mock = MagicMock()
            mock.read.return_value = json.dumps(_SEND_RESP).encode()
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=_handler):
            _run(self.adapter.send("!myroom:localhost", "test"))
        self.assertIn("myroom", captured_url[0])
        self.assertIn("m.room.message", captured_url[0])

    def test_send_returns_failure_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
            result = _run(self.adapter.send("!room1:localhost", "hi"))
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_send_includes_reply_to(self):
        body_sent = []

        def _handler(req, timeout=None):
            body_sent.append(json.loads(req.data.decode()))
            mock = MagicMock()
            mock.read.return_value = json.dumps(_SEND_RESP).encode()
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=_handler):
            _run(self.adapter.send("!room1:localhost", "reply!", reply_to="$orig1"))
        self.assertIn("m.relates_to", body_sent[0])

    def test_txn_ids_are_unique(self):
        seen: set[str] = set()

        def _handler(req, timeout=None):
            # Extract txnId from URL: .../send/m.room.message/{txnId}
            parts = req.full_url.split("/")
            seen.add(parts[-1])
            mock = MagicMock()
            mock.read.return_value = json.dumps({"event_id": "$x"}).encode()
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=_handler):
            for _ in range(5):
                _run(self.adapter.send("!room1:localhost", "msg"))
        self.assertEqual(len(seen), 5)


# ── event parsing ─────────────────────────────────────────────────────────────

class ParseSyncTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MatrixAdapter(_CFG_TOKEN)
        self.adapter._user_id = "@nexus:localhost"

    def test_parses_text_message(self):
        events = self.adapter._parse_sync(_SYNC_ONE_MSG)
        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertEqual(evt.text, "hola NEXUS")
        self.assertEqual(evt.user_id, "@william:localhost")
        self.assertEqual(evt.chat_id, "!room1:localhost")
        self.assertEqual(evt.message_id, "$evt1")
        self.assertEqual(evt.channel, "matrix")

    def test_filters_own_messages(self):
        sync = {
            "next_batch": "s3",
            "rooms": {
                "join": {
                    "!room1:localhost": {
                        "timeline": {
                            "events": [
                                {
                                    "type": "m.room.message",
                                    "event_id": "$self1",
                                    "sender": "@nexus:localhost",
                                    "content": {"msgtype": "m.text", "body": "I sent this"},
                                    "origin_server_ts": 1_700_000_000_000,
                                }
                            ]
                        }
                    }
                }
            },
        }
        events = self.adapter._parse_sync(sync)
        self.assertEqual(len(events), 0)

    def test_keeps_own_when_filter_disabled(self):
        self.adapter._filter_own = False
        sync = {
            "next_batch": "s4",
            "rooms": {
                "join": {
                    "!room1:localhost": {
                        "timeline": {
                            "events": [
                                {
                                    "type": "m.room.message",
                                    "event_id": "$self2",
                                    "sender": "@nexus:localhost",
                                    "content": {"msgtype": "m.text", "body": "echo"},
                                    "origin_server_ts": 1_700_000_000_000,
                                }
                            ]
                        }
                    }
                }
            },
        }
        events = self.adapter._parse_sync(sync)
        self.assertEqual(len(events), 1)

    def test_skips_non_message_events(self):
        sync = {
            "next_batch": "s5",
            "rooms": {
                "join": {
                    "!room1:localhost": {
                        "timeline": {
                            "events": [
                                {
                                    "type": "m.room.member",
                                    "event_id": "$member1",
                                    "sender": "@alice:localhost",
                                    "content": {"membership": "join"},
                                    "origin_server_ts": 1_700_000_000_000,
                                }
                            ]
                        }
                    }
                }
            },
        }
        events = self.adapter._parse_sync(sync)
        self.assertEqual(len(events), 0)

    def test_skips_unwatched_rooms(self):
        sync = {
            "next_batch": "s6",
            "rooms": {
                "join": {
                    "!other_room:localhost": {
                        "timeline": {
                            "events": [
                                {
                                    "type": "m.room.message",
                                    "event_id": "$other1",
                                    "sender": "@alice:localhost",
                                    "content": {"msgtype": "m.text", "body": "not for us"},
                                    "origin_server_ts": 1_700_000_000_000,
                                }
                            ]
                        }
                    }
                }
            },
        }
        events = self.adapter._parse_sync(sync)
        self.assertEqual(len(events), 0)

    def test_empty_sync_returns_no_events(self):
        self.assertEqual(self.adapter._parse_sync(_SYNC_EMPTY), [])

    def test_reply_to_extracted(self):
        sync = {
            "next_batch": "s7",
            "rooms": {
                "join": {
                    "!room1:localhost": {
                        "timeline": {
                            "events": [
                                {
                                    "type": "m.room.message",
                                    "event_id": "$reply1",
                                    "sender": "@william:localhost",
                                    "content": {
                                        "msgtype": "m.text",
                                        "body": "reply text",
                                        "m.relates_to": {
                                            "m.in_reply_to": {"event_id": "$original1"}
                                        },
                                    },
                                    "origin_server_ts": 1_700_000_000_000,
                                }
                            ]
                        }
                    }
                }
            },
        }
        events = self.adapter._parse_sync(sync)
        self.assertEqual(events[0].reply_to, "$original1")

    def test_timestamp_parsed_correctly(self):
        events = self.adapter._parse_sync(_SYNC_ONE_MSG)
        self.assertIsInstance(events[0].received_at, datetime)
        self.assertEqual(events[0].received_at.tzinfo, timezone.utc)


# ── healthcheck ───────────────────────────────────────────────────────────────

class HealthcheckTests(unittest.TestCase):
    def test_disconnected_health(self):
        a = MatrixAdapter(_CFG_TOKEN)
        h = a.healthcheck()
        self.assertFalse(h.connected)
        self.assertEqual(h.channel, "matrix")

    def test_connected_health(self):
        a = MatrixAdapter(_CFG_TOKEN)
        a._connected = True
        a._last_event_at = 1.0
        h = a.healthcheck()
        self.assertTrue(h.connected)
        self.assertEqual(h.last_event_at, 1.0)

    def test_error_reflected_in_health(self):
        a = MatrixAdapter(_CFG_TOKEN)
        a._last_error = "timeout"
        h = a.healthcheck()
        self.assertEqual(h.last_error, "timeout")


if __name__ == "__main__":
    unittest.main()
