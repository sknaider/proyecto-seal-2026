"""Tests for the Matrix channel adapter — parser + credential checks, no live homeserver."""

import asyncio
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.matrix import MatrixChannel
from tools.gateway.base import GatewayError, OutboundMessage


async def noop_handler(_):
    return None


async def test_start_without_credentials_raises():
    saved_h = os.environ.pop("MATRIX_HOMESERVER", None)
    saved_t = os.environ.pop("MATRIX_ACCESS_TOKEN", None)
    try:
        ch = MatrixChannel(noop_handler, homeserver="", access_token="")
        raised = False
        try:
            await ch.start()
        except GatewayError:
            raised = True
        assert raised
        assert not ch.running
    finally:
        if saved_h is not None:
            os.environ["MATRIX_HOMESERVER"] = saved_h
        if saved_t is not None:
            os.environ["MATRIX_ACCESS_TOKEN"] = saved_t


async def test_send_without_credentials_raises():
    ch = MatrixChannel(noop_handler, homeserver="", access_token="")
    raised = False
    try:
        await ch.send(OutboundMessage(channel="matrix", platform_chat_id="!room:srv", text="hi"))
    except GatewayError:
        raised = True
    assert raised


def test_parse_sync_extracts_text_messages():
    payload = {
        "next_batch": "s2_3_0_1",
        "rooms": {
            "join": {
                "!abc:server": {
                    "timeline": {
                        "events": [
                            {
                                "type": "m.room.message",
                                "event_id": "$evt1",
                                "sender": "@william:server",
                                "origin_server_ts": 1730000000000,
                                "content": {"msgtype": "m.text", "body": "hola jarvis"},
                            }
                        ]
                    }
                }
            }
        },
    }
    messages = MatrixChannel.parse_sync(payload)
    assert len(messages) == 1
    msg = messages[0]
    assert msg.channel == "matrix"
    assert msg.platform_user_id == "@william:server"
    assert msg.platform_chat_id == "!abc:server"
    assert msg.text == "hola jarvis"
    assert msg.platform_message_id == "$evt1"
    assert msg.received_at.tzinfo is not None


def test_parse_sync_filters_non_text_events():
    payload = {
        "rooms": {
            "join": {
                "!r:s": {
                    "timeline": {
                        "events": [
                            {"type": "m.room.member", "sender": "@x:s"},
                            {
                                "type": "m.room.message",
                                "sender": "@u:s",
                                "event_id": "$e",
                                "origin_server_ts": 1700000000000,
                                "content": {"msgtype": "m.image", "body": "img.png"},
                            },
                            {
                                "type": "m.room.message",
                                "sender": "@u:s",
                                "event_id": "$f",
                                "origin_server_ts": 1700000001000,
                                "content": {"msgtype": "m.text", "body": "ok"},
                            },
                        ]
                    }
                }
            }
        }
    }
    messages = MatrixChannel.parse_sync(payload)
    assert len(messages) == 1
    assert messages[0].text == "ok"


def test_parse_sync_empty_payload_returns_empty():
    assert MatrixChannel.parse_sync({}) == []
    assert MatrixChannel.parse_sync({"rooms": {}}) == []
    assert MatrixChannel.parse_sync({"rooms": {"join": {}}}) == []


def test_parse_sync_handles_multiple_rooms():
    payload = {
        "rooms": {
            "join": {
                "!r1:s": {
                    "timeline": {
                        "events": [
                            {
                                "type": "m.room.message",
                                "sender": "@a:s",
                                "event_id": "$1",
                                "origin_server_ts": 1700000000000,
                                "content": {"msgtype": "m.text", "body": "from r1"},
                            }
                        ]
                    }
                },
                "!r2:s": {
                    "timeline": {
                        "events": [
                            {
                                "type": "m.room.message",
                                "sender": "@b:s",
                                "event_id": "$2",
                                "origin_server_ts": 1700000001000,
                                "content": {"msgtype": "m.text", "body": "from r2"},
                            }
                        ]
                    }
                },
            }
        }
    }
    messages = MatrixChannel.parse_sync(payload)
    assert len(messages) == 2
    rooms = {m.platform_chat_id for m in messages}
    assert rooms == {"!r1:s", "!r2:s"}


async def main() -> int:
    async_tests = [test_start_without_credentials_raises, test_send_without_credentials_raises]
    sync_tests = [
        test_parse_sync_extracts_text_messages,
        test_parse_sync_filters_non_text_events,
        test_parse_sync_empty_payload_returns_empty,
        test_parse_sync_handles_multiple_rooms,
    ]
    passed = 0
    for t in async_tests:
        await t()
        print(f"[OK] {t.__name__}")
        passed += 1
    for t in sync_tests:
        t()
        print(f"[OK] {t.__name__}")
        passed += 1
    total = len(async_tests) + len(sync_tests)
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
