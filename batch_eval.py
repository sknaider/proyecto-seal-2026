#!/usr/bin/env python3
"""
batch_eval.py — Evaluación masiva MedGemma ronda 3.
Lee eval_ronda3.json, envía cada pregunta al servidor localhost:8080,
calcula BERTScore (xlm-roberta-large) + keyword matching + refusal detection.
Genera reporte consolidado por categoría/idioma/dificultad.

Uso:
  python3 batch_eval.py
  python3 batch_eval.py --input data/eval_ronda3.json --output results/ronda3_eval/
  python3 batch_eval.py --label seal_v1  # tag para identificar en comparación
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

SEAL_MCP_AVAILABLE = False
try:
    # Try to import reasoning trace storage
    sys.path.insert(0, os.path.expanduser("~/IA/proyecto-seal/memory"))
    SEAL_MCP_AVAILABLE = True
except Exception:
    pass

SERVER_URL = "http://127.0.0.1:8080"
BERT_MODEL = "xlm-roberta-large"


def check_server() -> dict:
    try:
        r = requests.get(f"{SERVER_URL}/health", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"ERROR: Server not reachable at {SERVER_URL}: {e}")
        print("Start server first: python3 serve_medgemma.py")
        sys.exit(1)


def call_generate(prompt: str, max_tokens: int = 160, temperature: float = 0.3) -> dict:
    payload = {"prompt": prompt, "max_tokens": max_tokens, "temperature": temperature}
    r = requests.post(f"{SERVER_URL}/generate", json=payload, timeout=300)
    r.raise_for_status()
    return r.json()


def compute_bertscore(candidates: list[str], references: list[str]) -> list[float]:
    from bert_score import score as bert_score_fn
    print(f"  Computing BERTScore with {BERT_MODEL} for {len(candidates)} pairs...")
    _, _, F1 = bert_score_fn(
        candidates, references,
        model_type=BERT_MODEL,
        lang="es",
        verbose=False,
        device="cuda" if __import__("torch").cuda.is_available() else "cpu",
    )
    return F1.tolist()


def compute_keyword_score(response: str, keywords: list[str]) -> tuple[float, list[str]]:
    if not keywords:
        return 0.0, []
    response_lower = response.lower()
    matched = [kw for kw in keywords if kw.lower() in response_lower]
    return len(matched) / len(keywords), matched


def evaluate_dataset(
    dataset: list[dict],
    output_dir: str,
    label: str,
) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    results = []
    candidates = []
    references = []

    print(f"\n{'='*60}")
    print(f"BATCH EVAL — {label.upper()} — {len(dataset)} questions")
    print(f"{'='*60}")

    total_tokens = 0
    total_time = 0.0

    for i, item in enumerate(dataset, start=1):
        q_id = item["id"]
        question = item["pregunta"]
        expected = item["respuesta_esperada"]
        category = item["categoria"]
        lang = item["idioma"]
        difficulty = item["dificultad"]
        keywords = item.get("keywords", [])

        print(f"[{i:3d}/{len(dataset)}] ID={q_id} | {category} | {lang} | diff={difficulty}")
        print(f"         Q: {question[:100]}...")

        try:
            resp = call_generate(question)
            response_text = resp["response"]
            tokens_gen = resp["tokens_generated"]
            time_s = resp["time_s"]
            speed_tps = resp["speed_tps"]
            is_refusal = resp["is_refusal"]
            adapter = resp["adapter"]
        except Exception as e:
            print(f"         ERROR: {e}")
            results.append({
                "id": q_id, "error": str(e), "category": category,
                "lang": lang, "difficulty": difficulty,
            })
            candidates.append("")
            references.append(expected)
            continue

        kw_score, matched_kw = compute_keyword_score(response_text, keywords)
        total_tokens += tokens_gen
        total_time += time_s

        print(f"         A: {response_text[:120]}...")
        print(f"         kw={kw_score:.0%} ({len(matched_kw)}/{len(keywords)}) | "
              f"refusal={is_refusal} | {tokens_gen}tok @ {speed_tps}t/s")

        results.append({
            "id": q_id,
            "title": item.get("title", ""),
            "pregunta": question,
            "respuesta_esperada": expected,
            "respuesta_modelo": response_text,
            "categoria": category,
            "idioma": lang,
            "dificultad": difficulty,
            "keywords": keywords,
            "keyword_score": round(kw_score, 3),
            "matched_keywords": matched_kw,
            "is_refusal": is_refusal,
            "tokens_generated": tokens_gen,
            "time_s": time_s,
            "speed_tps": speed_tps,
            "adapter": adapter,
            "bert_f1": None,  # filled after batch
        })
        candidates.append(response_text)
        references.append(expected)

    # BERTScore — batch computation
    print(f"\nComputing BERTScore for {len(candidates)} pairs...")
    valid_indices = [i for i, c in enumerate(candidates) if c]
    if valid_indices:
        valid_candidates = [candidates[i] for i in valid_indices]
        valid_references = [references[i] for i in valid_indices]
        bert_scores = compute_bertscore(valid_candidates, valid_references)
        score_map = {valid_indices[i]: bert_scores[i] for i in range(len(valid_indices))}
        for j, res in enumerate(results):
            if j in score_map:
                results[j]["bert_f1"] = round(float(score_map[j]), 4)

    # Aggregate metrics
    valid_results = [r for r in results if "error" not in r and r.get("bert_f1") is not None]

    avg_bert = sum(r["bert_f1"] for r in valid_results) / len(valid_results) if valid_results else 0
    avg_kw = sum(r["keyword_score"] for r in valid_results) / len(valid_results) if valid_results else 0
    refusal_count = sum(1 for r in valid_results if r.get("is_refusal"))
    accepted = sum(1 for r in valid_results if not r.get("is_refusal") and len(r.get("respuesta_modelo", "")) > 50)

    # By category
    by_category: dict[str, list] = {}
    for r in valid_results:
        cat = r["categoria"]
        by_category.setdefault(cat, []).append(r)

    cat_summary = {}
    for cat, items in by_category.items():
        cat_summary[cat] = {
            "n": len(items),
            "avg_bert_f1": round(sum(i["bert_f1"] for i in items) / len(items), 4),
            "avg_keyword_score": round(sum(i["keyword_score"] for i in items) / len(items), 3),
            "refusals": sum(1 for i in items if i.get("is_refusal")),
        }

    # By language
    by_lang: dict[str, list] = {}
    for r in valid_results:
        by_lang.setdefault(r["idioma"], []).append(r)

    lang_summary = {}
    for lang, items in by_lang.items():
        lang_summary[lang] = {
            "n": len(items),
            "avg_bert_f1": round(sum(i["bert_f1"] for i in items) / len(items), 4),
            "avg_keyword_score": round(sum(i["keyword_score"] for i in items) / len(items), 3),
        }

    # By difficulty
    by_diff: dict[int, list] = {}
    for r in valid_results:
        by_diff.setdefault(r["dificultad"], []).append(r)

    diff_summary = {}
    for diff, items in sorted(by_diff.items()):
        diff_summary[diff] = {
            "n": len(items),
            "avg_bert_f1": round(sum(i["bert_f1"] for i in items) / len(items), 4),
            "avg_keyword_score": round(sum(i["keyword_score"] for i in items) / len(items), 3),
        }

    summary = {
        "label": label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_questions": len(dataset),
        "valid_responses": len(valid_results),
        "accepted": accepted,
        "refusals": refusal_count,
        "avg_bert_f1": round(avg_bert, 4),
        "avg_keyword_score": round(avg_kw, 3),
        "total_tokens": total_tokens,
        "total_time_s": round(total_time, 1),
        "avg_speed_tps": round(total_tokens / total_time, 1) if total_time > 0 else 0,
        "by_category": cat_summary,
        "by_language": lang_summary,
        "by_difficulty": diff_summary,
    }

    # Save JSON
    result_file = os.path.join(output_dir, f"eval_{label}_results.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved: {result_file}")

    # Save Markdown report
    md_file = os.path.join(output_dir, f"eval_{label}_report.md")
    _write_markdown_report(summary, md_file)
    print(f"Report saved: {md_file}")

    # Print summary
    print(f"\n{'='*60}")
    print(f"SUMMARY — {label.upper()}")
    print(f"{'='*60}")
    print(f"Accepted: {accepted}/{len(dataset)}")
    print(f"Refusals: {refusal_count}")
    print(f"Avg BERTScore F1: {avg_bert:.4f}")
    print(f"Avg Keyword Score: {avg_kw:.1%}")
    print(f"Speed: {summary['avg_speed_tps']} t/s avg")
    print(f"\nBy Language:")
    for lang, ls in lang_summary.items():
        print(f"  {lang}: n={ls['n']} | bert={ls['avg_bert_f1']:.4f} | kw={ls['avg_keyword_score']:.1%}")
    print(f"\nBy Difficulty:")
    for diff, ds in diff_summary.items():
        label_d = {1: "easy", 2: "medium", 3: "hard"}.get(diff, str(diff))
        print(f"  {label_d}: n={ds['n']} | bert={ds['avg_bert_f1']:.4f} | kw={ds['avg_keyword_score']:.1%}")

    return summary


def _write_markdown_report(summary: dict, path: str) -> None:
    label = summary["label"]
    lines = [
        f"# Eval Report — {label.upper()}",
        f"",
        f"**Timestamp:** {summary['timestamp']}",
        f"**Total questions:** {summary['total_questions']}",
        f"**Valid responses:** {summary['valid_responses']}",
        f"**Accepted:** {summary['accepted']}",
        f"**Refusals:** {summary['refusals']}",
        f"",
        f"## Global Metrics",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| BERTScore F1 (xlm-roberta-large) | {summary['avg_bert_f1']:.4f} |",
        f"| Keyword Score | {summary['avg_keyword_score']:.1%} |",
        f"| Avg Speed | {summary['avg_speed_tps']} t/s |",
        f"| Total Tokens | {summary['total_tokens']} |",
        f"",
        f"## By Language",
        f"| Language | N | BERTScore F1 | Keyword Score |",
        f"|----------|---|-------------|--------------|",
    ]
    for lang, ls in summary["by_language"].items():
        lines.append(f"| {lang} | {ls['n']} | {ls['avg_bert_f1']:.4f} | {ls['avg_keyword_score']:.1%} |")

    lines += [
        f"",
        f"## By Difficulty",
        f"| Difficulty | N | BERTScore F1 | Keyword Score |",
        f"|-----------|---|-------------|--------------|",
    ]
    for diff, ds in summary["by_difficulty"].items():
        label_d = {1: "easy", 2: "medium", 3: "hard"}.get(diff, str(diff))
        lines.append(f"| {label_d} | {ds['n']} | {ds['avg_bert_f1']:.4f} | {ds['avg_keyword_score']:.1%} |")

    lines += [
        f"",
        f"## By Category",
        f"| Category | N | BERTScore F1 | Keyword Score | Refusals |",
        f"|----------|---|-------------|--------------|---------|",
    ]
    for cat, cs in sorted(summary["by_category"].items(), key=lambda x: -x[1]["avg_bert_f1"]):
        lines.append(
            f"| {cat} | {cs['n']} | {cs['avg_bert_f1']:.4f} | {cs['avg_keyword_score']:.1%} | {cs['refusals']} |"
        )

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch eval MedGemma ronda 3")
    parser.add_argument("--input", default="data/eval_ronda3.json")
    parser.add_argument("--output", default="results/ronda3_eval")
    parser.add_argument("--label", default="seal_v1", help="Label for this run (seal_v1, seal_v2, base)")
    args = parser.parse_args()

    # Check server
    health = check_server()
    print(f"Server OK. Adapter: {health.get('adapter', 'unknown')}")

    # Load dataset
    if not os.path.exists(args.input):
        print(f"Dataset not found: {args.input}")
        print("Run: python3 prepare_eval_dataset.py")
        sys.exit(1)

    with open(args.input, encoding="utf-8") as f:
        dataset = json.load(f)
    print(f"Loaded {len(dataset)} questions from {args.input}")

    evaluate_dataset(dataset, args.output, args.label)
