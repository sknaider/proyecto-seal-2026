#!/usr/bin/env python3
"""
Prepara y unifica todos los datasets médicos para el Proyecto SEAL.
- Elimina duplicados
- Unifica formato
- Balancea por tema
- Filtra calidad
- Genera train/eval splits
"""
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from datasets import load_from_disk

random.seed(42)
DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
OUTPUT_TRAIN = DATA_DIR / "unified_train.json"
OUTPUT_EVAL = DATA_DIR / "unified_eval.json"
OUTPUT_STATS = DATA_DIR / "dataset_stats.json"


def hash_text(text: str) -> str:
    return hashlib.md5(text.lower().strip()[:200].encode()).hexdigest()


def load_existing_medical():
    """Load the existing 15K medical QA dataset."""
    items = []
    path = DATA_DIR / "medical_train.json"
    if path.exists():
        with open(path) as f:
            data = json.load(f)
        for item in data:
            items.append({
                "question": item.get("question", ""),
                "answer": item.get("answer", ""),
                "title": item.get("title", ""),
                "source": item.get("source", "existing"),
                "lang": item.get("lang", "es"),
            })
    print(f"  Existing medical: {len(items)} items")
    return items


def load_wiki_med_es():
    """Load Spanish medical Wikipedia articles as context for self-edit generation."""
    items = []
    path = RAW_DIR / "wiki_med_es"
    if not path.exists():
        return items
    ds = load_from_disk(str(path))
    split = ds["train"] if "train" in ds else ds
    for row in split:
        title = row.get("title", "")
        sections = row.get("sections", [])
        # Combine all paragraphs into context
        context = ""
        for sec in sections:
            paragraphs = sec.get("paragraphs", [])
            if isinstance(paragraphs, list):
                context += " ".join(paragraphs) + "\n"
        if len(context.strip()) > 100:
            items.append({
                "question": f"Explica detalladamente sobre: {title}",
                "answer": context.strip()[:2000],
                "title": f"Wikipedia Médica — {title}",
                "source": "wiki_med_es",
                "lang": "es",
            })
    print(f"  Wiki Med ES: {len(items)} items")
    return items


def load_aya_exams():
    """Load aya medical exams in Spanish."""
    items = []
    path = RAW_DIR / "aya_med_es"
    if not path.exists():
        return items
    ds = load_from_disk(str(path))
    for split_name in ds:
        for row in ds[split_name]:
            q = row.get("question", row.get("input", ""))
            a = row.get("answer", row.get("output", row.get("target", "")))
            if q and a:
                items.append({
                    "question": str(q),
                    "answer": str(a),
                    "title": "Examen Médico AYA",
                    "source": "aya_med_es",
                    "lang": "es",
                })
    print(f"  Aya Exams ES: {len(items)} items")
    return items


def load_medical_spanish():
    """Load adriana98/medical_spanish dataset."""
    items = []
    path = RAW_DIR / "medical_spanish"
    if not path.exists():
        return items
    ds = load_from_disk(str(path))
    for split_name in ds:
        for row in ds[split_name]:
            q = row.get("question", row.get("input", row.get("instruction", "")))
            a = row.get("answer", row.get("output", row.get("response", "")))
            if q and a:
                items.append({
                    "question": str(q),
                    "answer": str(a),
                    "title": "Medical Spanish",
                    "source": "medical_spanish",
                    "lang": "es",
                })
    print(f"  Medical Spanish: {len(items)} items")
    return items


def load_lavita():
    """Load lavita all-processed medical QA (English)."""
    items = []
    path = RAW_DIR / "lavita_all"
    if not path.exists():
        return items
    ds = load_from_disk(str(path))
    split = ds["train"] if "train" in ds else ds
    for row in split:
        instruction = row.get("instruction", "")
        inp = row.get("input", "")
        output = row.get("output", "")
        q = f"{instruction} {inp}".strip() if inp else instruction
        if q and output and len(output) > 30:
            items.append({
                "question": str(q)[:1000],
                "answer": str(output)[:2000],
                "title": "Medical QA (EN)",
                "source": "lavita",
                "lang": "en",
            })
    print(f"  Lavita (EN): {len(items)} items")
    return items


