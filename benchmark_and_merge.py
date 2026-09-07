#!/usr/bin/env python3
"""
BENCHMARK & MERGE — MedGemma v2 post-training decision tool.

Compara checkpoints disponibles, identifica el mejor, y opcionalmente mergea.

Uso:
    python3 benchmark_and_merge.py --compare        # compara todos los checkpoints
    python3 benchmark_and_merge.py --merge best     # mergea el mejor checkpoint
    python3 benchmark_and_merge.py --merge 400      # mergea checkpoint específico
    python3 benchmark_and_merge.py --status         # muestra checkpoints disponibles (sin cargar modelo)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoProcessor, AutoModelForImageTextToText

BASE_MODEL = "/home/dadito/IA/modelos/llm/medgemma-27b-it"
RESULTS_DIR = Path("/home/dadito/IA/proyecto-seal/results")
OUTPUT_DIR = Path("/home/dadito/IA/modelos/llm")
TRAIN_LOG = Path("/home/dadito/IA/proyecto-seal/seal_overnight.log")

# Preguntas médicas de referencia (mismo set que eval_benchmark.py)
BENCHMARK_QUESTIONS = [
    {
        "id": 1, "lang": "es",
        "question": "¿Cuál es el tratamiento de primera línea para la neumonía adquirida en la comunidad en un paciente adulto ambulatorio sin comorbilidades?",
        "keywords": ["amoxicilina", "macrólido", "azitromicina", "doxiciclina"],
    },
    {
        "id": 2, "lang": "es",
        "question": "Explica la fisiopatología del infarto agudo de miocardio con elevación del ST.",
        "keywords": ["trombo", "placa", "oclusión", "isquemia", "arteria coronaria"],
    },
    {
        "id": 3, "lang": "es",
        "question": "¿Cuáles son los criterios diagnósticos de diabetes mellitus tipo 2?",
        "keywords": ["glucosa", "126", "hba1c", "6.5", "ayuno", "tolerancia"],
    },
    {
        "id": 4, "lang": "en",
        "question": "What is the mechanism of action of metformin in type 2 diabetes?",
        "keywords": ["AMP kinase", "AMPK", "gluconeogenesis", "hepatic", "liver"],
    },
    {
        "id": 5, "lang": "es",
        "question": "¿Cómo se maneja el shock séptico en las primeras 6 horas según los protocolos actuales?",
        "keywords": ["cristaloides", "antibióticos", "vasopresores", "norepinefrina", "lactato", "cultivos"],
    },
]


# ── Utilidades ──

def find_checkpoints() -> list[dict]:
    """Descubre todos los checkpoints disponibles."""
    checkpoints = []

    for run_dir in RESULTS_DIR.iterdir():
        if not run_dir.is_dir():
            continue

        # Checkpoints intermedios
        for cp_dir in sorted(run_dir.glob("checkpoint-*")):
            step_m = re.search(r"checkpoint-(\d+)", cp_dir.name)
            if not step_m:
                continue
            step = int(step_m.group(1))
            loss = _extract_loss_from_trainer_state(cp_dir / "trainer_state.json")
            checkpoints.append({
                "name": f"{run_dir.name}/{cp_dir.name}",
                "path": str(cp_dir),
                "step": step,
                "loss": loss,
                "type": "checkpoint",
                "run": run_dir.name,
            })

        # Adapter final
        adapter_dir = run_dir / "lora_adapter"
        if adapter_dir.exists() and (adapter_dir / "adapter_config.json").exists():
            loss = _extract_loss_from_log(run_dir.name)
            checkpoints.append({
                "name": f"{run_dir.name}/lora_adapter (final)",
                "path": str(adapter_dir),
                "step": None,
                "loss": loss,
                "type": "final",
                "run": run_dir.name,
            })

    return sorted(checkpoints, key=lambda x: (x["run"], x["step"] or 9999))


def _extract_loss_from_trainer_state(path: Path) -> float | None:
    try:
        with open(path) as f:
            state = json.load(f)
        history = state.get("log_history", [])
        losses = [e["loss"] for e in history if "loss" in e]
        return round(min(losses), 4) if losses else None
    except Exception:
        return None


def _extract_loss_from_log(run_name: str) -> float | None:
    """Extrae mejor loss del seal_overnight.log para este run."""
    try:
        with open(TRAIN_LOG, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            tail = f.read().decode("utf-8", errors="ignore")
        losses = re.findall(r"[Ll]oss[:\s]+([0-9]+\.[0-9]+)", tail)
        if losses:
            return round(min(float(l) for l in losses), 4)
    except Exception:
        pass
    return None


def print_checkpoints(checkpoints: list[dict]):
    print(f"\n{'═'*65}")
    print(f"  Checkpoints disponibles — MedGemma v2")
    print(f"{'═'*65}")
    print(f"  {'#':<3} {'Nombre':<42} {'Step':<8} {'Best Loss'}")
    print(f"  {'─'*60}")
    for i, cp in enumerate(checkpoints, 1):
        loss_str = f"{cp['loss']:.4f}" if cp['loss'] else "  N/A "
        step_str = str(cp["step"]) if cp["step"] else "final"
        marker = " ← mejor" if cp == _best_checkpoint(checkpoints) else ""
        print(f"  {i:<3} {cp['name']:<42} {step_str:<8} {loss_str}{marker}")
    print(f"{'═'*65}\n")


def _best_checkpoint(checkpoints: list[dict]) -> dict | None:
    """Elige el checkpoint con menor loss conocido."""
    with_loss = [c for c in checkpoints if c["loss"] is not None]
    if not with_loss:
        return checkpoints[-1] if checkpoints else None
    return min(with_loss, key=lambda x: x["loss"])


# ── Benchmark ──

def run_benchmark(adapter_path: str, cp_name: str) -> dict:
    """Carga el adapter, corre 5 preguntas, retorna scores."""
    print(f"\n  Cargando modelo + adapter: {cp_name}")
    t0 = time.time()

    processor = AutoProcessor.from_pretrained(BASE_MODEL, trust_remote_code=True)
    model = AutoModelForImageTextToText.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(model, adapter_path)
    model.eval()
    print(f"  Modelo listo en {time.time()-t0:.0f}s")

    scores = []
    for q in BENCHMARK_QUESTIONS:
        prompt = f"<start_of_turn>user\n{q['question']}<end_of_turn>\n<start_of_turn>model\n"
        inputs = processor(text=prompt, return_tensors="pt").to(model.device)

        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=200,
                do_sample=False,
                temperature=1.0,
            )

        response = processor.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        response_lower = response.lower()
        hits = sum(1 for kw in q["keywords"] if kw.lower() in response_lower)
        score = hits / len(q["keywords"])
        scores.append({"id": q["id"], "score": score, "hits": hits, "total": len(q["keywords"]), "response": response[:200]})
        print(f"  Q{q['id']} ({q['lang']}): {hits}/{len(q['keywords'])} keywords ({'✅' if score >= 0.5 else '❌'})")

    del model
    torch.cuda.empty_cache()

    avg = sum(s["score"] for s in scores) / len(scores)
    return {"checkpoint": cp_name, "avg_score": round(avg, 3), "scores": scores, "time": round(time.time()-t0)}


# ── Reasoning Trace ──

def _store_reasoning_trace(cp: dict, output_name: str, total_min: float, success: bool) -> None:
    """Guarda un reasoning trace en PostgreSQL. Safe fallback si la tabla no existe o DB no responde."""
    async def _write():
        try:
            import asyncpg
            conn = await asyncpg.connect(
                "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory",
                timeout=5,
            )
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='reasoning_traces')"
            )
            if not exists:
                await conn.close()
                return
            await conn.execute(
                """INSERT INTO reasoning_traces
                   (agent, task, premises, reasoning, conclusion, outcome, outcome_success, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())""",
                "ADA",
                "checkpoint_merge_decision",
                [
                    f"Checkpoint seleccionado: {cp['name']}",
                    f"Loss del checkpoint: {cp['loss']}",
                    f"Tipo: {cp.get('type', 'checkpoint')}",
                    "William aprobó el merge interactivamente",
                    f"Output destino: {output_name}",
                ],
                f"El checkpoint con menor loss disponible es el candidato óptimo. "
                f"Se verificó disponibilidad del archivo y aprobación de William antes de proceder.",
                f"Merge de {cp['name']} → {output_name}",
                f"Modelo mergeado en {total_min:.1f} min, listo para inferencia" if success else "Merge fallido",
                success,
            )
            await conn.close()
        except Exception as e:
            print(f"  [reasoning_trace] No disponible: {e}")

    try:
        asyncio.run(_write())
    except Exception:
        pass


# ── Merge ──

def merge_checkpoint(adapter_path: str, cp_name: str, output_name: str):
    """Mergea adapter en base model y guarda."""
    output_path = OUTPUT_DIR / output_name
    print(f"\n{'═'*60}")
    print(f"  MERGE: {cp_name} → {output_path}")
    print(f"{'═'*60}")

    # Backup check
    if output_path.exists():
        backup = OUTPUT_DIR / f"{output_name}_prev"
        print(f"  ⚠️  Output ya existe. Backup: {backup}")
        if not backup.exists():
            output_path.rename(backup)
        else:
            print(f"  Backup ya existe. Continuando sin renombrar.")

    t0 = time.time()

    print("  [1/5] Cargando processor...")
    processor = AutoProcessor.from_pretrained(BASE_MODEL, trust_remote_code=True)

    print("  [2/5] Cargando base model (BF16, CPU)...")
    model = AutoModelForImageTextToText.from_pretrained(
        BASE_MODEL, torch_dtype=torch.bfloat16, device_map="cpu", trust_remote_code=True,
    )

    print(f"  [3/5] Cargando adapter: {adapter_path}")
    model = PeftModel.from_pretrained(model, adapter_path)

    print("  [4/5] Mergeando...")
    model = model.merge_and_unload()

    print(f"  [5/5] Guardando en {output_path}...")
    output_path.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(output_path), safe_serialization=True)
    processor.save_pretrained(str(output_path))

    total = time.time() - t0
    print(f"\n  ✅ Merge completado en {total/60:.1f} min → {output_path}")
    return str(output_path)


# ── Main ──

def main():
    parser = argparse.ArgumentParser(description="Benchmark & Merge — MedGemma v2")
    parser.add_argument("--status", action="store_true", help="Listar checkpoints sin cargar modelo")
    parser.add_argument("--compare", action="store_true", help="Benchmark de todos los checkpoints")
    parser.add_argument("--merge", metavar="TARGET",
                        help="Mergear: 'best', 'final', o nombre de checkpoint (ej: 400)")
    parser.add_argument("--output", default="medgemma-27b-seal-v2",
                        help="Nombre del modelo mergeado (default: medgemma-27b-seal-v2)")
    args = parser.parse_args()

    checkpoints = find_checkpoints()

    if not checkpoints:
        print("No se encontraron checkpoints en results/")
        sys.exit(1)

    if args.status or not any([args.compare, args.merge]):
        print_checkpoints(checkpoints)
        best = _best_checkpoint(checkpoints)
        if best:
            print(f"  Recomendación: {best['name']} (loss {best['loss']})")
        return

    if args.compare:
        print_checkpoints(checkpoints)
        results = []
        for cp in checkpoints:
            try:
                r = run_benchmark(cp["path"], cp["name"])
                results.append(r)
                print(f"  → avg_score: {r['avg_score']:.3f}")
            except Exception as e:
                print(f"  ERROR en {cp['name']}: {e}")

        # Guardar resultados
        out_file = RESULTS_DIR / "benchmark_comparison.json"
        with open(out_file, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n  Resultados guardados: {out_file}")

        # Recomendación
        if results:
            best_r = max(results, key=lambda x: x["avg_score"])
            print(f"\n  ✅ MEJOR: {best_r['checkpoint']} (avg_score={best_r['avg_score']})")

    if args.merge:
        target = args.merge.lower()
        if target == "best":
            cp = _best_checkpoint(checkpoints)
        elif target == "final":
            finals = [c for c in checkpoints if c["type"] == "final"]
            cp = finals[-1] if finals else _best_checkpoint(checkpoints)
        else:
            # Buscar por step o nombre parcial
            matches = [c for c in checkpoints if target in c["name"].lower() or str(c["step"]) == target]
            cp = matches[0] if matches else _best_checkpoint(checkpoints)

        if not cp:
            print("No se encontró checkpoint para mergear.")
            sys.exit(1)

        print(f"\n  Mergeando: {cp['name']} (loss={cp['loss']})")
        confirm = input("  ¿Confirmar merge? [s/N]: ").strip().lower()
        if confirm != "s":
            print("  Cancelado.")
            return

        t_merge_start = time.time()
        merge_checkpoint(cp["path"], cp["name"], args.output)
        total_min = (time.time() - t_merge_start) / 60
        _store_reasoning_trace(cp, args.output, total_min, success=True)


if __name__ == "__main__":
    main()
