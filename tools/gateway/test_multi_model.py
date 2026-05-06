"""Tests for the multi-model adapter layer — no live network required."""

import asyncio
import json
import sys
import pathlib
import urllib.error
import urllib.request
from io import BytesIO
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.multi_model import (
    ChatMessage,
    GeminiAdapter,
    LMStudioAdapter,
    ModelError,
    ModelResponse,
    MultiModelRouter,
    NvidiaNIMAdapter,
    OllamaAdapter,
    OpenAIAdapter,
    BedrockAdapter,
    XAIAdapter,
    _OpenAICompatAdapter,
    _sign_request,
    _derive_signing_key,
)


def _fake_response(content: str, model: str = "test-model") -> MagicMock:
    payload = json.dumps({
        "choices": [{"message": {"content": content}}],
        "model": model,
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }).encode()
    mock = MagicMock()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    mock.read = MagicMock(return_value=payload)
    return mock


MESSAGES = [ChatMessage(role="user", content="hola")]


def test_openai_compat_happy_path():
    adapter = _OpenAICompatAdapter("http://localhost/v1", "key", "test")
    with patch("urllib.request.urlopen", return_value=_fake_response("respuesta ok")):
        resp = adapter.complete(MESSAGES, model="m1")
    assert resp.content == "respuesta ok"
    assert resp.provider == "test"
    assert resp.prompt_tokens == 10
    assert resp.completion_tokens == 20


def test_openai_compat_http_error_raises():
    adapter = _OpenAICompatAdapter("http://localhost/v1", "key", "test")
    err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, BytesIO(b"bad key"))
    with patch("urllib.request.urlopen", side_effect=err):
        raised = False
        try:
            adapter.complete(MESSAGES, model="m1")
        except ModelError:
            raised = True
    assert raised


def test_openai_compat_os_error_raises():
    adapter = _OpenAICompatAdapter("http://localhost/v1", "key", "test")
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        raised = False
        try:
            adapter.complete(MESSAGES, model="m1")
        except ModelError:
            raised = True
    assert raised


def test_ollama_adapter_uses_default_model():
    adapter = OllamaAdapter(base_url="http://localhost:11434", model="qwen2.5:7b")
    with patch("urllib.request.urlopen", return_value=_fake_response("ok")) as mock_open:
        resp = adapter.complete(MESSAGES)
    assert resp.content == "ok"
    req_arg = mock_open.call_args[0][0]
    body = json.loads(req_arg.data.decode())
    assert body["model"] == "qwen2.5:7b"


def test_nvidia_nim_adapter_provider():
    adapter = NvidiaNIMAdapter(api_key="nvapi-fake")
    assert adapter.provider == "nvidia-nim"


def test_xai_adapter_provider():
    adapter = XAIAdapter(api_key="fake")
    assert adapter.provider == "xai"


def test_gemini_adapter_provider():
    adapter = GeminiAdapter(api_key="fake")
    assert adapter.provider == "gemini"


def test_gemini_uses_correct_base_url():
    adapter = GeminiAdapter(api_key="fake")
    assert "generativelanguage.googleapis.com" in adapter.base_url


def test_router_returns_first_success():
    a1 = OllamaAdapter()
    a2 = NvidiaNIMAdapter(api_key="fake")
    router = MultiModelRouter([a1, a2])
    with patch("urllib.request.urlopen", return_value=_fake_response("from ollama")):
        resp = router.complete(MESSAGES)
    assert resp.content == "from ollama"


def test_router_fallback_on_error():
    a1 = OllamaAdapter()
    a2 = XAIAdapter(api_key="fake")
    router = MultiModelRouter([a1, a2])

    call_count = [0]
    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise OSError("ollama down")
        return _fake_response("from xai")

    with patch("urllib.request.urlopen", side_effect=side_effect):
        resp = router.complete(MESSAGES)
    assert resp.content == "from xai"
    assert call_count[0] == 2


def test_router_all_fail_raises():
    a1 = OllamaAdapter()
    router = MultiModelRouter([a1])
    with patch("urllib.request.urlopen", side_effect=OSError("down")):
        raised = False
        try:
            router.complete(MESSAGES)
        except ModelError as e:
            raised = True
            assert "all adapters failed" in str(e)
    assert raised


def test_router_provider_hint_reorders():
    a1 = OllamaAdapter()
    a2 = XAIAdapter(api_key="fake")
    router = MultiModelRouter([a1, a2])

    calls: list[str] = []
    original_complete = _OpenAICompatAdapter.complete

    def recording_complete(self, messages, model=None, **kwargs):
        calls.append(self.provider)
        return ModelResponse(content="ok", model="m", provider=self.provider)

    with patch.object(_OpenAICompatAdapter, "complete", recording_complete):
        router.complete(MESSAGES, provider="xai")

    assert calls[0] == "xai"


def test_router_from_env_always_includes_ollama():
    import os
    with patch.dict(os.environ, {}, clear=False):
        router = MultiModelRouter.from_env()
    assert "ollama" in router.providers


def test_router_providers_list():
    a1 = OllamaAdapter()
    a2 = GeminiAdapter(api_key="k")
    router = MultiModelRouter([a1, a2])
    assert router.providers == ["ollama", "gemini"]


def _fake_bedrock_response(text: str = "bedrock answer", model: str = "claude-3") -> MagicMock:
    payload = json.dumps({
        "content": [{"type": "text", "text": text}],
        "model": model,
        "usage": {"input_tokens": 5, "output_tokens": 10},
    }).encode()
    mock = MagicMock()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    mock.read = MagicMock(return_value=payload)
    return mock


