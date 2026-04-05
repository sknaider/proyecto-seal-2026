"""Microcompact Engine — Limpieza inteligente de contexto sin LLM.

Inspirado en microCompact.ts y autoCompact.ts de Claude Code v2.1.88.
Detecta y comprime tool_results repetitivos y outputs grandes
ANTES de que Claude realice su compactación LLM.

Nivel 1 del sistema de compactación de 4 niveles de Claude Code:
  1. Microcompact (sin LLM) ← ESTE MÓDULO
  2. Session Memory Compact (sin LLM)
  3. LLM Compact
  4. Reactive (fallback 413)

Creado por JARVIS para Team SEAL — basado en investigación de Claude Code.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

LOG = logging.getLogger("seal-microcompact")

# ── Patrones de tool_results comprimibles ──

@dataclass
class CompactionRule:
    """Regla de compactación: qué detectar y cómo comprimir."""
    name: str
    pattern: re.Pattern
    max_occurrences: int  # cuántas veces mantener antes de comprimir
    extract_summary: callable  # función que extrae resumen del output
    category: str = "tool_output"


def _summarize_nvidia_smi(text: str) -> str:
    """Extrae métricas clave de nvidia-smi."""
    gpu_util = re.search(r'(\d+)%\s+Default', text)
    temp = re.search(r'(\d+)C\s', text)
    mem_used = re.search(r'(\d+)MiB\s*/\s*(\d+)MiB', text)

    parts = []
    if gpu_util:
        parts.append(f"GPU: {gpu_util.group(1)}%")
    if temp:
        parts.append(f"Temp: {temp.group(1)}C")
    if mem_used:
        parts.append(f"VRAM: {mem_used.group(1)}/{mem_used.group(2)}MiB")

    return " | ".join(parts) if parts else "GPU stats capturados"


def _summarize_grep_output(text: str) -> str:
    """Resume output de grep/rg."""
    lines = text.strip().split('\n')
    total = len(lines)
    if total <= 5:
        return text  # no comprimir si es corto
    files = set()
    for line in lines:
        if ':' in line:
            files.add(line.split(':')[0])
    return f"{total} coincidencias en {len(files)} archivos"


def _summarize_ls_output(text: str) -> str:
    """Resume output de ls/find."""
    lines = [l for l in text.strip().split('\n') if l.strip()]
    if len(lines) <= 10:
        return text
    return f"{len(lines)} archivos/directorios listados"


def _summarize_git_log(text: str) -> str:
    """Resume output de git log."""
    commits = re.findall(r'commit [a-f0-9]{40}', text)
    if len(commits) <= 3:
        return text
    return f"{len(commits)} commits mostrados"


def _summarize_pip_output(text: str) -> str:
    """Resume output de pip install/list."""
    lines = text.strip().split('\n')
    if len(lines) <= 5:
        return text
    installed = [l for l in lines if 'Successfully installed' in l or 'already satisfied' in l]
    if installed:
        return installed[-1]
    return f"{len(lines)} líneas de output pip"


def _summarize_cat_output(text: str) -> str:
    """Resume output de lectura de archivo largo."""
    lines = text.strip().split('\n')
    if len(lines) <= 30:
        return text
    return f"Archivo leído: {len(lines)} líneas"


def _summarize_generic_long(text: str) -> str:
    """Resume cualquier output largo genérico."""
    lines = text.strip().split('\n')
    if len(lines) <= 20:
        return text
    first_3 = '\n'.join(lines[:3])
    last_2 = '\n'.join(lines[-2:])
    return f"{first_3}\n... [{len(lines) - 5} líneas omitidas] ...\n{last_2}"


# ── Reglas de compactación ──
COMPACTION_RULES: list[CompactionRule] = [
    CompactionRule(
        name="nvidia-smi",
        pattern=re.compile(r'nvidia-smi|NVIDIA-SMI|GPU-Util|MiB\s*/\s*\d+MiB', re.IGNORECASE),
        max_occurrences=2,
        extract_summary=_summarize_nvidia_smi,
        category="gpu_monitoring",
    ),
    CompactionRule(
        name="grep/rg output",
        pattern=re.compile(r'^\S+:\d+:', re.MULTILINE),
        max_occurrences=3,
        extract_summary=_summarize_grep_output,
        category="search_results",
    ),
    CompactionRule(
        name="git log",
        pattern=re.compile(r'commit [a-f0-9]{40}'),
        max_occurrences=2,
        extract_summary=_summarize_git_log,
        category="version_control",
    ),
    CompactionRule(
        name="pip output",
        pattern=re.compile(r'(?:Successfully installed|already satisfied|Collecting|Downloading)', re.IGNORECASE),
        max_occurrences=1,
        extract_summary=_summarize_pip_output,
        category="package_management",
    ),
    CompactionRule(
        name="directory listing",
        pattern=re.compile(r'(?:^total \d+|^drwx|^-rw)', re.MULTILINE),
        max_occurrences=3,
        extract_summary=_summarize_ls_output,
        category="file_system",
    ),
]


@dataclass
class CompactionResult:
    """Resultado de la compactación."""
    original_chars: int
    compacted_chars: int
    savings_pct: float
    tombstones_created: int
    rules_triggered: list[str]
    details: list[dict] = field(default_factory=list)


@dataclass
class Tombstone:
    """Reemplazo de un tool_result compactado."""
    rule_name: str
    original_chars: int
    summary: str
    timestamp: str
    category: str


# ── Motor de compactación ──

class MicrocompactEngine:
    """Motor de microcompactación sin LLM.

    Mantiene un registro de tool_results vistos por regla
    y comprime los que exceden max_occurrences.
    """

    def __init__(self):
        self._occurrence_count: dict[str, int] = {}  # rule_name → count
        self._total_saved: int = 0
        self._tombstones: list[Tombstone] = []

    def should_compact(self, text: str, min_chars: int = 200) -> Optional[CompactionRule]:
        """Determina si un texto debería compactarse.

        Args:
            text: El texto a evaluar (típicamente un tool_result)
            min_chars: Mínimo de caracteres para considerar compactación

        Returns:
            La regla que aplica, o None si no se debe compactar
        """
        if len(text) < min_chars:
            return None

        for rule in COMPACTION_RULES:
            if rule.pattern.search(text):
                count = self._occurrence_count.get(rule.name, 0)
                if count >= rule.max_occurrences:
                    return rule
                self._occurrence_count[rule.name] = count + 1

        # Compactar cualquier output genérico > 3000 chars
        if len(text) > 3000:
            return CompactionRule(
                name="output_largo",
                pattern=re.compile(r'.*'),
                max_occurrences=0,
                extract_summary=_summarize_generic_long,
                category="generic",
            )

        return None

    def compact(self, text: str) -> tuple[str, Optional[Tombstone]]:
        """Intenta compactar un texto. Retorna (resultado, tombstone_o_None).

        Si el texto no necesita compactación, retorna (text, None).
        Si se compactó, retorna (resumen, Tombstone).
        """
        rule = self.should_compact(text)
        if rule is None:
            return text, None

        summary = rule.extract_summary(text)
        tombstone = Tombstone(
            rule_name=rule.name,
            original_chars=len(text),
            summary=summary,
            timestamp=datetime.now(timezone.utc).isoformat(),
            category=rule.category,
        )

        self._tombstones.append(tombstone)
        self._total_saved += len(text) - len(summary)

        compact_text = f"[COMPACTADO:{rule.name}] {summary}"
        return compact_text, tombstone

    def compact_batch(self, texts: list[str]) -> CompactionResult:
        """Compacta una lista de textos (ej: todos los tool_results de la sesión).

        Returns:
            CompactionResult con estadísticas
        """
        original_total = sum(len(t) for t in texts)
        compacted_total = 0
        tombstones_created = 0
        rules_triggered = set()
        details = []

        for i, text in enumerate(texts):
            result, tombstone = self.compact(text)
            compacted_total += len(result)
            if tombstone:
                tombstones_created += 1
                rules_triggered.add(tombstone.rule_name)
                details.append({
                    "index": i,
                    "rule": tombstone.rule_name,
                    "original_chars": tombstone.original_chars,
                    "compacted_chars": len(result),
                    "saved_chars": tombstone.original_chars - len(result),
                })

        savings_pct = (1 - compacted_total / original_total) * 100 if original_total > 0 else 0

        return CompactionResult(
            original_chars=original_total,
            compacted_chars=compacted_total,
            savings_pct=round(savings_pct, 1),
            tombstones_created=tombstones_created,
            rules_triggered=sorted(rules_triggered),
            details=details,
        )

    def get_stats(self) -> dict:
        """Retorna estadísticas del engine."""
        return {
            "total_chars_saved": self._total_saved,
            "tombstones_created": len(self._tombstones),
            "occurrence_counts": dict(self._occurrence_count),
            "rules_active": len(COMPACTION_RULES),
        }

    def reset(self):
        """Reset para nueva sesión."""
        self._occurrence_count.clear()
        self._total_saved = 0
        self._tombstones.clear()


# ── Singleton global ──
_engine: MicrocompactEngine | None = None


def get_engine() -> MicrocompactEngine:
    """Obtiene el engine singleton."""
    global _engine
    if _engine is None:
        _engine = MicrocompactEngine()
    return _engine


# ── Self-test ──
if __name__ == "__main__":
    print("=== SEAL Microcompact Engine — Self Test ===\n")

    engine = MicrocompactEngine()

    # Test 1: nvidia-smi repetido
    nvidia_output = """
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 560.94                 Driver Version: 560.94         CUDA Version: 12.8     |
|-------------------------------+------------------------+----------------------+
| GPU  Name                 Persistence-M | Bus-Id          Disp.A | Volatile Uncorr. ECC |
| Fan  Temp   Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |
|===============================+=========================+======================|
|   0  NVIDIA GB10B              On  |   0000:08:00.0      Off |                    0 |
| N/A   40C    P0              10W / 100W |    2048MiB / 131072MiB |      5%      Default |
+-------------------------------+------------------------+----------------------+
"""
    # Primeras 2 ocurrencias se mantienen
    r1, t1 = engine.compact(nvidia_output)
    r2, t2 = engine.compact(nvidia_output)
    assert t1 is None, "Primera nvidia-smi no debe compactarse"
    assert t2 is None, "Segunda nvidia-smi no debe compactarse"
    # Tercera se compacta
    r3, t3 = engine.compact(nvidia_output)
    assert t3 is not None, "Tercera nvidia-smi DEBE compactarse"
    print(f"  [PASS] nvidia-smi: 2 mantenidas, 3ra compactada → '{r3}'")

    # Test 2: grep output largo
    engine2 = MicrocompactEngine()
    grep_output = "\n".join([f"file{i}.py:{i*10}: some matching line content here" for i in range(50)])
    # Saturar las 3 ocurrencias permitidas
    for _ in range(3):
        engine2.compact(grep_output)
    r4, t4 = engine2.compact(grep_output)
    assert t4 is not None, "4ta grep DEBE compactarse"
    print(f"  [PASS] grep: 3 mantenidas, 4ta compactada → '{r4}'")

    # Test 3: texto corto no se compacta
    engine3 = MicrocompactEngine()
    short = "ls: total 5 archivos"
    r5, t5 = engine3.compact(short)
    assert t5 is None, "Texto corto no debe compactarse"
    print(f"  [PASS] Texto corto ({len(short)} chars): no compactado")

    # Test 4: output genérico largo
    engine4 = MicrocompactEngine()
    long_output = "\n".join([f"Line {i}: some very long output that takes up space" for i in range(100)])
    r6, t6 = engine4.compact(long_output)
    assert t6 is not None, "Output largo genérico DEBE compactarse"
    print(f"  [PASS] Output largo ({len(long_output)} chars) → compactado a {len(r6)} chars")

    # Test 5: batch compaction
    engine5 = MicrocompactEngine()
    batch = [nvidia_output] * 5 + [grep_output] * 5
    result = engine5.compact_batch(batch)
    print(f"  [PASS] Batch: {result.original_chars} → {result.compacted_chars} chars "
          f"({result.savings_pct}% ahorro, {result.tombstones_created} tombstones)")

    print(f"\n  Todos los tests pasaron.")