def load_mmlu_genetics():
    """Load MMLU Medical Genetics in Spanish."""
    items = []
    path = RAW_DIR / "mmlu_med_genetics_es"
    if not path.exists():
        return items
    ds = load_from_disk(str(path))
    for split_name in ds:
        for row in ds[split_name]:
            q = row.get("question", row.get("input", ""))
            a = row.get("answer", row.get("output", row.get("target", "")))
            if q and a:
                items.append({
                    "question": str(q),
                    "answer": str(a),
                    "title": "MMLU Medical Genetics (ES)",
                    "source": "mmlu_genetics_es",
                    "lang": "es",
                })
    print(f"  MMLU Genetics ES: {len(items)} items")
    return items


def deduplicate(items: list[dict]) -> list[dict]:
    """Remove duplicates based on question hash."""
    seen = set()
    unique = []
    for item in items:
        h = hash_text(item["question"])
        if h not in seen:
            seen.add(h)
            unique.append(item)
    removed = len(items) - len(unique)
    print(f"  Deduplicación: {len(items)} → {len(unique)} ({removed} duplicados removidos)")
    return unique


def filter_quality(items: list[dict]) -> list[dict]:
    """Filter low-quality items."""
    filtered = []
    for item in items:
        q = item["question"].strip()
        a = item["answer"].strip()
        # Skip empty or too short
        if len(q) < 10 or len(a) < 20:
            continue
        # Skip if answer is just the question repeated
        if q.lower() == a.lower():
            continue
        filtered.append(item)
    removed = len(items) - len(filtered)
    print(f"  Filtro calidad: {len(items)} → {len(filtered)} ({removed} removidos)")
    return filtered


def main():
    print("=" * 60)
    print("Proyecto SEAL — Preparación de Datasets")
    print("=" * 60)

    # Load all sources
    print("\n📥 Cargando datasets...")
    all_items = []
    all_items.extend(load_existing_medical())
    all_items.extend(load_wiki_med_es())
    all_items.extend(load_aya_exams())
    all_items.extend(load_medical_spanish())
    all_items.extend(load_lavita())
    all_items.extend(load_mmlu_genetics())

    print(f"\n📊 Total raw: {len(all_items)} items")

    # Deduplicate
    print("\n🔍 Deduplicando...")
    all_items = deduplicate(all_items)

    # Quality filter
    print("\n✂️ Filtrando calidad...")
    all_items = filter_quality(all_items)

    # Shuffle
    random.shuffle(all_items)

    # Stats
    lang_counts = Counter(item["lang"] for item in all_items)
    source_counts = Counter(item["source"] for item in all_items)

    print(f"\n📈 Dataset final: {len(all_items)} items")
    print(f"   Por idioma: {dict(lang_counts)}")
    print(f"   Por fuente: {dict(source_counts)}")

    # Split: 95% train, 5% eval
    eval_size = min(500, len(all_items) // 20)
    eval_items = all_items[:eval_size]
    train_items = all_items[eval_size:]

    print(f"\n💾 Guardando...")
    print(f"   Train: {len(train_items)} items → {OUTPUT_TRAIN}")
    print(f"   Eval:  {len(eval_items)} items → {OUTPUT_EVAL}")

    with open(OUTPUT_TRAIN, "w", encoding="utf-8") as f:
        json.dump(train_items, f, ensure_ascii=False, indent=2)

    with open(OUTPUT_EVAL, "w", encoding="utf-8") as f:
        json.dump(eval_items, f, ensure_ascii=False, indent=2)

    stats = {
        "total_items": len(all_items),
        "train_items": len(train_items),
        "eval_items": len(eval_items),
        "by_language": dict(lang_counts),
        "by_source": dict(source_counts),
    }
    with open(OUTPUT_STATS, "w") as f:
        json.dump(stats, f, indent=2)

    print(f"\n✅ Preparación completa. Stats guardados en {OUTPUT_STATS}")
    print("=" * 60)


if __name__ == "__main__":
    main()
