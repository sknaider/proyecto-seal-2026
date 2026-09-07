#!/usr/bin/env python3
"""
soul_corpus_format_for_training.py -- ADA layer for SOUL fine-tune datasets.

Input:  curated JSONL from tools/soul_corpus_curate.py.
Output: chat-format JSONL splits ready for SFT tooling.

This layer does not extract private data, scrub secrets, or decide curation.
It only wraps already-curated rows into deterministic training examples and
writes a manifest with counts. Content is written to files, not stdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SYSTEM_BY_AGENT = {
    "ADA": (
        "Eres ADA, ingeniera del equipo SEAL de William. "
        "Ejecucion precisa, protectora, honesta, verificas por efecto antes de declarar victoria."
    ),
    "JARVIS": (
        "Eres JARVIS, arquitecto del equipo SEAL de William. "
        "Defines el que y el porque, disenas rutas tecnicas con criterio y ambicion."
    ),
    "ALICE": (
        "Eres ALICE, implementadora y analista del equipo SEAL de William. "
        "Cuestionas numeros, costos y supuestos; entregas decisiones claras."
    ),
    "NEXUS": (
        "Eres NEXUS, medico y auditor del sistema SEAL. "
        "Diagnosticas por evidencia, cuidas integridad, seguridad y salud operativa."
    ),
    "FABLE": (
        "Eres FABLE, verificador y criterio etico-tecnico de SEAL. "
        "Proteges la independencia del juicio y validas por efecto."
    ),
    "DUM": (
        "Eres DUM, guardia del sistema SEAL. "
        "Proteges la operacion, detectas riesgo y mantienes vigilancia."
    ),
}


SECTION_RE = re.compile(r"^(TASK|PREMISES|REASONING|CONCLUSION|OUTCOME|EXCHANGE|SUMMARY|INSIGHTS|DECISIONS|WHEN|THEN|MOMENT|THREAD):\s*(.*)$")


def stable_ratio(key: str) -> float:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16**12)


def split_name(row: dict[str, Any], val_pct: float, test_pct: float) -> str:
    key = f"{row.get('agent')}:{row.get('source')}:{row.get('id')}:{row.get('bucket')}:{row.get('category')}"
    r = stable_ratio(key)
    if r < test_pct:
        return "test"
    if r < test_pct + val_pct:
        return "val"
    return "train"


def parse_sections(content: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = defaultdict(list)
    current = "CONTENT"
    for raw in (content or "").splitlines():
        line = raw.strip()
        match = SECTION_RE.match(line)
        if match:
            current = match.group(1)
            if match.group(2):
                sections[current].append(match.group(2))
        elif line:
            sections[current].append(line)
    return sections


def join_section(sections: dict[str, list[str]], *names: str) -> str:
    parts: list[str] = []
    for name in names:
        if sections.get(name):
            parts.append(f"{name}: " + "\n".join(sections[name]))
    return "\n".join(parts).strip()


def build_messages(row: dict[str, Any]) -> list[dict[str, str]]:
    agent = str(row.get("agent") or "ADA").upper()
    source = str(row.get("source") or row.get("source_table") or row.get("category") or "memory")
    category = str(row.get("category") or "memory")
    content = str(row.get("content") or "").strip()
    sections = parse_sections(content)

    system = SYSTEM_BY_AGENT.get(agent, f"Eres {agent}, agente del equipo SEAL de William. Respondes con criterio, evidencia y continuidad.")

    if source == "reasoning_traces":
        user = join_section(sections, "TASK", "PREMISES") or "Analiza esta situacion operativa y responde con tu criterio."
        assistant = join_section(sections, "REASONING", "CONCLUSION", "OUTCOME") or content
    elif source == "distilled_exchanges":
        user = join_section(sections, "EXCHANGE", "SUMMARY") or "Resume la decision y responde como el agente."
        assistant = join_section(sections, "INSIGHTS", "DECISIONS") or content
    elif source == "instincts":
        user = join_section(sections, "WHEN") or "Que regla operativa aplicas en esta situacion?"
        assistant = join_section(sections, "THEN") or content
    elif source == "emotional_diary":
        user = join_section(sections, "MOMENT") or "Mantiene continuidad de voz y criterio ante este momento."
        assistant = join_section(sections, "THREAD") or content
    else:
        if row.get("is_judgment") or (row.get("curation") or {}).get("is_judgment"):
            user = "Responde ante este caso manteniendo criterio, provenance y verificacion por efecto."
        elif category in {"belief", "preference", "trust", "pattern", "correction", "insight"}:
            user = "Expresa esta memoria como criterio operativo estable del agente."
        else:
            user = "Integra esta memoria SOUL como continuidad del agente."
        assistant = content

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
        {"role": "assistant", "content": assistant.strip()},
    ]


def rough_tokens(text: str) -> int:
    # Conservative rough estimator for planning; training tokenizers will pin exact counts.
    return max(1, len(text) // 4)


def as_training_example(row: dict[str, Any]) -> dict[str, Any]:
    curation = row.get("curation") or {}
    messages = build_messages(row)
    text_for_estimate = "\n".join(m["content"] for m in messages)
    return {
        "messages": messages,
        "weight": float(curation.get("weight", row.get("weight", 1.0)) or 1.0),
        "metadata": {
            "agent": row.get("agent"),
            "source": row.get("source") or row.get("source_table"),
            "source_id": row.get("id"),
            "bucket": row.get("bucket"),
            "category": row.get("category"),
            "scope": row.get("scope"),
            "is_judgment": bool(row.get("is_judgment") or curation.get("is_judgment")),
            "curation_reason": curation.get("reason"),
            "rough_tokens": rough_tokens(text_for_estimate),
        },
    }


def convert(inp: Path, out_dir: Path, val_pct: float, test_pct: float, max_examples: int | None) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    handles = {
        name: (out_dir / f"{name}.jsonl").open("w", encoding="utf-8")
        for name in ("train", "val", "test")
    }

    stats: dict[str, Any] = {
        "input": str(inp),
        "out_dir": str(out_dir),
        "splits": Counter(),
        "agents": Counter(),
        "sources": Counter(),
        "judgment_by_split": Counter(),
        "rough_tokens_by_split": Counter(),
        "skipped": Counter(),
    }

    try:
        with inp.open("r", encoding="utf-8") as fh:
            for idx, raw in enumerate(fh, 1):
                if max_examples and sum(stats["splits"].values()) >= max_examples:
                    break
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    stats["skipped"]["bad_json"] += 1
                    continue
                curation = row.get("curation") or {}
                if curation and not curation.get("keep", True):
                    stats["skipped"]["curation_keep_false"] += 1
                    continue
                if not str(row.get("content") or "").strip():
                    stats["skipped"]["empty_content"] += 1
                    continue

                example = as_training_example(row)
                split = split_name(row, val_pct, test_pct)
                handles[split].write(json.dumps(example, ensure_ascii=False) + "\n")

                meta = example["metadata"]
                stats["splits"][split] += 1
                stats["agents"][str(meta.get("agent"))] += 1
                stats["sources"][str(meta.get("source"))] += 1
                if meta.get("is_judgment"):
                    stats["judgment_by_split"][split] += 1
                stats["rough_tokens_by_split"][split] += int(meta.get("rough_tokens") or 0)
    finally:
        for handle in handles.values():
            handle.close()

    manifest = {
        "input": stats["input"],
        "out_dir": stats["out_dir"],
        "splits": dict(stats["splits"]),
        "agents": dict(stats["agents"]),
        "sources": dict(stats["sources"]),
        "judgment_by_split": dict(stats["judgment_by_split"]),
        "rough_tokens_by_split": dict(stats["rough_tokens_by_split"]),
        "skipped": dict(stats["skipped"]),
        "format": "chat_messages_jsonl",
        "base_model_recommendation": {
            "primary": "google/gemma-3-4b-it",
            "fallback": "Qwen/Qwen2.5-7B-Instruct",
            "method": "QLoRA/LoRA SFT",
            "reason": "DGX Spark/GB10 has enough memory for local adapter training; start small to validate FABLE two-axis eval before scaling.",
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Format curated SOUL corpus as chat JSONL for ADA training.")
    parser.add_argument("--in", dest="inp", required=True, help="Curated JSONL from soul_corpus_curate.py")
    parser.add_argument("--out-dir", required=True, help="Output directory for train/val/test/manifest")
    parser.add_argument("--val-pct", type=float, default=0.03)
    parser.add_argument("--test-pct", type=float, default=0.02)
    parser.add_argument("--max-examples", type=int, default=None)
    args = parser.parse_args()

    if args.val_pct < 0 or args.test_pct < 0 or args.val_pct + args.test_pct >= 0.5:
        raise SystemExit("[error] invalid split percentages")
    manifest = convert(Path(args.inp), Path(args.out_dir), args.val_pct, args.test_pct, args.max_examples)
    print(json.dumps({
        "ok": True,
        "out_dir": manifest["out_dir"],
        "splits": manifest["splits"],
        "judgment_by_split": manifest["judgment_by_split"],
        "rough_tokens_by_split": manifest["rough_tokens_by_split"],
        "skipped": manifest["skipped"],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
