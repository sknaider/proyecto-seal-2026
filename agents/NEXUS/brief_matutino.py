#!/usr/bin/env python3
"""Brief matutino operativo de ADA.

Publica siempre uno de tres resultados: estado sano, hallazgos o NO_MEDIBLE.
El proceso sólo termina en éxito si el writer autenticado devuelve un id durable.
No lee DMs, memorias privadas ni evidencia del ledger; la DB expone dos vistas
acotadas para títulos/estado de ADA y el id del último mensaje público de William.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg


ROOT = Path("/home/dadito/IA/proyecto-seal")
SEND = ROOT / "scripts/seal_send.py"
DSN_FILE = Path.home() / ".config/seal/stability_guard.dsn"
STABILITY_REPORT = ROOT / "messages/seal_agent_stability_guard_last.json"
LISTENING_REPORT = Path.home() / ".local/state/seal/ada_listening_guard_report.json"
OWN_UNITS = (
    "ada-codex-remote-bridge.service",
    "seal-channel-monitor@ADA.service",
    "seal-ada-codex-stream-relay.service",
    "seal-ada-codex-poller.service",
    "seal-ada-codex-autostart.service",
)


@dataclass(frozen=True)
class Snapshot:
    completed: tuple[str, ...]
    open_work: tuple[str, ...]
    reply_to: str | None
    findings: tuple[str, ...]
    unmeasurable: tuple[str, ...]


def _run(argv: list[str], timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def _private_dsn() -> str:
    mode = DSN_FILE.stat().st_mode & 0o777
    if mode != 0o600:
        raise RuntimeError(f"{DSN_FILE} mode={mode:o}, esperado=600")
    dsn = DSN_FILE.read_text(encoding="utf-8").strip()
    if (urlsplit(dsn).username or "") != "svc_soul_stability_guard":
        raise RuntimeError("principal inesperado en DSN del brief")
    return dsn


async def _work_snapshot(now: datetime) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    conn = await asyncpg.connect(_private_dsn(), timeout=5)
    try:
        rows = await conn.fetch(
            "SELECT id,title,status,priority,updated_at,completed_at "
            "FROM soul_v3.ada_morning_brief_work_v "
            "ORDER BY priority DESC, updated_at DESC, id DESC"
        )
        reply_to = await conn.fetchval(
            "SELECT legacy_id FROM soul_v3.ada_morning_brief_reply_v"
        )
    finally:
        await conn.close()

    cutoff = now - timedelta(hours=24)
    completed = tuple(
        f"#{row['id']} {row['title']}"
        for row in rows
        if row["status"] == "completed"
        and row["completed_at"] is not None
        and row["completed_at"] >= cutoff
    )
    open_rows = [row for row in rows if row["status"] in {"pending", "in_progress", "reviewing", "blocked"}]
    open_work = tuple(
        f"#{row['id']} [{row['status']}] {row['title']}" for row in open_rows
    )
    return completed, open_work, str(reply_to) if reply_to else None


def _json_report(path: Path, max_age_seconds: float) -> tuple[dict | None, str | None]:
    try:
        age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
        if age > max_age_seconds:
            return None, f"{path.name} obsoleto ({int(age)}s)"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("raíz no es objeto")
        return payload, None
    except Exception as exc:
        return None, f"{path.name} no medible: {type(exc).__name__}: {exc}"


def _health() -> tuple[list[str], list[str]]:
    findings: list[str] = []
    unmeasurable: list[str] = []

    for unit in OWN_UNITS:
        result = _run(["systemctl", "--user", "show", unit, "-p", "LoadState", "-p", "ActiveState", "--value"], 8)
        values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if result.returncode != 0 or len(values) < 2:
            unmeasurable.append(f"{unit}: systemd no devolvió LoadState+ActiveState")
        elif values[0] != "loaded" or values[1] != "active":
            findings.append(f"{unit}: load={values[0]} active={values[1]}")

    stability, error = _json_report(STABILITY_REPORT, 5 * 60)
    if error:
        unmeasurable.append(error)
    elif stability.get("autonomy_contract", {}).get("status") != "healthy":
        findings.append(
            "Stability Guard autonomía="
            + str(stability.get("autonomy_contract", {}).get("status", "ausente"))
        )

    listening, error = _json_report(LISTENING_REPORT, 3 * 60)
    if error:
        unmeasurable.append(error)
    elif listening.get("failures"):
        findings.append(f"William↔ADA: {len(listening['failures'])} falla(s)")

    return findings, unmeasurable


async def collect(now: datetime | None = None) -> Snapshot:
    now = now or datetime.now(timezone.utc)
    findings, unmeasurable = _health()
    try:
        completed, open_work, reply_to = await _work_snapshot(now)
    except Exception as exc:
        completed, open_work, reply_to = (), (), None
        unmeasurable.append(f"ledger/reply no medible: {type(exc).__name__}: {exc}")
    return Snapshot(completed, open_work, reply_to, tuple(findings), tuple(unmeasurable))


def render(snapshot: Snapshot) -> str:
    lines = ["**ADA — brief matutino operativo.**"]
    if not any((snapshot.completed, snapshot.open_work, snapshot.findings, snapshot.unmeasurable)):
        return lines[0] + " Hoy no hay nada; autonomía y comunicación medidas sanas."
    if snapshot.completed:
        lines += ["", "**Cambió en las últimas 24 h:**"]
        lines += [f"- {item}" for item in snapshot.completed[:5]]
        if len(snapshot.completed) > 5:
            lines.append(f"- …y {len(snapshot.completed) - 5} cierre(s) más")
    if snapshot.open_work:
        lines += ["", "**Falta cerrar o verificar:**"]
        lines += [f"- {item}" for item in snapshot.open_work[:5]]
        if len(snapshot.open_work) > 5:
            lines.append(f"- …y {len(snapshot.open_work) - 5} pendiente(s) más")
    if snapshot.findings:
        lines += ["", "⚠️ **Hallazgos de salud:**"] + [f"- {item}" for item in snapshot.findings]
    if snapshot.unmeasurable:
        lines += ["", "**NO pude medir — no equivale a sano:**"]
        lines += [f"- {item}" for item in snapshot.unmeasurable]
    return "\n".join(lines)


def _extract_message_id(stdout: str) -> str | None:
    for line in reversed([line.strip() for line in stdout.splitlines() if line.strip()]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("ok") is True and payload.get("id") is not None:
            return str(payload["id"])
    return None


def publish(text: str, reply_to: str | None, key: str) -> str:
    destination = os.environ.get("SEAL_BRIEF_TO", "William")
    channel = os.environ.get("SEAL_BRIEF_CANAL", "web_chat")
    command = [
        str(SEND), "ADA", destination, text,
        "--channel", channel,
        "--type", "system_alive",
        "--proactive",
        "--idempotency-key", key,
    ]
    if reply_to:
        command += ["--in-reply-to", reply_to]
    result = _run(command, 90)
    message_id = _extract_message_id(result.stdout)
    if result.returncode != 0 or not message_id:
        detail = (result.stderr or result.stdout).strip()[-1000:]
        raise RuntimeError(f"publicación sin recibo durable rc={result.returncode}: {detail}")
    return message_id


async def amain(publicar: bool) -> int:
    snapshot = await collect()
    text = render(snapshot)
    print(text)
    if not publicar:
        return 0
    key = os.environ.get(
        "SEAL_BRIEF_KEY",
        "ada-brief-" + datetime.now().strftime("%Y-%m-%d"),
    )
    message_id = publish(text, snapshot.reply_to, key)
    print(json.dumps({"ok": True, "message_id": message_id, "idempotency_key": key}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publicar", action="store_true")
    args = parser.parse_args()
    try:
        return asyncio.run(amain(args.publicar))
    except Exception as exc:
        print(f"ADA brief FATAL: {type(exc).__name__}: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
