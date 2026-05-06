"""Tests for the Bluesky adapter — parser + credential checks, no live PDS."""

import asyncio
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.bluesky import BlueskyChannel
from tools.gateway.base import GatewayError, OutboundMessage


async def noop_handler(_):
    return None


async def test_start_without_credentials_raises():
    saved_i = os.environ.pop("BLUESKY_IDENTIFIER", None)
    saved_p = os.environ.pop("BLUESKY_PASSWORD", None)
    try:
        ch = BlueskyChannel(noop_handler, identifier="", password="")
        raised = False
        try:
            await ch.start()
        except GatewayError:
            raised = True
        assert raised
        assert not ch.running
    finally:
        if saved_i is not None:
            os.environ["BLUESKY_IDENTIFIER"] = saved_i
        if saved_p is not None:
            os.environ["BLUESKY_PASSWORD"] = saved_p


async def test_send_without_session_raises():
    ch = BlueskyChannel(noop_handler, identifier="x", password="y")
    raised = False
    try:
        await ch.send(OutboundMessage(channel="bluesky", platform_chat_id="", text="hi"))
    except GatewayError:
        raised = True
    assert raised


def test_parse_notifications_basic():
    payload = {
        "notifications": [
            {
                "uri": "at://did:plc:abc/app.bsky.feed.post/123",
                "reason": "mention",
                "indexedAt": "2026-04-30T20:00:00Z",
                "author": {
                    "did": "did:plc:abc",
                    "handle": "william.bsky.social",
                    "displayName": "William",
                },
                "record": {"text": "@seal hola"},
            }
        ]
    }
    msgs = BlueskyChannel.parse_notifications(payload, since=None)
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.channel == "bluesky"
    assert msg.platform_user_id == "did:plc:abc"
    assert msg.text == "@seal hola"
    assert msg.user_display_name == "William"
    assert msg.platform_message_id == "at://did:plc:abc/app.bsky.feed.post/123"
    assert msg.metadata.get("reason") == "mention"
    assert msg.received_at.tzinfo is not None


def test_parse_notifications_filters_non_mentions():
    payload = {
        "notifications": [
            {
                "uri": "at://did:plc:x/app.bsky.feed.like/1",
                "reason": "like",
                "indexedAt": "2026-04-30T20:00:00Z",
                "author": {"did": "did:plc:x"},
                "record": {},
            },
            {
                "uri": "at://did:plc:y/app.bsky.feed.post/2",
                "reason": "reply",
                "indexedAt": "2026-04-30T20:01:00Z",
                "author": {"did": "did:plc:y"},
                "record": {"text": "reply text"},
            },
        ]
    }
    msgs = BlueskyChannel.parse_notifications(payload, since=None)
    assert len(msgs) == 1
    assert msgs[0].text == "reply text"
    assert msgs[0].metadata.get("reason") == "reply"


def test_parse_notifications_respects_since_cursor():
    payload = {
        "notifications": [
            {
                "uri": "at://x/1",
                "reason": "mention",
                "indexedAt": "2026-04-30T19:00:00Z",
                "author": {"did": "did:plc:a"},
                "record": {"text": "old"},
            },
            {
                "uri": "at://x/2",
                "reason": "mention",
                "indexedAt": "2026-04-30T21:00:00Z",
                "author": {"did": "did:plc:a"},
                "record": {"text": "new"},
            },
        ]
    }
    msgs = BlueskyChannel.parse_notifications(payload, since="2026-04-30T20:00:00Z")
    assert len(msgs) == 1
    assert msgs[0].text == "new"


def test_parse_notifications_empty_payload():
    assert BlueskyChannel.parse_notifications({}, since=None) == []
    assert BlueskyChannel.parse_notifications({"notifications": []}, since=None) == []


def test_latest_indexed_at_finds_max():
    payload = {
        "notifications": [
            {"indexedAt": "2026-04-30T19:00:00Z"},
            {"indexedAt": "2026-04-30T21:00:00Z"},
            {"indexedAt": "2026-04-30T20:00:00Z"},
        ]
    }
    assert BlueskyChannel._latest_indexed_at(payload) == "2026-04-30T21:00:00Z"


def test_latest_indexed_at_empty_returns_none():
    assert BlueskyChannel._latest_indexed_at({}) is None
    assert BlueskyChannel._latest_indexed_at({"notifications": []}) is None


def test_parse_notifications_handles_invalid_indexed_at():
    payload = {
        "notifications": [
            {
                "uri": "at://x/3",
                "reason": "mention",
                "indexedAt": "not-a-date",
                "author": {"did": "did:plc:z"},
                "record": {"text": "still works"},
            }
        ]
    }
    msgs = BlueskyChannel.parse_notifications(payload, since=None)
    assert len(msgs) == 1
    assert msgs[0].received_at.tzinfo is not None


async def main() -> int:
    async_tests = [test_start_without_credentials_raises, test_send_without_session_raises]
    sync_tests = [
        test_parse_notifications_basic,
        test_parse_notifications_filters_non_mentions,
        test_parse_notifications_respects_since_cursor,
        test_parse_notifications_empty_payload,
        test_latest_indexed_at_finds_max,
        test_latest_indexed_at_empty_returns_none,
        test_parse_notifications_handles_invalid_indexed_at,
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