def test_openai_adapter_provider():
    adapter = OpenAIAdapter(api_key="sk-fake")
    assert adapter.provider == "openai"


def test_openai_adapter_uses_openai_base_url():
    adapter = OpenAIAdapter(api_key="sk-fake")
    assert "api.openai.com" in adapter.base_url


def test_openai_adapter_no_key_still_constructs():
    adapter = OpenAIAdapter(api_key="")
    assert adapter.provider == "openai"


def test_openai_adapter_happy_path():
    adapter = OpenAIAdapter(api_key="sk-fake")
    with patch("urllib.request.urlopen", return_value=_fake_response("gpt answer")):
        resp = adapter.complete(MESSAGES, model="gpt-4o-mini")
    assert resp.content == "gpt answer"
    assert resp.provider == "openai"


def test_bedrock_adapter_provider():
    adapter = BedrockAdapter(access_key="AK", secret_key="SK")
    assert adapter.provider == "bedrock"


def test_bedrock_adapter_no_creds_raises():
    adapter = BedrockAdapter(access_key="", secret_key="")
    raised = False
    try:
        adapter.complete(MESSAGES, model="anthropic.claude-3-haiku-20240307-v1:0")
    except ModelError as e:
        raised = True
        assert "AWS_ACCESS_KEY_ID" in str(e)
    assert raised


def test_bedrock_adapter_available_false_without_creds():
    adapter = BedrockAdapter(access_key="", secret_key="")
    assert not adapter.available()


def test_bedrock_adapter_available_true_with_creds():
    adapter = BedrockAdapter(access_key="AK", secret_key="SK")
    assert adapter.available()


def test_bedrock_adapter_happy_path():
    adapter = BedrockAdapter(access_key="AKID", secret_key="SKID", region="us-east-1")
    with patch("urllib.request.urlopen", return_value=_fake_bedrock_response("answer")) as mock_open:
        resp = adapter.complete(MESSAGES, model="anthropic.claude-3-haiku-20240307-v1:0")
    assert resp.content == "answer"
    assert resp.provider == "bedrock"
    assert resp.prompt_tokens == 5
    assert resp.completion_tokens == 10
    # Verify Authorization header was set
    req = mock_open.call_args[0][0]
    assert "AWS4-HMAC-SHA256" in req.get_header("Authorization")


def test_bedrock_adapter_http_error_raises():
    adapter = BedrockAdapter(access_key="AK", secret_key="SK")
    err = urllib.error.HTTPError("url", 403, "Forbidden", {}, BytesIO(b"denied"))
    with patch("urllib.request.urlopen", side_effect=err):
        raised = False
        try:
            adapter.complete(MESSAGES)
        except ModelError as e:
            raised = True
            assert "403" in str(e)
    assert raised


def test_sigv4_produces_authorization_header():
    headers = _sign_request(
        "POST",
        "https://bedrock-runtime.us-east-1.amazonaws.com/model/anthropic.claude-3-haiku/invoke",
        b'{"test": true}',
        "AKIDTEST",
        "SKTEST",
        "us-east-1",
        service="bedrock",
    )
    assert "AWS4-HMAC-SHA256" in headers["Authorization"]
    assert "AKIDTEST" in headers["Authorization"]
    assert "X-Amz-Date" in headers
    assert len(headers["X-Amz-Date"]) == 16  # YYYYMMDDTHHMMSSz


def test_sigv4_session_token_included():
    headers = _sign_request(
        "POST", "https://bedrock-runtime.us-east-1.amazonaws.com/model/test/invoke",
        b"body", "AK", "SK", "us-east-1", session_token="SESS-TOKEN",
    )
    assert "X-Amz-Security-Token" in headers
    assert headers["X-Amz-Security-Token"] == "SESS-TOKEN"


def test_router_from_env_includes_openai_when_key_set():
    import os
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-fake"}):
        router = MultiModelRouter.from_env()
    assert "openai" in router.providers


def test_router_from_env_includes_bedrock_when_keys_set():
    import os
    with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "AK", "AWS_SECRET_ACCESS_KEY": "SK"}):
        router = MultiModelRouter.from_env()
    assert "bedrock" in router.providers


def main() -> int:
    tests = [
        test_openai_compat_happy_path,
        test_openai_compat_http_error_raises,
        test_openai_compat_os_error_raises,
        test_ollama_adapter_uses_default_model,
        test_nvidia_nim_adapter_provider,
        test_xai_adapter_provider,
        test_gemini_adapter_provider,
        test_gemini_uses_correct_base_url,
        test_router_returns_first_success,
        test_router_fallback_on_error,
        test_router_all_fail_raises,
        test_router_provider_hint_reorders,
        test_router_from_env_always_includes_ollama,
        test_router_providers_list,
        test_openai_adapter_provider,
        test_openai_adapter_uses_openai_base_url,
        test_openai_adapter_no_key_still_constructs,
        test_openai_adapter_happy_path,
        test_bedrock_adapter_provider,
        test_bedrock_adapter_no_creds_raises,
        test_bedrock_adapter_available_false_without_creds,
        test_bedrock_adapter_available_true_with_creds,
        test_bedrock_adapter_happy_path,
        test_bedrock_adapter_http_error_raises,
        test_sigv4_produces_authorization_header,
        test_sigv4_session_token_included,
        test_router_from_env_includes_openai_when_key_set,
        test_router_from_env_includes_bedrock_when_keys_set,
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
