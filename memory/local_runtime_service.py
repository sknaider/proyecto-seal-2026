#!/usr/bin/env python3
"""Gemma 4 local runtime wrapper for SEAL continuous awareness.

This wraps the existing llama.cpp/OpenAI-compatible server. It does not launch
or manage a daemon; Fase 3 contract only verifies health and produces bounded
classification/reflection proposals.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


DEFAULT_ENDPOINT = os.environ.get("SEAL_LOCAL_RUNTIME_ENDPOINT", "http://127.0.0.1:8899")
DEFAULT_MODEL = os.environ.get("SEAL_LOCAL_RUNTIME_MODEL", "gemma4-dum")
ALLOWED_TASKS = {"classify", "summarize", "reflect"}
ALLOWED_ACTIONS = {"ignore", "store_only", "reflex_action", "local_reflect", "wake_codex", "wake_nexus", "ask_william"}


@dataclass(frozen=True)
class LocalRuntimeConfig:
    endpoint: str = DEFAULT_ENDPOINT
    model: str = DEFAULT_MODEL
    provider: str = "llama_cpp"
    timeout_seconds: float = 5.0
    max_tokens: int = 128
    temperature: float = 0.0


@dataclass(frozen=True)
class LocalRuntimeResult:
    ok: bool
    task: str
    model: str
    latency_ms: float
    output: dict[str, Any]
    raw_text: str = ""
    error: str = ""
    degraded: bool = False
    boundary: str = "proposal_only_no_side_effects"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Transport = Callable[[str, dict[str, Any] | None, float], dict[str, Any]]


def http_json(url: str, payload: dict[str, Any] | None, timeout_seconds: float) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8", errors="replace")
    return json.loads(body)


def runtime_health(config: LocalRuntimeConfig = LocalRuntimeConfig(), transport: Transport = http_json) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        payload = transport(f"{config.endpoint.rstrip('/')}/v1/models", None, config.timeout_seconds)
        model_ids = [str(item.get("id") or item.get("model") or item.get("name")) for item in payload.get("data", [])]
        if not model_ids and isinstance(payload.get("models"), list):
            model_ids = [str(item.get("model") or item.get("name") or item.get("id")) for item in payload["models"]]
        return {
            "ok": config.model in model_ids,
            "provider": config.provider,
            "endpoint": config.endpoint,
            "model": config.model,
            "model_ids": model_ids,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": config.provider,
            "endpoint": config.endpoint,
            "model": config.model,
            "model_ids": [],
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": str(exc),
        }


def _messages_for_task(task: str, context: str) -> list[dict[str, str]]:
    if task == "classify":
        instruction = (
            "Return only compact JSON. Classify the SEAL event into one action from "
            f"{sorted(ALLOWED_ACTIONS)} and include confidence 0..1 plus reason."
        )
    elif task == "summarize":
        instruction = "Return only compact JSON with keys summary, risk, recommended_action. Keep summary under 40 words."
    elif task == "reflect":
        instruction = (
            "Return only compact JSON with keys summary, risk, recommended_action, escalate. "
            "This is proposal-only; never claim execution."
        )
    else:
        raise ValueError(f"Unsupported local runtime task: {task}")
    return [
        {"role": "system", "content": instruction},
        {"role": "user", "content": context[:6000]},
    ]


def _extract_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            return str(message.get("content", ""))
        return str(choices[0].get("text", ""))
    return ""


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def parse_runtime_json(text: str, task: str) -> dict[str, Any]:
    cleaned = _strip_json_fence(text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return {
            "summary": cleaned[:240],
            "risk": "unknown",
            "recommended_action": "ask_william",
            "parse_error": True,
        }
    if not isinstance(parsed, dict):
        return {"summary": str(parsed)[:240], "risk": "unknown", "recommended_action": "ask_william", "parse_error": True}
    if task == "classify":
        action = str(parsed.get("action", "ask_william"))
        if action not in ALLOWED_ACTIONS:
            action = "ask_william"
        parsed["action"] = action
        parsed["confidence"] = max(0.0, min(1.0, float(parsed.get("confidence", 0.0) or 0.0)))
    parsed.setdefault("risk", "low")
    parsed.setdefault("recommended_action", parsed.get("action", "store_only"))
    return parsed


def _fallback(task: str, context: str, started: float, config: LocalRuntimeConfig, error: str) -> LocalRuntimeResult:
    lowered = context.lower()
    if "drop table" in lowered or "delete from" in lowered or "rm -rf" in lowered:
        output = {"action": "reflex_action", "confidence": 1.0, "risk": "critical", "recommended_action": "block_execution"}
    elif "failed" in lowered or "caido" in lowered or "caído" in lowered:
        output = {"action": "reflex_action", "confidence": 0.8, "risk": "high", "recommended_action": "capture_evidence"}
    else:
        output = {"action": "store_only", "confidence": 0.5, "risk": "low", "recommended_action": "store_only"}
    return LocalRuntimeResult(
        ok=False,
        task=task,
        model=config.model,
        latency_ms=round((time.perf_counter() - started) * 1000, 3),
        output=output,
        error=error,
        degraded=True,
    )


def run_local_task(
    task: str,
    context: str,
    config: LocalRuntimeConfig = LocalRuntimeConfig(),
    transport: Transport = http_json,
) -> LocalRuntimeResult:
    if task not in ALLOWED_TASKS:
        raise ValueError(f"Unsupported local runtime task: {task}")
    started = time.perf_counter()
    payload = {
        "model": config.model,
        "messages": _messages_for_task(task, context),
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "stream": False,
    }
    try:
        response = transport(f"{config.endpoint.rstrip('/')}/v1/chat/completions", payload, config.timeout_seconds)
        text = _extract_text(response)
        parsed = parse_runtime_json(text, task)
        return LocalRuntimeResult(
            ok=True,
            task=task,
            model=str(response.get("model") or config.model),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            output=parsed,
            raw_text=text[:1000],
        )
    except Exception as exc:
        return _fallback(task, context, started, config, str(exc))


def classify(context: str, config: LocalRuntimeConfig = LocalRuntimeConfig(), transport: Transport = http_json) -> LocalRuntimeResult:
    return run_local_task("classify", context, config, transport)


def summarize(context: str, config: LocalRuntimeConfig = LocalRuntimeConfig(), transport: Transport = http_json) -> LocalRuntimeResult:
    return run_local_task("summarize", context, config, transport)


def reflect(context: str, config: LocalRuntimeConfig = LocalRuntimeConfig(), transport: Transport = http_json) -> LocalRuntimeResult:
    return run_local_task("reflect", context, config, transport)


def evaluate_local_runtime_contract(config: LocalRuntimeConfig = LocalRuntimeConfig()) -> dict[str, Any]:
    health = runtime_health(config)

    def fake_transport(url: str, payload: dict[str, Any] | None, timeout_seconds: float) -> dict[str, Any]:
        if url.endswith("/v1/models"):
            return {"data": [{"id": config.model, "object": "model"}]}
        assert payload is not None
        return {
            "model": config.model,
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "reflex_action",
                                "confidence": 0.91,
                                "summary": "service failure requires evidence capture",
                                "risk": "high",
                                "recommended_action": "capture_evidence",
                                "escalate": False,
                            }
                        )
                    }
                }
            ],
        }

    fake_health = runtime_health(config, fake_transport)
    classified = classify("seal-chat.service failed", config, fake_transport)
    reflected = reflect("William asked ADA to continue local runtime contract", config, fake_transport)
    fallback = classify("DROP TABLE soul_v3.memories", config, lambda *_: (_ for _ in ()).throw(RuntimeError("down")))
    checks = {
        "live_models_endpoint_reachable": bool(health.get("ok")),
        "live_model_is_gemma4": config.model in health.get("model_ids", []),
        "fake_health_contract_ok": fake_health["ok"] is True,
        "classify_schema_ok": classified.ok and classified.output.get("action") in ALLOWED_ACTIONS,
        "reflect_schema_ok": reflected.ok and "recommended_action" in reflected.output,
        "fallback_blocks_destructive": fallback.degraded and fallback.output.get("recommended_action") == "block_execution",
        "proposal_only_boundary": classified.boundary == "proposal_only_no_side_effects",
    }
    return {
        "checks": checks,
        "passed": sum(1 for ok in checks.values() if ok),
        "total": len(checks),
        "health": health,
        "fake_health": fake_health,
        "classified": classified.to_dict(),
        "reflected": reflected.to_dict(),
        "fallback": fallback.to_dict(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL Gemma 4 local runtime wrapper")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    for name in sorted(ALLOWED_TASKS):
        task = sub.add_parser(name)
        task.add_argument("--context", required=True)
    sub.add_parser("contract")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = LocalRuntimeConfig(endpoint=args.endpoint, model=args.model)
    if args.command == "health":
        payload = runtime_health(config)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload.get("ok") else 2
    if args.command in ALLOWED_TASKS:
        result = run_local_task(args.command, args.context, config)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.ok or result.degraded else 2
    if args.command == "contract":
        payload = evaluate_local_runtime_contract(config)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["passed"] == payload["total"] else 2
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

