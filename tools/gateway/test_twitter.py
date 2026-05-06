"""Tests for the Twitter/X adapter — parser + credential checks, no live API."""

import asyncio
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.twitter import TwitterChannel
from tools.gateway.base import GatewayError, OutboundMessage


async def noop_handler(_):
    return None


async def test_start_without_credentials_raises():
    saved_b = os.environ.pop("TWITTER_BEARER_TOKEN", None)
    saved_u = os.environ.pop("TWITTER_USER_ID", None)
    try:
        ch = TwitterChannel(noop_handler, bearer_token="", user_id="")
        raised = False
        try:
            await ch.start()
        except GatewayError:
            raised = True
        assert raised
        assert not ch.running
    finally:
        if saved_b is not None:
            os.environ["TWITTER_BEARER_TOKEN"] = saved_b
        if saved_u is not None:
            os.environ["TWITTER_USER_ID"] = saved_u


async def test_send_without_user_oauth_token_raises():
    saved = os.environ.pop("TWITTER_USER_OAUTH_TOKEN", None)
    try:
        ch = TwitterChannel(
            noop_handler,
            bearer_token="b",
            user_id="123",
            user_oauth_token="",
        )
        raised = False
        try:
            await ch.send(OutboundMessage(channel="twitter", platform_chat_id="conv-1", text="hola"))
        except GatewayError:
            raised = True
        assert raised
    finally:
        if saved is not None:
            os.environ["TWITTER_USER_OAUTH_TOKEN"] = saved


def test_parse_mentions_basic():
    payload = {
        "data": [
            {
                "id": "tweet-1",
                "text": "@seal hola jarvis",
                "author_id": "u-42",
                "conversation_id": "conv-1",
                "created_at": "2026-04-30T20:00:00.000Z",
                "in_reply_to_user_id": None,
            }
        ],
        "includes": {
            "users": [
                {"id": "u-42", "username": "william", "name": "William"},
            ]
        },
        "meta": {"newest_id": "tweet-1", "result_count": 1},
    }
    msgs = TwitterChannel.parse_mentions(payload)
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.channel == "twitter"
    assert msg.platform_user_id == "u-42"
    assert msg.platform_chat_id == "conv-1"
    assert msg.text == "@seal hola jarvis"
    assert msg.platform_message_id == "tweet-1"
    assert msg.user_display_name == "William"
    assert msg.metadata.get("username") == "william"
    assert msg.received_at.tzinfo is not None


def test_parse_mentions_handles_missing_user_in_includes():
    """If author isn't in includes.users, parsing must still produce an InboundMessage."""
    payload = {
        "data": [
            {
                "id": "tweet-2",
                "text": "lonely tweet",
                "author_id": "u-orphan",
                "conversation_id": "conv-2",
                "created_at": "2026-04-30T20:01:00Z",
            }
        ],
        "meta": {"newest_id": "tweet-2"},
    }
    msgs = TwitterChannel.parse_mentions(payload)
    assert len(msgs) == 1
    assert msgs[0].user_display_name is None
    assert msgs[0].platform_user_id == "u-orphan"


def test_parse_mentions_falls_back_to_tweet_id_when_no_conversation():
    payload = {
        "data": [
            {
                "id": "tweet-3",
                "text": "no conv",
                "author_id": "u-3",
                "created_at": "2026-04-30T20:02:00Z",
            }
        ]
    }
    msgs = TwitterChannel.parse_mentions(payload)
    assert msgs[0].platform_chat_id == "tweet-3"


def test_parse_mentions_empty_payload():
    assert TwitterChannel.parse_mentions({}) == []
    assert TwitterChannel.parse_mentions({"data": []}) == []


def test_parse_mentions_handles_invalid_created_at():
    payload = {
        "data": [
            {
                "id": "tweet-4",
                "text": "bad ts",
                "author_id": "u-4",
                "conversation_id": "c-4",
                "created_at": "not-a-date",
            }
        ]
    }
    msgs = TwitterChannel.parse_mentions(payload)
    assert len(msgs) == 1
    assert msgs[0].received_at.tzinfo is not None


def test_parse_mentions_multiple_tweets_preserves_order():
    payload = {
        "data": [
            {"id": "t1", "text": "first", "author_id": "u1", "conversation_id": "c1", "created_at": "2026-04-30T20:00:00Z"},
            {"id": "t2", "text": "second", "author_id": "u1", "conversation_id": "c1", "created_at": "2026-04-30T20:01:00Z"},
        ],
        "includes": {"users": [{"id": "u1", "username": "x", "name": "X"}]},
    }
    msgs = TwitterChannel.parse_mentions(payload)
    assert len(msgs) == 2
    assert [m.platform_message_id for m in msgs] == ["t1", "t2"]


async def main() -> int:
    async_tests = [
        test_start_without_credentials_raises,
        test_send_without_user_oauth_token_raises,
    ]
    sync_tests = [
        test_parse_mentions_basic,
        test_parse_mentions_handles_missing_user_in_includes,
        test_parse_mentions_falls_back_to_tweet_id_when_no_conversation,
        test_parse_mentions_empty_payload,
        test_parse_mentions_handles_invalid_created_at,
        test_parse_mentions_multiple_tweets_preserves_order,
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
