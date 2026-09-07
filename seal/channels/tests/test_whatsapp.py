"""Tests for seal/channels/whatsapp.py.

All network IO is mocked — no real Evolution API server required.

Run:
    python3 -m pytest seal/channels/tests/test_whatsapp.py -v

Or standalone:
    python3 -m unittest seal.channels.tests.test_whatsapp -v
"""
from __future__ import annotations

import asyncio
import json
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from seal.channels.whatsapp import WhatsAppAdapter


# ── fixtures ──────────────────────────────────────────────────────────────────

_CFG = {
    "base_url": "http://localhost:8080",
    "instance_name": "seal-wa",
    "api_key": "test-key-abc",
    "poll_interval": 0.05,
}

_CONNECTED_RESP = {
    "instance": {"instanceName": "seal-wa", "state": "open"}
}

_DISCONNECTED_RESP = {
    "instance": {"instanceName": "seal-wa", "state": "close"}
}

_SEND_RESP = {
    "key": {
        "remoteJid": "5521912345678@s.whatsapp.net",
        "fromMe": True,
        "id": "OUT_MSG_001",
    },
    "message": {"extendedTextMessage": {"text": "hola!"}},
    "messageTimestamp": 1_700_000_000,
    "status": "PENDING",
}

_RAW_INBOUND = {
    "key": {
        "remoteJid": "5521912345678@s.whatsapp.net",
        "fromMe": False,
        "id": "IN_MSG_001",
    },
    "pushName": "William",
    "messageType": "conversation",
    "message": {"conversation": "oi ADA!"},
    "messageTimestamp": 1_700_000_001,
    "source": "android",
}

_RAW_EXTENDED_TEXT = {
    "key": {
        "remoteJid": "5521900000001@s.whatsapp.net",
        "fromMe": False,
        "id": "IN_MSG_002",
    },
    "pushName": "Henry",
    "messageType": "extendedTextMessage",
    "message": {"extendedTextMessage": {"text": "extended hello"}},
    "messageTimestamp": 1_700_000_002,
    "source": "web",
}

_RAW_GROUP_MSG = {
    "key": {
        "remoteJid": "551191234567-1234567890@g.us",
        "fromMe": False,
        "id": "IN_MSG_003",
    },
    "pushName": "JARVIS",
    "messageType": "conversation",
    "message": {"conversation": "group message"},
    "messageTimestamp": 1_700_000_003,
    "source": "android",
}

_RAW_FROM_ME = {
    "key": {
        "remoteJid": "5521912345678@s.whatsapp.net",
        "fromMe": True,
        "id": "IN_MSG_004",
    },
    "pushName": "",
    "messageType": "conversation",
    "message": {"conversation": "I sent this"},
    "messageTimestamp": 1_700_000_004,
    "source": "android",
}

_RAW_MEDIA_ONLY = {
    "key": {
        "remoteJid": "5521912345678@s.whatsapp.net",
        "fromMe": False,
        "id": "IN_MSG_005",
    },
    "pushName": "Alice",
    "messageType": "imageMessage",
    "message": {"imageMessage": {"url": "https://example.com/img.jpg"}},
    "messageTimestamp": 1_700_000_005,
    "source": "android",
}

_POLL_ONE_MSG = {
    "messages": {
        "total": 1,
        "records": [_RAW_INBOUND],
    }
}

_POLL_EMPTY = {"messages": {"records": []}}


def _mock_urlopen(responses: list[dict]):
    """Return a side_effect that yields each dict as a urllib response."""
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
        a = WhatsAppAdapter(_CFG)
        self.assertEqual(a.channel, "whatsapp")

    def test_base_url_trailing_slash_stripped(self):
        a = WhatsAppAdapter({**_CFG, "base_url": "http://localhost:8080/"})
        self.assertFalse(a._base_url.endswith("/"))

    def test_instance_stored(self):
        a = WhatsAppAdapter(_CFG)
        self.assertEqual(a._instance, "seal-wa")

    def test_api_key_stored(self):
        a = WhatsAppAdapter(_CFG)
        self.assertEqual(a._api_key, "test-key-abc")

    def test_not_connected_on_init(self):
        a = WhatsAppAdapter(_CFG)
        self.assertFalse(a._connected)

    def test_custom_poll_interval(self):
        a = WhatsAppAdapter({**_CFG, "poll_interval": 5.0})
        self.assertEqual(a._poll_interval, 5.0)

    def test_custom_since_ts(self):
        a = WhatsAppAdapter({**_CFG, "since_ts": 999})
        self.assertEqual(a._since_ts, 999)

    def test_ignore_from_me_default_true(self):
        a = WhatsAppAdapter(_CFG)
        self.assertTrue(a._ignore_from_me)

    def test_ignore_from_me_can_be_disabled(self):
        a = WhatsAppAdapter({**_CFG, "ignore_from_me": False})
        self.assertFalse(a._ignore_from_me)


