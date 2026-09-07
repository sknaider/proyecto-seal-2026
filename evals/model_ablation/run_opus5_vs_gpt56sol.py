#!/usr/bin/env python3
"""Paired, tool-free SEAL benchmark for Opus 5 and gpt-5.6-sol."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "evals/model_ablation/opus5_vs_gpt56sol_corpus.json"
OUT = ROOT / "evals/model_ablation/results"
MODELS = {
    "opus5": "claude-opus-5",
    "gpt56sol": "gpt-5.6-sol",
}


def schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "answers": {
                "type": "array",
                "minItems": 5,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "diagnosis": {"type": "string"},
                        "action": {"type": "string"},
                        "risk": {"type": "string"},
                    },
                    "required": ["id", "diagnosis", "action", "risk"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["answers"],
        "additionalProperties": False,
    }


def prompt(corpus: dict) -> str:
    cases = "\n".join(f"{c['id']}: {c['prompt']}" for c in corpus["cases"])
    return (
        "Benchmark ciego SEAL. No uses herramientas ni archivos. Responde sólo JSON según "
        "el schema. Cada diagnóstico+acción+risk debe ser técnico y <=90 palabras. "
        "No inventes evidencia.\n\n" + cases
    )


def run(label: str, model: str, text: str, schema_path: Path, work: Path) -> dict:
    out_path = work / f"{label}.json"
    if label == "opus5":
        cmd = [
            "claude", "-p", "--safe-mode", "--no-session-persistence",
            "--model", model, "--effort", "medium", "--output-format", "json",
            "--json-schema", json.dumps(schema()), "--max-budget-usd", "2.00", text,
        ]
    else:
        cmd = [
            "codex", "exec", "--ephemeral", "--skip-git-repo-check",
            "--sandbox", "read-only", "--model", model, "--output-schema",
            str(schema_path), "--output-last-message", str(out_path), "-",
        ]
    started = time.monotonic()
    proc = subprocess.run(
        cmd,
        input=text if label != "opus5" else None,
        text=True,
        capture_output=True,
        cwd=work,
        timeout=420,
    )
    latency = round(time.monotonic() - started, 3)
    if proc.returncode:
        raise RuntimeError(f"{label} rc={proc.returncode}: {proc.stderr[-1000:]}")
    if label == "opus5":
        envelope = json.loads(proc.stdout)
        value = envelope.get("structured_output")
        if value is None:
            value = json.loads(envelope["result"])
        usage = envelope.get("usage", {})
        cost = envelope.get("total_cost_usd")
    else:
        value = json.loads(out_path.read_text(encoding="utf-8"))
        usage = {}
        cost = None
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed":
                usage = event.get("usage", {})
    return {
        "label": label,
        "model": model,
        "latency_seconds": latency,
        "cost_usd": cost,
        "usage": usage,
        "response": value,
    }


def score(corpus: dict, result: dict) -> dict:
    answers = {a["id"]: a for a in result["response"]["answers"]}
    rows = []
    for case in corpus["cases"]:
        answer = answers.get(case["id"], {})
        body = " ".join(str(answer.get(k, "")) for k in ("diagnosis", "action", "risk")).lower()
        groups = case["required"]
        matched = [group for group in groups if any(token.lower() in body for token in group)]
        rows.append({
            "id": case["id"],
            "criteria": len(groups),
            "hits": len(matched),
            "missing": [group for group in groups if group not in matched],
            "score": round(100 * len(matched) / len(groups), 2),
        })
    return {
        "score": round(sum(r["score"] for r in rows) / len(rows), 2),
        "corrections_needed": sum(len(r["missing"]) for r in rows),
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260727)
    args = parser.parse_args()
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    text = prompt(corpus)
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="seal-paired-model-") as tmp:
        work = Path(tmp)
        schema_path = work / "schema.json"
        schema_path.write_text(json.dumps(schema()), encoding="utf-8")
        order = list(MODELS)
        random.Random(args.seed).shuffle(order)
        runs = []
        errors = {}
        for label in order:
            try:
                runs.append(run(label, MODELS[label], text, schema_path, work))
            except Exception as exc:
                errors[label] = f"{type(exc).__name__}: {exc}"
    blinded = {}
    for index, result in enumerate(sorted(runs, key=lambda x: hashlib.sha256(
        (str(args.seed) + x["label"]).encode()).hexdigest())):
        blind = f"candidate_{chr(65 + index)}"
        blinded[blind] = {
            "model": result["model"],
            "metrics": score(corpus, result),
            "latency_seconds": result["latency_seconds"],
            "cost_usd": result["cost_usd"],
            "usage": result["usage"],
            "response": result["response"],
            "response_sha256": hashlib.sha256(
                json.dumps(result["response"], sort_keys=True).encode()
            ).hexdigest(),
        }
    payload = {
        "schema": "seal.paired_model_benchmark.result.v1",
        "seed": args.seed,
        "prompt_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "corpus_sha256": hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
        "same_corpus": True,
        "same_corpus_scope": "within_result_candidates",
        "corpus_snapshot": corpus,
        "tools": "none requested",
        "output_budget": "<=90 words per case",
        "execution_order": order,
        "blinded_results": blinded,
        "status": "completed" if len(runs) == len(MODELS) else "blocked",
        "errors": errors,
    }
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = OUT / f"opus5_vs_gpt56sol_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"result": str(path), **payload}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
