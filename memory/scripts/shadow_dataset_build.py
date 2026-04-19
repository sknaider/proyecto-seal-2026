#!/usr/bin/env python3
"""
SEAL Shadow Dataset Builder — LatentGraphMem V1 vs MAGMA
Extrae N=100 queries balanceadas de los jsonl de agentes para el shadow test.

Breakdown target:
  40 factual   — "¿qué decidió/es/fue/dijo X sobre Y?"
  30 multi-hop — "relación entre A y B tras C", múltiples entidades
  20 temporal  — "qué pasó entre fechas", "cuándo"
  10 causal    — "por qué", "razón de", "causa"

Source: messages/{alice,jarvis,ada}_messages.jsonl (últimos 30 días)
Output: memory/results/shadow_dataset_N100.json
"""
import json
import re
import random
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT = Path.home() / "IA" / "proyecto-seal"
MSGS = ROOT / "messages"
OUT  = ROOT / "memory" / "results" / "shadow_dataset_N100.json"

SOURCES = ["alice_messages.jsonl", "jarvis_messages.jsonl", "ada_messages.jsonl"]

# Keyword heuristics for query classification
FACTUAL_KW  = [r"\bqué (decidió|dijo|es|fue|hizo)\b", r"\bcuál (es|fue)\b", r"\bquién\b",
               r"\bdefinir?\b", r"\bestado de\b"]
MULTIHOP_KW = [r"\brelación entre\b", r"\bimpacto de .+ en\b", r"\btras\b.*\by\b",
               r"\bentre .+ y .+\b", r"\bconexión\b"]
TEMPORAL_KW = [r"\bcuándo\b", r"\bentre\b.*\b(abril|marzo|febrero|enero)\b",
               r"\bantes de\b", r"\bdespués de\b", r"\búltimo[as]?\b",
               r"\bhace \d+\b", r"\ben los últimos\b"]
CAUSAL_KW   = [r"\bpor qué\b", r"\brazón (de|para)\b", r"\bcausa de\b",
               r"\bmotivo\b", r"\ba qué se debe\b"]

INTERROGATIVE_OPEN = re.compile(r"^\s*(¿|qué |cuál|cómo|cuándo|dónde|quién|por qué|a qué|hay |se puede|puede[ns]?)", re.I)

def is_query(text: str) -> bool:
    """Must be a real question: contains '?' OR starts with interrogative."""
    if "?" in text:
        return True
    if INTERROGATIVE_OPEN.match(text):
        return True
    return False

def classify(text: str) -> str | None:
    if not is_query(text):
        return None
    t = text.lower()
    if any(re.search(p, t) for p in CAUSAL_KW):   return "causal"
    if any(re.search(p, t) for p in TEMPORAL_KW): return "temporal"
    if any(re.search(p, t) for p in MULTIHOP_KW): return "multihop"
    if any(re.search(p, t) for p in FACTUAL_KW):  return "factual"
    if "?" in text and len(text) > 15:            return "factual"
    return None

def load_recent(days: int = 30) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    for name in SOURCES:
        path = MSGS / name
        if not path.exists():
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_raw = m.get("timestamp") or m.get("ts") or m.get("time")
                if not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except (ValueError, AttributeError):
                    continue
                if ts < cutoff:
                    continue
                content = m.get("content") or m.get("message") or m.get("text") or ""
                if not isinstance(content, str) or len(content) < 10:
                    continue
                rows.append({
                    "source": name,
                    "timestamp": ts.isoformat(),
                    "from": m.get("from", "?"),
                    "to": m.get("to", "?"),
                    "content": content,
                })
    return rows

def build_dataset(target: dict[str, int], seed: int = 42) -> dict:
    random.seed(seed)
    rows = load_recent()
    buckets: dict[str, list[dict]] = {k: [] for k in target}
    for r in rows:
        cls = classify(r["content"])
        if cls and cls in buckets:
            buckets[cls].append(r)

    dataset = []
    stats = {}
    for cls, n in target.items():
        pool = buckets[cls]
        random.shuffle(pool)
        chosen = pool[:n]
        stats[cls] = {"target": n, "available": len(pool), "selected": len(chosen)}
        for i, r in enumerate(chosen):
            dataset.append({
                "query_id": f"{cls}_{i+1:03d}",
                "type": cls,
                "query": r["content"][:500],
                "source_file": r["source"],
                "source_ts": r["timestamp"],
                "ground_truth_memory_ids": [],  # to be filled manually / by LLM judge
            })
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": target,
        "stats": stats,
        "total": len(dataset),
        "queries": dataset,
    }

def main():
    target = {"factual": 40, "multihop": 30, "temporal": 20, "causal": 10}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ds = build_dataset(target)
    with open(OUT, "w") as f:
        json.dump(ds, f, indent=2, ensure_ascii=False)
    print(f"[shadow_dataset] wrote {OUT}")
    print(f"[shadow_dataset] total: {ds['total']}")
    for cls, s in ds["stats"].items():
        print(f"  {cls:10s} target={s['target']:3d} available={s['available']:4d} selected={s['selected']:3d}")

if __name__ == "__main__":
    main()