# ── connect / disconnect ──────────────────────────────────────────────────────


class ConnectTests(unittest.TestCase):
    def test_connect_when_open(self):
        a = WhatsAppAdapter(_CFG)
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen([_CONNECTED_RESP])):
            _run(a.connect())
        self.assertTrue(a._connected)

    def test_connect_raises_when_not_open(self):
        a = WhatsAppAdapter(_CFG)
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen([_DISCONNECTED_RESP])):
            with self.assertRaises(RuntimeError):
                _run(a.connect())
        self.assertFalse(a._connected)

    def test_connect_idempotent(self):
        a = WhatsAppAdapter(_CFG)
        calls = [0]
        orig = _mock_urlopen([_CONNECTED_RESP])

        def counting(*args, **kwargs):
            calls[0] += 1
            return orig(*args, **kwargs)

        with patch("urllib.request.urlopen", side_effect=counting):
            _run(a.connect())
            _run(a.connect())  # second call — no new request
        self.assertEqual(calls[0], 1)

    def test_disconnect_sets_not_connected(self):
        a = WhatsAppAdapter(_CFG)
        a._connected = True
        _run(a.disconnect())
        self.assertFalse(a._connected)

    def test_disconnect_idempotent(self):
        a = WhatsAppAdapter(_CFG)
        a._connected = False
        _run(a.disconnect())  # no error
        self.assertFalse(a._connected)


# ── send ─────────────────────────────────────────────────────────────────────


class SendTests(unittest.TestCase):
    def setUp(self):
        self.adapter = WhatsAppAdapter(_CFG)
        self.adapter._connected = True

    def test_send_returns_success(self):
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen([_SEND_RESP])):
            result = _run(self.adapter.send(
                "5521912345678@s.whatsapp.net", "hola!"
            ))
        self.assertTrue(result.success)
        self.assertEqual(result.message_id, "OUT_MSG_001")
        self.assertEqual(result.channel, "whatsapp")

    def test_send_hits_correct_url(self):
        captured = []

        def _handler(req, timeout=None):
            captured.append(req.full_url)
            mock = MagicMock()
            mock.read.return_value = json.dumps(_SEND_RESP).encode()
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=_handler):
            _run(self.adapter.send("5521912345678@s.whatsapp.net", "test"))
        self.assertIn("/message/sendText/seal-wa", captured[0])

    def test_send_includes_apikey_header(self):
        captured_headers = []

        def _handler(req, timeout=None):
            captured_headers.append(dict(req.headers))
            mock = MagicMock()
            mock.read.return_value = json.dumps(_SEND_RESP).encode()
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=_handler):
            _run(self.adapter.send("55219@s.whatsapp.net", "x"))
        headers = captured_headers[0]
        # urllib capitalizes headers
        self.assertIn("Apikey", headers)
        self.assertEqual(headers["Apikey"], "test-key-abc")

    def test_send_body_contains_number_and_text(self):
        captured_body = []

        def _handler(req, timeout=None):
            captured_body.append(json.loads(req.data.decode()))
            mock = MagicMock()
            mock.read.return_value = json.dumps(_SEND_RESP).encode()
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=_handler):
            _run(self.adapter.send("55219@s.whatsapp.net", "hello world"))
        self.assertEqual(captured_body[0]["number"], "55219@s.whatsapp.net")
        self.assertEqual(captured_body[0]["text"], "hello world")

    def test_send_returns_failure_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
            result = _run(self.adapter.send("55219@s.whatsapp.net", "hi"))
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_send_fallback_message_id_when_key_missing(self):
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen([{}])):
            result = _run(self.adapter.send("55219@s.whatsapp.net", "hi"))
        self.assertTrue(result.success)
        self.assertTrue(result.message_id.startswith("wa_"))

    def test_multiple_sends_have_unique_fallback_ids(self):
        seen: set[str] = set()
        for _ in range(5):
            with patch("urllib.request.urlopen",
                       side_effect=_mock_urlopen([{}])):
                result = _run(self.adapter.send("55219@s.whatsapp.net", "x"))
            seen.add(result.message_id)
        self.assertEqual(len(seen), 5)


# ── parse message ─────────────────────────────────────────────────────────────


