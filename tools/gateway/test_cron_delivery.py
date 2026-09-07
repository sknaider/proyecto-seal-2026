"""Tests for CronDeliveryHook — no live network required."""

import json
import sys
import pathlib
from io import BytesIO
from unittest.mock import MagicMock, patch
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.cron_delivery import (
    CronDeliveryHook,
    DeliveryConfig,
    DeliveryError,
    DeliveryResult,
    _deliver_discord,
    _deliver_telegram,
    _deliver_web_chat,
)


def _mock_urlopen(response_body: dict):
    payload = json.dumps(response_body).encode()
    mock = MagicMock()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    mock.read = MagicMock(return_value=payload)
    return mock


def test_deliver_web_chat_happy():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen({"id": "msg-1", "ok": True})):
        mid = _deliver_web_chat("test", agent="ADA")
    assert mid == "msg-1"


def test_deliver_web_chat_http_error():
    err = urllib.error.HTTPError("url", 500, "Server Error", {}, BytesIO(b"oops"))
    with patch("urllib.request.urlopen", side_effect=err):
        raised = False
        try:
            _deliver_web_chat("test", agent="ADA")
        except DeliveryError as e:
            raised = True
            assert "500" in str(e)
    assert raised


def test_deliver_telegram_happy():
    resp = {"result": {"message_id": 42}}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(resp)):
        mid = _deliver_telegram("msg", chat_id="123", token="tok")
    assert mid == "42"


def test_deliver_discord_happy():
    resp = {"id": "discord-99"}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(resp)):
        mid = _deliver_discord("msg", channel_id="ch1", token="tok")
    assert mid == "discord-99"


def test_hook_register_and_config():
    hook = CronDeliveryHook()
    hook.register("daily-brief", channels=["web_chat", "telegram"], agent="ADA")
    cfg = hook.config_for("daily-brief")
    assert cfg is not None
    assert cfg.channels == ["web_chat", "telegram"]
    assert cfg.agent == "ADA"


def test_hook_deliver_web_chat():
    hook = CronDeliveryHook.from_env()
    hook.register("test-cron", channels=["web_chat"], agent="NEXUS")
    with patch("urllib.request.urlopen", return_value=_mock_urlopen({"id": "m1", "ok": True})):
        results = hook.deliver("test-cron", "output content")
    assert len(results) == 1
    assert results[0].success
    assert results[0].channel == "web_chat"
    assert results[0].message_id == "m1"


def test_hook_deliver_unknown_cron_defaults_to_web_chat():
    hook = CronDeliveryHook.from_env()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen({"id": "x", "ok": True})):
        results = hook.deliver("unknown-cron", "some output")
    assert results[0].channel == "web_chat"
    assert results[0].success


def test_hook_deliver_channel_error_captured():
    hook = CronDeliveryHook.from_env()
    hook.register("fail-cron", channels=["web_chat"], agent="ADA")
    with patch("urllib.request.urlopen", side_effect=OSError("down")):
        results = hook.deliver("fail-cron", "output")
    assert not results[0].success
    assert results[0].error is not None


def test_hook_multi_channel_partial_failure():
    hook = CronDeliveryHook()
    hook._env = {
        "webchat_url": "http://localhost:8765",
        "telegram_token": "",
        "telegram_chat_id": "",
        "discord_token": "",
        "discord_channel_id": "",
    }
    hook.register("multi", channels=["web_chat", "telegram"], agent="JARVIS")

    call_count = [0]
    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return _mock_urlopen({"id": "wc-1", "ok": True})
        raise OSError("telegram down")

    with patch("urllib.request.urlopen", side_effect=side_effect):
        results = hook.deliver("multi", "content")

    assert results[0].success
    assert not results[1].success
    assert "TELEGRAM_BOT_TOKEN" in results[1].error


def test_hook_format_prefix():
    hook = CronDeliveryHook.from_env()
    hook.register("fmt-cron", channels=["web_chat"], agent="ADA", format_prefix="📋 Daily:")

    sent_texts = []
    def capture(req, timeout=10):
        body = json.loads(req.data.decode())
        sent_texts.append(body["message"])
        return _mock_urlopen({"id": "x", "ok": True})

    with patch("urllib.request.urlopen", side_effect=capture):
        hook.deliver("fmt-cron", "line one\nline two")

    assert sent_texts[0].startswith("📋 Daily:")
    assert "line one" in sent_texts[0]


def test_hook_mention_william():
    hook = CronDeliveryHook.from_env()
    hook.register("mention-cron", channels=["web_chat"], agent="ADA", mention_william=True)

    sent_texts = []
    def capture(req, timeout=10):
        body = json.loads(req.data.decode())
        sent_texts.append(body["message"])
        return _mock_urlopen({"id": "x", "ok": True})

    with patch("urllib.request.urlopen", side_effect=capture):
        hook.deliver("mention-cron", "urgent update")

    assert sent_texts[0].startswith("@William")


def test_hook_unsupported_channel_returns_error():
    hook = CronDeliveryHook.from_env()
    hook.register("bad", channels=["signal"], agent="ADA")
    results = hook.deliver("bad", "test")
    assert not results[0].success
    assert "unsupported channel" in results[0].error


def test_hook_whatsapp_missing_config_returns_error():
    hook = CronDeliveryHook.from_env()
    hook.register("wa-test", channels=["whatsapp"], agent="ADA")
    results = hook.deliver("wa-test", "hola William")
    assert not results[0].success
    assert "WHATSAPP_NUMBER" in results[0].error or "EVOLUTION" in results[0].error


def test_deliver_whatsapp_happy():
    from tools.gateway.cron_delivery import _deliver_whatsapp
    resp = {"key": {"id": "wa-msg-42"}}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(resp)):
        mid = _deliver_whatsapp("hola", number="51999999999", instance="seal", api_key="secret")
    assert mid == "wa-msg-42"


def test_hook_summary():
    hook = CronDeliveryHook.from_env()
    hook.register("c1", channels=["web_chat"])
    hook.register("c2", channels=["telegram"])
    s = hook.summary()
    assert s["registered_crons"] == 2
    assert "c1" in s["cron_ids"]
    assert "web_chat" in s["env_channels"]


def test_hook_registered_ids():
    hook = CronDeliveryHook()
    hook.register("alpha", channels=["web_chat"])
    hook.register("beta", channels=["discord"])
    assert set(hook.registered_ids()) == {"alpha", "beta"}


def main() -> int:
    tests = [
        test_deliver_web_chat_happy,
        test_deliver_web_chat_http_error,
        test_deliver_telegram_happy,
        test_deliver_discord_happy,
        test_hook_register_and_config,
        test_hook_deliver_web_chat,
        test_hook_deliver_unknown_cron_defaults_to_web_chat,
        test_hook_deliver_channel_error_captured,
        test_hook_multi_channel_partial_failure,
        test_hook_format_prefix,
        test_hook_mention_william,
        test_hook_unsupported_channel_returns_error,
        test_hook_whatsapp_missing_config_returns_error,
        test_deliver_whatsapp_happy,
        test_hook_summary,
        test_hook_registered_ids,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"[OK] {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
