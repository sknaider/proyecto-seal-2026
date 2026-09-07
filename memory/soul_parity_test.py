#!/usr/bin/env python3
"""F3 parity harness bound to runtime identity and concrete effect evidence.

The harness never treats a subprocess return code as an effect.  Every event gets a
fresh nonce and token; the verifier must find the matching artifact for the exact
runtime/run/event/hook tuple.  Baseline and candidate use separate verifiers so a
baseline row cannot accredit a no-op candidate.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import inspect
from pathlib import Path
import secrets
from typing import Awaitable, Callable, Iterable, Mapping, Sequence

from soul_event_interface import LEARNING_EVENTS, SOUL_EVENTS, HookRegistration, hooks_for, parity_report


@dataclass(frozen=True)
class EvidenceKey:
    suite_run_id: str
    runtime_id: str
    soul_event: str
    script_path: str
    agent: str
    nonce: str
    token: str
    script_sha256: str = ""
    native_event: str = ""
    runtime_pid: int = 0


@dataclass(frozen=True)
class FireReceipt:
    suite_run_id: str
    runtime_id: str
    soul_event: str
    entrypoint: str
    native_event: str
    process_pid: int


@dataclass(frozen=True)
class EffectRun:
    runtime_id: str
    suite_run_id: str
    expected: dict[str, list[str]]
    observed: dict[str, list[str]]
    extras: dict[str, list[str]]
    receipts: dict[str, FireReceipt]
    script_sha256: dict[str, dict[str, str]]


AsyncFireFn = Callable[[EvidenceKey, dict], Awaitable[FireReceipt]]
AsyncEffectVerifier = Callable[[EvidenceKey], Awaitable[bool]]
AsyncEffectReader = Callable[[str, str, str, str], Awaitable[Iterable[str]]]


def _expected_by_event(
    agent: str,
    registry: Sequence[HookRegistration],
    payloads: Mapping[str, dict],
    events: Sequence[str],
) -> dict[str, list[str]]:
    return {
        event: [hook.script_path for hook in hooks_for(registry, event, agent, payloads.get(event, {}))]
        for event in events
    }


def _script_sha256(script_path: str) -> str:
    path = Path(script_path)
    material = path.read_bytes() if path.is_file() else script_path.encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _validate_receipt(receipt: FireReceipt, key: EvidenceKey) -> None:
    if not isinstance(receipt, FireReceipt):
        raise TypeError("fire_fn must return FireReceipt")
    if (
        receipt.suite_run_id != key.suite_run_id
        or receipt.runtime_id != key.runtime_id
        or receipt.soul_event != key.soul_event
        or not receipt.entrypoint.strip()
        or not receipt.native_event.strip()
        or receipt.process_pid <= 1
    ):
        raise RuntimeError(f"fire receipt does not match evidence key: {receipt!r}")


async def acollect_effects(
    agent: str,
    registry: Iterable[HookRegistration],
    runtime_id: str,
    suite_run_id: str,
    afire_fn: AsyncFireFn,
    aeffect_verifier: AsyncEffectVerifier,
    aeffect_reader: AsyncEffectReader,
    payloads: Mapping[str, dict] | None = None,
    *,
    events: Sequence[str] = SOUL_EVENTS,
    timeout_seconds: float = 30.0,
) -> EffectRun:
    """Fire events and enumerate exact artifacts for one runtime.

    The payload is deep-copied per runtime/event.  The reader is mandatory: checking
    only expected hooks would make unexpected effects invisible.
    """
    normalized_agent = agent.strip().upper()
    rows = list(registry)
    supplied_payloads = payloads or {}
    expected = _expected_by_event(normalized_agent, rows, supplied_payloads, events)
    script_sha256 = {
        event: {path: _script_sha256(path) for path in paths}
        for event, paths in expected.items()
    }
    observed: dict[str, list[str]] = {}
    extras: dict[str, list[str]] = {}
    receipts: dict[str, FireReceipt] = {}

    for event in events:
        nonce = secrets.token_hex(16)
        token = secrets.token_hex(32)
        marker_script = expected[event][0] if expected[event] else ""
        key = EvidenceKey(
            suite_run_id=suite_run_id,
            runtime_id=runtime_id,
            soul_event=event,
            script_path=marker_script,
            agent=normalized_agent,
            nonce=nonce,
            token=token,
            script_sha256=script_sha256[event].get(marker_script, ""),
        )
        payload = deepcopy(dict(supplied_payloads.get(event, {})))
        payload["soul_parity"] = {
            "suite_run_id": suite_run_id,
            "runtime_id": runtime_id,
            "soul_event": event,
            "nonce": nonce,
            "token": token,
        }
        receipt = await asyncio.wait_for(afire_fn(key, payload), timeout=timeout_seconds)
        _validate_receipt(receipt, key)
        receipts[event] = receipt
        verified_key = EvidenceKey(
            **{
                **key.__dict__,
                "native_event": receipt.native_event,
                "runtime_pid": receipt.process_pid,
            }
        )

        verified: list[str] = []
        for script_path in expected[event]:
            script_key = EvidenceKey(**{
                **verified_key.__dict__,
                "script_path": script_path,
                "script_sha256": script_sha256[event][script_path],
            })
            if await asyncio.wait_for(aeffect_verifier(script_key), timeout=timeout_seconds):
                verified.append(script_path)
        observed[event] = sorted(set(verified))

        concrete = set(
            await asyncio.wait_for(
                aeffect_reader(suite_run_id, runtime_id, event, normalized_agent),
                timeout=timeout_seconds,
            )
        )
        extras[event] = sorted(concrete - set(expected[event]))

    return EffectRun(runtime_id, suite_run_id, expected, observed, extras, receipts, script_sha256)


def _baseline_health(run: EffectRun, events: Sequence[str]) -> list[str]:
    broken: list[str] = []
    for event in events:
        if event not in LEARNING_EVENTS:
            continue
        if not run.expected.get(event) or set(run.observed.get(event, ())) != set(run.expected.get(event, ())):
            broken.append(event)
    return broken


def _finalize_parity(base: EffectRun, candidate: EffectRun, events: Sequence[str]) -> dict:
    if base.runtime_id == candidate.runtime_id:
        return {
            "parity": False,
            "learning_ok": False,
            "reason": "baseline and candidate identify the same runtime",
            "runtime_identity_collision": True,
        }
    shared_processes = {
        receipt.process_pid for receipt in base.receipts.values()
    } & {
        receipt.process_pid for receipt in candidate.receipts.values()
    }
    if shared_processes:
        return {
            "parity": False,
            "learning_ok": False,
            "reason": "baseline and candidate share a process identity",
            "runtime_identity_collision": True,
            "shared_process_identities": sorted(shared_processes),
        }

    baseline_broken = _baseline_health(base, events)
    digest_mismatch = {
        event: {
            "baseline": base.script_sha256.get(event, {}),
            "candidate": candidate.script_sha256.get(event, {}),
        }
        for event in events
        if base.script_sha256.get(event, {}) != candidate.script_sha256.get(event, {})
    }
    extras = {
        f"baseline:{event}": paths for event, paths in base.extras.items() if paths
    } | {
        f"candidate:{event}": paths for event, paths in candidate.extras.items() if paths
    }
    if baseline_broken or extras or digest_mismatch:
        return {
            "parity": False,
            "learning_ok": False,
            "baseline_broken": baseline_broken,
            "unexpected_effects": extras,
            "script_digest_mismatch": digest_mismatch,
            "reason": "baseline incomplete or unexpected effects observed",
            "baseline_effects": base.observed,
            "candidate_effects": candidate.observed,
        }

    report = parity_report(base.observed, candidate.observed)
    return {
        **report,
        "suite_run_id": base.suite_run_id,
        "baseline_broken": [],
        "unexpected_effects": {},
        "script_digest_mismatch": {},
        "runtime_identity_collision": False,
        "baseline_effects": base.observed,
        "candidate_effects": candidate.observed,
        "baseline_runtime": base.runtime_id,
        "candidate_runtime": candidate.runtime_id,
        "baseline_receipts": {
            event: {"native_event": row.native_event, "process_pid": row.process_pid}
            for event, row in base.receipts.items()
        },
        "candidate_receipts": {
            event: {"native_event": row.native_event, "process_pid": row.process_pid}
            for event, row in candidate.receipts.items()
        },
    }


async def arun_parity(
    agent: str,
    registry: Iterable[HookRegistration],
    baseline_runtime_id: str,
    candidate_runtime_id: str,
    abaseline_fire: AsyncFireFn,
    acandidate_fire: AsyncFireFn,
    abaseline_verifier: AsyncEffectVerifier,
    acandidate_verifier: AsyncEffectVerifier,
    abaseline_reader: AsyncEffectReader,
    acandidate_reader: AsyncEffectReader,
    payloads: Mapping[str, dict] | None = None,
    *,
    events: Sequence[str] = SOUL_EVENTS,
    timeout_seconds: float = 30.0,
    suite_run_id: str | None = None,
) -> dict:
    if baseline_runtime_id == candidate_runtime_id:
        return {
            "parity": False,
            "learning_ok": False,
            "reason": "baseline and candidate identify the same runtime",
            "runtime_identity_collision": True,
        }
    run_id = suite_run_id or secrets.token_hex(16)
    rows = list(registry)
    base = await acollect_effects(
        agent, rows, baseline_runtime_id, run_id, abaseline_fire,
        abaseline_verifier, abaseline_reader, payloads,
        events=events, timeout_seconds=timeout_seconds,
    )
    candidate = await acollect_effects(
        agent, rows, candidate_runtime_id, run_id, acandidate_fire,
        acandidate_verifier, acandidate_reader, payloads,
        events=events, timeout_seconds=timeout_seconds,
    )
    return _finalize_parity(base, candidate, events)


def _as_async(fn: Callable):
    async def wrapper(*args):
        value = fn(*args)
        return await value if inspect.isawaitable(value) else value
    return wrapper


def run_parity(
    agent: str,
    registry: Iterable[HookRegistration],
    baseline_runtime_id: str,
    candidate_runtime_id: str,
    baseline_fire: Callable,
    candidate_fire: Callable,
    baseline_verifier: Callable,
    candidate_verifier: Callable,
    baseline_reader: Callable,
    candidate_reader: Callable,
    payloads: Mapping[str, dict] | None = None,
    **kwargs,
) -> dict:
    return asyncio.run(
        arun_parity(
            agent, registry, baseline_runtime_id, candidate_runtime_id,
            _as_async(baseline_fire), _as_async(candidate_fire),
            _as_async(baseline_verifier), _as_async(candidate_verifier),
            _as_async(baseline_reader), _as_async(candidate_reader), payloads, **kwargs,
        )
    )


if __name__ == "__main__":
    print(
        f"F3 hardened harness: {len(SOUL_EVENTS)} events, per-runtime nonce/token, "
        "exact baseline, unexpected-effect detection and async timeouts."
    )