class ParseMessageTests(unittest.TestCase):
    def setUp(self):
        self.adapter = WhatsAppAdapter(_CFG)

    def test_parses_conversation_text(self):
        evt = self.adapter._parse_message(_RAW_INBOUND)
        self.assertIsNotNone(evt)
        self.assertEqual(evt.text, "oi ADA!")
        self.assertEqual(evt.channel, "whatsapp")

    def test_parses_extended_text_message(self):
        evt = self.adapter._parse_message(_RAW_EXTENDED_TEXT)
        self.assertIsNotNone(evt)
        self.assertEqual(evt.text, "extended hello")

    def test_user_id_is_remote_jid(self):
        evt = self.adapter._parse_message(_RAW_INBOUND)
        self.assertEqual(evt.user_id, "5521912345678@s.whatsapp.net")

    def test_chat_id_is_remote_jid(self):
        evt = self.adapter._parse_message(_RAW_INBOUND)
        self.assertEqual(evt.chat_id, "5521912345678@s.whatsapp.net")

    def test_message_id_from_key(self):
        evt = self.adapter._parse_message(_RAW_INBOUND)
        self.assertEqual(evt.message_id, "IN_MSG_001")

    def test_timestamp_parsed_correctly(self):
        evt = self.adapter._parse_message(_RAW_INBOUND)
        self.assertIsInstance(evt.received_at, datetime)
        self.assertEqual(evt.received_at.tzinfo, timezone.utc)
        self.assertEqual(
            evt.received_at,
            datetime.fromtimestamp(1_700_000_001, tz=timezone.utc),
        )

    def test_dm_detection_for_private_chat(self):
        evt = self.adapter._parse_message(_RAW_INBOUND)
        self.assertTrue(evt.is_dm)

    def test_group_message_is_not_dm(self):
        evt = self.adapter._parse_message(_RAW_GROUP_MSG)
        self.assertFalse(evt.is_dm)

    def test_skips_from_me_when_ignore_enabled(self):
        evt = self.adapter._parse_message(_RAW_FROM_ME)
        self.assertIsNone(evt)

    def test_keeps_from_me_when_ignore_disabled(self):
        a = WhatsAppAdapter({**_CFG, "ignore_from_me": False})
        evt = a._parse_message(_RAW_FROM_ME)
        self.assertIsNotNone(evt)
        self.assertEqual(evt.text, "I sent this")

    def test_skips_media_only_messages(self):
        evt = self.adapter._parse_message(_RAW_MEDIA_ONLY)
        self.assertIsNone(evt)

    def test_metadata_push_name(self):
        evt = self.adapter._parse_message(_RAW_INBOUND)
        self.assertEqual(evt.metadata["push_name"], "William")

    def test_metadata_instance(self):
        evt = self.adapter._parse_message(_RAW_INBOUND)
        self.assertEqual(evt.metadata["instance"], "seal-wa")

    def test_metadata_from_me_false(self):
        evt = self.adapter._parse_message(_RAW_INBOUND)
        self.assertFalse(evt.metadata["from_me"])

    def test_empty_key_id_gets_uuid(self):
        raw = {**_RAW_INBOUND, "key": {"remoteJid": "55219@s.whatsapp.net", "fromMe": False, "id": ""}}
        evt = self.adapter._parse_message(raw)
        self.assertIsNotNone(evt)
        self.assertTrue(len(evt.message_id) > 0)


# ── poll messages ─────────────────────────────────────────────────────────────


class PollMessagesTests(unittest.TestCase):
    def test_returns_new_messages(self):
        a = WhatsAppAdapter(_CFG)
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen([_POLL_ONE_MSG])):
            msgs = a._poll_messages()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["key"]["id"], "IN_MSG_001")

    def test_deduplicates_same_id(self):
        a = WhatsAppAdapter(_CFG)
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen([_POLL_ONE_MSG, _POLL_ONE_MSG])):
            first = a._poll_messages()
            second = a._poll_messages()
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)

    def test_empty_poll_returns_empty_list(self):
        a = WhatsAppAdapter(_CFG)
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen([_POLL_EMPTY])):
            msgs = a._poll_messages()
        self.assertEqual(msgs, [])

    def test_advances_since_ts(self):
        a = WhatsAppAdapter({**_CFG, "since_ts": 0})
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen([_POLL_ONE_MSG])):
            a._poll_messages()
        self.assertGreaterEqual(a._since_ts, 1_700_000_001)

    def test_poll_request_body_has_filter(self):
        a = WhatsAppAdapter({**_CFG, "since_ts": 12345})
        captured = []

        def _handler(req, timeout=None):
            captured.append(json.loads(req.data.decode()))
            mock = MagicMock()
            mock.read.return_value = json.dumps(_POLL_EMPTY).encode()
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=_handler):
            a._poll_messages()
        self.assertIn("where", captured[0])
        self.assertEqual(
            captured[0]["where"]["messageTimestamp"]["gte"], 12345
        )

    def test_poll_request_hits_correct_url(self):
        a = WhatsAppAdapter(_CFG)
        captured_url = []

        def _handler(req, timeout=None):
            captured_url.append(req.full_url)
            mock = MagicMock()
            mock.read.return_value = json.dumps(_POLL_EMPTY).encode()
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=_handler):
            a._poll_messages()
        self.assertIn("/message/findMessages/seal-wa", captured_url[0])

    def test_accepts_messages_as_list(self):
        """Handles Evolution API responses where 'messages' is a plain list."""
        a = WhatsAppAdapter(_CFG)
        resp = {"messages": [_RAW_INBOUND]}
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen([resp])):
            msgs = a._poll_messages()
        self.assertEqual(len(msgs), 1)


