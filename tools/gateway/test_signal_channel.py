"""Tests for the Signal channel adapter — parser + credential checks, no live daemon."""

import asyncio
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.signal_channel import SignalChannel
from tools.gateway.base import GatewayError, OutboundMessage


async def noop_handler(_):
    return None


async def test_start_without_account_raises():
    saved = os.environ.pop("SIGNAL_ACCOUNT", None)
    try:
        ch = SignalChannel(noop_handler, account="")
        raised = False
        try:
            await ch.start()
        except GatewayError:
            raised = True
        assert raised
        assert not ch.running
    finally:
        if saved is not None:
            os.environ["SIGNAL_ACCOUNT"] = saved


async def test_send_without_account_raises():
    ch = SignalChannel(noop_handler, account="")
    raised = False
    try:
        await ch.send(OutboundMessage(channel="signal", platform_chat_id="+15551234", text="hi"))
    except GatewayError:
        raised = True
    assert raised


def test_parse_envelope_direct_message():
    envelope = {
        "envelope": {
            "source": "+15551112222",
            "sourceName": "William",
            "timestamp": 1730000000000,
            "dataMessage": {"message": "hola desde signal"},
        }
    }
    msg = SignalChannel.parse_envelope(envelope)
    assert msg.channel == "signal"
    assert msg.platform_user_id == "+15551112222"
    assert msg.user_display_name == "William"
    assert msg.text == "hola desde signal"
    assert msg.platform_chat_id == "+15551112222"
    assert msg.metadata.get("group") is False
    assert msg.received_at.tzinfo is not None


def test_parse_envelope_group_message():
    envelope = {
        "envelope": {
            "source": "+15551112222",
            "timestamp": 1730000001000,
            "dataMessage": {
                "message": "hola al grupo",
                "groupInfo": {"groupId": "grp-abc-123"},
            },
        }
    }
    msg = SignalChannel.parse_envelope(envelope)
    assert msg.platform_chat_id == "grp-abc-123"
    assert msg.metadata.get("group") is True


def test_parse_envelope_handles_missing_fields():
    msg = SignalChannel.parse_envelope({"envelope": {}})
    assert msg.channel == "signal"
    assert msg.text == ""
    assert msg.received_at.tzinfo is not None


def test_parse_envelope_snake_case_aliases():
    """Some signal-cli builds use snake_case (data_message, group_info)."""
    envelope = {
        "envelope": {
            "source": "+15551112222",
            "source_name": "Henry",
            "timestamp": 1730000002000,
            "data_message": {
                "body": "snake case body",
                "group_info": {"group_id": "grp-xyz"},
            },
        }
    }
    msg = SignalChannel.parse_envelope(envelope)
    assert msg.text == "snake case body"
    assert msg.platform_chat_id == "grp-xyz"
    assert msg.user_display_name == "Henry"


async def main() -> int:
    async_tests = [test_start_without_account_raises, test_send_without_account_raises]
    sync_tests = [
        test_parse_envelope_direct_message,
        test_parse_envelope_group_message,
        test_parse_envelope_handles_missing_fields,
        test_parse_envelope_snake_case_aliases,
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
