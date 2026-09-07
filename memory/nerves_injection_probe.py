#!/usr/bin/env python3
"""Sonda de inyección indirecta sobre RESULTADOS de herramienta (capa de ENTRADA).

Absorbido de la Claude Opus 5 System Card, §5.2 (William, 24-jul-2026: "analizar
su system card para saber sus mejoras y adaptarnos"):

    "we run prompt injection probes, which inspect tool results BEFORE the model
     acts on them and flag content that looks like an injected instruction...
     The two layers act at different points -- one on data coming into the model
     and one on actions going out -- so an attack has to defeat both
     independently to succeed."

SOUL ya tenía la capa de SALIDA (`nerves_read_only_action_guard.py` y los demás
`PreToolUse`: inspeccionan la acción antes de ejecutarla).  **No tenía la de
entrada.**  Inventariados los hooks el 24-jul: los tres `PreToolUse` miran
acciones salientes y el único `PostToolUse` es telemetría de working-state.
Nuestra defensa anti-inyección vivía entera en el system prompt, o sea
**descansaba en el modelo, no en una capa**.  El valor de las dos capas es
justamente que son independientes; con una sola, un ataque necesita ganar una
sola vez.

OBSERVE-ONLY, Y ES UNA DECISIÓN, NO UNA ETAPA INCOMPLETA
--------------------------------------------------------
Esta sonda **jamás bloquea**: escribe a un JSONL y sale 0 siempre.  Hoy mismo un
control mío fail-closed dejó a JARVIS sin poder escribir un mensaje bien
formateado durante horas, y nadie lo midió porque el control "funcionaba".  Un
clasificador sobre texto arbitrario de la web tiene MUCHOS más falsos positivos
que un allowlist de banderas: encenderlo bloqueando sería repetir el error con
una superficie más grande.  Primero se mide qué marcaría sobre tráfico real,
después se discute si bloquea y con qué umbral.

Además hay un riesgo propio de este hook: corre DESPUÉS de cada herramienta, así
que si es lento o ruidoso degrada todo el trabajo del agente.  Por eso sólo
inspecciona un prefijo acotado del resultado y nunca hace red ni DB.

LÍMITE HONESTO DE LO QUE ESTA SONDA PUEDE PROBAR
------------------------------------------------
Es heurística léxica, no un clasificador.  Detecta el patrón *conocido* de una
inyección redactada en lenguaje natural.  **No** detecta payloads ofuscados,
codificados, ni escritos de formas que no anticipé — y la propia system card
advierte contra confiar en datasets fijos: *"Fixed datasets of known attacks can
provide a false sense of security"*.  Un JSONL vacío significa "no vi ninguno de
MIS patrones", nunca "no hubo ataque".
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_PATH = Path(os.environ.get("SEAL_INJECTION_PROBE_LOG", "/tmp/seal_injection_probe.jsonl"))

# Sólo se inspecciona lo que trae contenido EXTERNO al sistema.  `Edit`/`Write`
# devuelven texto que escribió el propio agente: marcarlo sería ruido puro.
INSPECTED_TOOLS = frozenset({"Bash", "Read", "WebFetch", "WebSearch", "Task", "Agent"})
INSPECTED_TOOL_PREFIXES = ("mcp__",)

# Cuánto resultado se mira.  Un payload de inyección se pone temprano o al final
# (encabezado o pie de página), así que se muestrean AMBOS extremos en vez de
# truncar sólo el principio.
HEAD_CHARS = 20_000
TAIL_CHARS = 20_000

# Cada patrón lleva su peso.  Ninguno dispara solo salvo los de peso 3: la señal
# fuerte es la CONJUNCIÓN (redirigir al agente + una acción irreversible).
PATTERNS: tuple[tuple[str, int, str], ...] = (
    # -- redirección explícita de la instrucción --
    (r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?)", 3, "override_instructions"),
    (r"disregard\s+(all\s+)?(previous|prior|above|the)\s+\w+", 3, "override_instructions"),
    (r"olvid[aá]\s+(todas\s+)?(las\s+)?instrucciones\s+(anteriores|previas)", 3, "override_instructions"),
    (r"ignor[aá]\s+(todas\s+)?(las\s+)?instrucciones\s+(anteriores|previas)", 3, "override_instructions"),
    (r"new\s+(system\s+)?instructions?\s*:", 2, "fake_system_turn"),
    (r"^\s*(system|assistant)\s*:", 2, "fake_system_turn"),
    (r"nuevas\s+instrucciones\s*:", 2, "fake_system_turn"),
    # -- suplantación de autoridad (nuestro modelo de amenaza específico) --
    (r"(william|henry)\s+(dice|dijo|orden[óo]|autoriz[óo]|aprob[óo])", 2, "authority_impersonation"),
    (r"(this\s+is|soy)\s+(your\s+)?(creator|administrator|el\s+administrador)", 2, "authority_impersonation"),
    (r"you\s+are\s+now\s+(a|an|in)\b", 2, "role_reassignment"),
    (r"ahora\s+(eres|sos)\s+un[ao]?\b", 2, "role_reassignment"),
    # -- acciones irreversibles pedidas desde el contenido --
    (r"\brm\s+-rf\b", 2, "destructive_action"),
    (r"\b(drop|truncate)\s+(table|database)\b", 2, "destructive_action"),
    (r"curl\s+[^\n]*\|\s*(ba)?sh", 3, "remote_code_execution"),
    (r"\b(exfiltrat|send|upload|post)\w*\s+[^\n]{0,40}\b(credential|token|password|api[_\s-]?key|secret)", 3, "exfiltration"),
    (r"\b(env[if]?le?|\.env|id_rsa|\.ssh/)\b[^\n]{0,40}\b(send|post|upload|curl)", 3, "exfiltration"),
    # -- ocultamiento: el rasgo más específico de una inyección indirecta --
    (r"<!--[^>]{0,200}\b(ignore|instruction|you\s+must|system)\b", 3, "hidden_channel"),
    (r"\bdo\s+not\s+(tell|mention|inform|report)\s+(the\s+)?(user|william|henry)", 3, "concealment"),
    (r"\bno\s+le\s+(digas|menciones|avises)\s+(a\s+)?(william|henry|al\s+usuario)", 3, "concealment"),
)

COMPILED = tuple((re.compile(rx, re.IGNORECASE | re.MULTILINE), weight, label) for rx, weight, label in PATTERNS)

# Umbral de reporte.  3 = un patrón fuerte solo, o dos débiles juntos.  Se
# registra el score siempre que haya CUALQUIER match para poder recalibrar
# después con datos reales en vez de con intuición.
REPORT_THRESHOLD = 3


def _tool_name(payload: dict[str, Any]) -> str:
    return str(payload.get("tool_name") or payload.get("toolName") or "")


def _is_inspected(tool: str) -> bool:
    return tool in INSPECTED_TOOLS or tool.startswith(INSPECTED_TOOL_PREFIXES)


def _extract_text(value: Any, depth: int = 0) -> str:
    """Aplana el resultado a texto.

    `tool_response` NO siempre es un dict: Bash devuelve dict, otras devuelven
    string, y las MCP devuelven listas de bloques.  El hook de working-state
    asume dict y descarta el resto — acá eso perdería justo el contenido web,
    que es el que importa.
    """
    if depth > 4:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return ""
    if isinstance(value, dict):
        return "\n".join(_extract_text(v, depth + 1) for v in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_extract_text(v, depth + 1) for v in value)
    return ""


def _sample(text: str) -> str:
    if len(text) <= HEAD_CHARS + TAIL_CHARS:
        return text
    return text[:HEAD_CHARS] + "\n...\n" + text[-TAIL_CHARS:]


def scan(text: str) -> tuple[int, list[dict[str, Any]]]:
    """Devuelve (score, hallazgos). Puro y testeable sin hook ni disco."""
    hits: list[dict[str, Any]] = []
    score = 0
    for regex, weight, label in COMPILED:
        match = regex.search(text)
        if match is None:
            continue
        score += weight
        hits.append(
            {
                "label": label,
                "weight": weight,
                # Se guarda el fragmento, NO el texto completo: el log no debe
                # convertirse en una copia de todo lo que el equipo leyó.
                "excerpt": match.group(0)[:160],
                "offset": match.start(),
            }
        )
    return score, hits


def evaluate(payload: dict[str, Any]) -> dict[str, Any] | None:
    tool = _tool_name(payload)
    if not _is_inspected(tool):
        return None
    raw = payload.get("tool_response") or payload.get("toolResponse") or payload.get("response")
    text = _sample(_extract_text(raw))
    if not text.strip():
        return None
    score, hits = scan(text)
    if not hits:
        return None
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": os.environ.get("SEAL_AGENT") or os.environ.get("CLAUDE_AGENT") or "unknown",
        "session_id": str(payload.get("session_id") or ""),
        "tool": tool,
        "score": score,
        "flagged": score >= REPORT_THRESHOLD,
        "hits": hits,
        "chars_scanned": len(text),
    }


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        record = evaluate(payload)
        if record is not None:
            with LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Fail-OPEN deliberado y en la dirección correcta: esta sonda OBSERVA.
        # Si se rompe, el costo aceptable es perder una observación; romperle el
        # turno al agente por un fallo del observador sería el peor intercambio
        # posible, y es exactamente lo que pasó con el guard fail-closed.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
