"""
Companion agent orchestrator.
Fallback chain: Anthropic Claude (+ MCP tools) → Ollama → stub
"""
import re
from typing import Optional, Callable, Awaitable, Any

try:
    from anthropic import AsyncAnthropic
except ImportError:  # anthropic not installed → only Ollama/stub available
    AsyncAnthropic = None  # type: ignore[assignment,misc]

_STUB = "[No AI backend available — configure API key in Settings or start Ollama]"
_LONE_SURROGATE = re.compile(r'[\ud800-\udfff]')


def _sanitize(text: str) -> str:
    """Replace lone Unicode surrogates — they make JSON invalid (HTTP 400 from Anthropic)."""
    return _LONE_SURROGATE.sub('�', text)


def _clean_messages(messages: list[dict]) -> list[dict]:
    """Return messages with all string content sanitized of lone surrogates."""
    out = []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            out.append({**m, "content": _sanitize(content)})
        else:
            out.append(m)
    return out


# ── Low-level calls ──────────────────────────────────────────────────────────

async def call_claude(
    messages: list[dict],
    api_key: str,
    model: str = "claude-haiku-4-5-20251001",
    system: str = "",
    max_tokens: int = 2048,
) -> str:
    if AsyncAnthropic is None:
        raise RuntimeError("anthropic package not installed")
    client = AsyncAnthropic(api_key=api_key)
    kwargs: dict = dict(model=model, max_tokens=max_tokens, messages=_clean_messages(messages))
    if system:
        kwargs["system"] = _sanitize(system)
    response = await client.messages.create(**kwargs)
    return response.content[0].text


async def call_claude_with_tools(
    messages: list[dict],
    api_key: str,
    model: str,
    system: str,
    tools: list[dict],
    tool_executor: Callable[[str, dict], Awaitable[Any]],
    max_tokens: int = 4096,
    max_rounds: int = 5,
) -> str:
    """Claude with MCP tool use. Loops until text stop or max_rounds."""
    if AsyncAnthropic is None:
        raise RuntimeError("anthropic package not installed")
    ac = AsyncAnthropic(api_key=api_key)
    current_messages: list = list(messages)

    for _ in range(max_rounds):
        kwargs: dict = dict(
            model=model, max_tokens=max_tokens,
            messages=_clean_messages(current_messages), tools=tools,
        )
        if system:
            kwargs["system"] = _sanitize(system)
        response = await ac.messages.create(**kwargs)

        if response.stop_reason != "tool_use":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        # Build tool results
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                try:
                    raw = await tool_executor(block.name, block.input)
                    content = raw if isinstance(raw, str) else str(raw)
                except Exception as exc:
                    content = f"Tool error: {exc}"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                })

        # Append assistant turn + tool results (in-memory only, not persisted)
        current_messages.append({"role": "assistant", "content": response.content})
        current_messages.append({"role": "user", "content": tool_results})

    return "[Max tool call rounds reached]"


async def stream_claude(
    messages: list[dict],
    api_key: str,
    model: str = "claude-haiku-4-5-20251001",
    system: str = "",
    max_tokens: int = 2048,
):
    """Async generator — yields text chunks from Claude streaming API."""
    if AsyncAnthropic is None:
        yield "[anthropic package not installed]"
        return
    ac = AsyncAnthropic(api_key=api_key)
    kwargs: dict = dict(model=model, max_tokens=max_tokens, messages=_clean_messages(messages))
    if system:
        kwargs["system"] = _sanitize(system)
    async with ac.messages.stream(**kwargs) as stream:
        async for text in stream.text_stream:
            yield text


async def call_ollama(prompt: str, model: str) -> Optional[str]:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as hx:
            r = await hx.post("http://localhost:11434/api/generate", json={
                "model": model, "prompt": prompt, "stream": False
            })
            if r.status_code == 200:
                return r.json().get("response")
    except Exception:
        pass
    return None


# ── High-level entry point ───────────────────────────────────────────────────

async def get_reply(
    thread_history: list[dict],
    new_content: str,
    api_key: Optional[str],
    model: Optional[str],
    user_name: str = "",
    tools: Optional[list[dict]] = None,
    tool_executor: Optional[Callable[[str, dict], Awaitable[Any]]] = None,
    system: Optional[str] = None,
) -> str:
    """
    Return assistant reply for a chat turn.

    thread_history: [{"role": "user"|"assistant", "content": str}, ...]
    tools: Anthropic-format tool definitions (from MCP cache)
    tool_executor: async callable(tool_name, tool_input) → result
    system: override system prompt (default builds from user_name)
    """
    messages = [*thread_history, {"role": "user", "content": new_content}]
    if system is None:
        system = (
            f"You are a personal AI companion. The user's name is {user_name}."
            if user_name else "You are a personal AI companion."
        )

    if api_key:
        chosen_model = model or "claude-haiku-4-5-20251001"
        try:
            if tools and tool_executor:
                return await call_claude_with_tools(
                    messages, api_key, chosen_model, system,
                    tools, tool_executor,
                )
            return await call_claude(messages, api_key, model=chosen_model, system=system)
        except Exception:
            pass

    # Ollama fallback (no tool support, no history in generate API)
    ollama_model = model or "qwen2.5:7b"
    reply = await call_ollama(new_content, ollama_model)
    if reply:
        return reply

    return _STUB