# ── check connection ──────────────────────────────────────────────────────────


class CheckConnectionTests(unittest.TestCase):
    def test_open_state_does_not_raise(self):
        a = WhatsAppAdapter(_CFG)
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen([_CONNECTED_RESP])):
            a._check_connection()  # no exception

    def test_closed_state_raises(self):
        a = WhatsAppAdapter(_CFG)
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen([_DISCONNECTED_RESP])):
            with self.assertRaises(RuntimeError) as ctx:
                a._check_connection()
        self.assertIn("state=", str(ctx.exception))

    def test_check_hits_connectionstate_url(self):
        a = WhatsAppAdapter(_CFG)
        captured = []

        def _handler(req, timeout=None):
            captured.append(req.full_url)
            mock = MagicMock()
            mock.read.return_value = json.dumps(_CONNECTED_RESP).encode()
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=_handler):
            a._check_connection()
        self.assertIn("/instance/connectionState/seal-wa", captured[0])


# ── healthcheck ───────────────────────────────────────────────────────────────


class HealthcheckTests(unittest.TestCase):
    def test_initial_health_disconnected(self):
        a = WhatsAppAdapter(_CFG)
        h = a.healthcheck()
        self.assertFalse(h.connected)
        self.assertEqual(h.channel, "whatsapp")
        self.assertIsNone(h.last_error)

    def test_connected_health(self):
        a = WhatsAppAdapter(_CFG)
        a._connected = True
        a._last_event_at = 1_700_000_000.0
        h = a.healthcheck()
        self.assertTrue(h.connected)
        self.assertEqual(h.last_event_at, 1_700_000_000.0)

    def test_error_reflected(self):
        a = WhatsAppAdapter(_CFG)
        a._last_error = "connection refused"
        h = a.healthcheck()
        self.assertEqual(h.last_error, "connection refused")


# ── request helper ────────────────────────────────────────────────────────────


class RequestTests(unittest.TestCase):
    def test_apikey_header_sent(self):
        a = WhatsAppAdapter(_CFG)
        captured = []

        def _handler(req, timeout=None):
            captured.append(dict(req.headers))
            mock = MagicMock()
            mock.read.return_value = b"{}"
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=_handler):
            a._request("GET", "http://localhost:8080/test")
        self.assertIn("Apikey", captured[0])
        self.assertEqual(captured[0]["Apikey"], "test-key-abc")

    def test_content_type_is_json(self):
        a = WhatsAppAdapter(_CFG)
        captured = []

        def _handler(req, timeout=None):
            captured.append(dict(req.headers))
            mock = MagicMock()
            mock.read.return_value = b"{}"
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=_handler):
            a._request("POST", "http://localhost:8080/test", {"x": 1})
        self.assertEqual(captured[0]["Content-type"], "application/json")

    def test_body_is_utf8_encoded(self):
        a = WhatsAppAdapter(_CFG)
        captured_data = []

        def _handler(req, timeout=None):
            captured_data.append(req.data)
            mock = MagicMock()
            mock.read.return_value = b"{}"
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        payload = {"text": "olá — ñ"}
        with patch("urllib.request.urlopen", side_effect=_handler):
            a._request("POST", "http://localhost:8080/test", payload)
        decoded = json.loads(captured_data[0].decode("utf-8"))
        self.assertEqual(decoded["text"], "olá — ñ")

    def test_http_error_raises_runtime_error(self):
        import urllib.error
        a = WhatsAppAdapter(_CFG)
        exc = urllib.error.HTTPError(
            url="http://x", code=401, msg="Unauthorized",
            hdrs=None, fp=None,  # type: ignore[arg-type]
        )
        exc.read = lambda: b'{"message": "invalid api key"}'
        with patch("urllib.request.urlopen", side_effect=exc):
            with self.assertRaises(RuntimeError) as ctx:
                a._request("GET", "http://localhost:8080/test")
        self.assertIn("401", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
