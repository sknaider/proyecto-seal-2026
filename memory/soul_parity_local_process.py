#!/usr/bin/env python3
"""Run one local-runtime parity event in a distinct process via llama.cpp."""
from __future__ import annotations

import asyncio
import json
import os
import sys

from soul_runtime_orchestrator import SoulRuntimeOrchestrator, http_json


async def run() -> dict:
    request = json.loads(sys.stdin.read())
    agent = str(request["agent"]).upper()
    native_event = str(request["native_event"])
    payload = dict(request["payload"])
    payload["hook_event_name"] = native_event
    dsn = os.environ.get("SEAL_PG_DSN", "")
    os.environ["SOUL_PARITY_RUNTIME_PID"] = str(os.getpid())
    orchestrator = SoulRuntimeOrchestrator(
        agent,
        runtime="local_llama",
        restricted_dsn=dsn,
        session_id=str(request["session_id"]),
    )
    # The candidate must traverse the actual llama.cpp inference endpoint; an
    # adapter-only call is not accepted as a second runtime.
    status, response = await asyncio.to_thread(
        http_json,
        orchestrator.llm_url,
        {
            "model": orchestrator.model,
            "messages": [{"role": "user", "content": "Reply exactly PARITY_LOCAL_OK."}],
            "stream": False,
            "max_tokens": 16,
            "temperature": 0.0,
        },
        120.0,
    )
    if status != 200:
        raise RuntimeError(f"llama.cpp returned HTTP {status}")
    answer = response["choices"][0]["message"]["content"]
    if not answer.strip():
        raise RuntimeError("llama.cpp returned an empty response")
    result = await orchestrator.emit(native_event, payload)
    return {
        "pid": os.getpid(),
        "llm_effect": True,
        "native_event": native_event,
        "all_ran": result["all_ran"],
        "effects": result["effects"],
    }


def main() -> int:
    try:
        print(json.dumps(asyncio.run(run()), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
