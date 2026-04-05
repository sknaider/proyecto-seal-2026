#!/usr/bin/env python3
"""
compare_models.py — Compara resultados de eval entre modelos.
Lee los JSON generados por batch_eval.py y genera tabla comparativa.

Uso:
  python3 compare_models.py
  python3 compare_models.py --results results/ronda3_eval --output results/ronda3_eval/comparison.md
"""
import argparse
import json
import os
import glob


def load_summaries(results_dir: str) -> list[dict]:
    """Load all eval_*_results.json from the results directory."""
    pattern = os.path.join(results_dir, "eval_*_results.json")
    files = sorted(glob.glob(pattern))
    summaries = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        summaries.append(data["summary"])
        print(f"Loaded: {f} — label={data['summary']['label']}")
    return summaries


def find_best(summaries: list[dict], metric: str) -> str:
    best = max(summaries, key=lambda s: s.get(metric, 0))
    return best["label"]


def delta_str(val: float, ref: float, fmt: str = ".4f") -> str:
    d = val - ref
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:{fmt}}"


def write_comparison(summaries: list[dict], output_path: str) -> None:
    if len(summaries) < 2:
        print("Need at least 2 models to compare.")
        return

    # Sort: base first, then by label
    summaries = sorted(summaries, key=lambda s: (0 if s["label"] == "base" else 1, s["label"]))
    base = summaries[0]
    others = summaries[1:]

    lines = [
        "# Model Comparison — MedGemma Ronda 3",
        "",
        "## Global Metrics",
        "",
    ]

    # Header
    headers = ["Metric", base["label"]] + [s["label"] for s in others]
    sep = ["-" * max(len(h), 6) for h in headers]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(sep) + " |")

    metrics = [
        ("BERTScore F1", "avg_bert_f1", ".4f"),
        ("Keyword Score", "avg_keyword_score", ".3f"),
        ("Accepted", "accepted", "d"),
        ("Refusals", "refusals", "d"),
        ("Avg Speed (t/s)", "avg_speed_tps", ".1f"),
    ]

    for label_m, key, fmt in metrics:
        base_val = base.get(key, 0)
        row = [label_m, f"{base_val:{fmt}}"]
        for s in others:
            val = s.get(key, 0)
            if fmt == "d":
                d = val - base_val
                sign = "+" if d >= 0 else ""
                row.append(f"{val:{fmt}} ({sign}{d})")
            else:
                d = val - base_val
                sign = "+" if d >= 0 else ""
                row.append(f"{val:{fmt}} ({sign}{d:{fmt}})")
        lines.append("| " + " | ".join(row) + " |")

    lines += ["", "## By Language", ""]

    # Get all languages
    all_langs = set()
    for s in summaries:
        all_langs.update(s.get("by_language", {}).keys())

    for lang in sorted(all_langs):
        lines.append(f"### {lang.upper()}")
        lines.append("")
        lheaders = ["Metric", base["label"]] + [s["label"] for s in others]
        lsep = ["-" * max(len(h), 6) for h in lheaders]
        lines.append("| " + " | ".join(lheaders) + " |")
        lines.append("| " + " | ".join(lsep) + " |")

        base_lang = base.get("by_language", {}).get(lang, {})
        for metric_name, key, fmt in [("BERTScore F1", "avg_bert_f1", ".4f"),
                                       ("Keyword Score", "avg_keyword_score", ".3f")]:
            bv = base_lang.get(key, 0)
            row = [metric_name, f"{bv:{fmt}}"]
            for s in others:
                sv = s.get("by_language", {}).get(lang, {}).get(key, 0)
                d = sv - bv
                sign = "+" if d >= 0 else ""
                row.append(f"{sv:{fmt}} ({sign}{d:{fmt}})")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    lines += ["## By Category", ""]
    all_cats = set()
    for s in summaries:
        all_cats.update(s.get("by_category", {}).keys())

    cheaders = ["Category"] + [s["label"] for s in summaries]
    csep = ["-" * max(len(h), 6) for h in cheaders]
    lines.append("### BERTScore F1")
    lines.append("| " + " | ".join(cheaders) + " |")
    lines.append("| " + " | ".join(csep) + " |")

    for cat in sorted(all_cats):
        row = [cat]
        base_val = base.get("by_category", {}).get(cat, {}).get("avg_bert_f1", 0)
        row.append(f"{base_val:.4f}")
        for s in others:
            val = s.get("by_category", {}).get(cat, {}).get("avg_bert_f1", 0)
            d = val - base_val
            sign = "+" if d >= 0 else ""
            row.append(f"{val:.4f} ({sign}{d:.4f})")
        lines.append("| " + " | ".join(row) + " |")

    lines += [
        "",
        "## Conclusions",
        "",
    ]

    # Auto-conclusions
    best_bert = find_best(summaries, "avg_bert_f1")
    best_kw = find_best(summaries, "avg_keyword_score")
    lines.append(f"- **Best BERTScore F1:** {best_bert}")
    lines.append(f"- **Best Keyword Score:** {best_kw}")

    # Check for regressions
    for s in others:
        bert_delta = s["avg_bert_f1"] - base["avg_bert_f1"]
        kw_delta = s["avg_keyword_score"] - base["avg_keyword_score"]
        if bert_delta < -0.01:
            lines.append(f"- ⚠️ REGRESSION: {s['label']} BERTScore {bert_delta:+.4f} vs base")
        if kw_delta < -0.05:
            lines.append(f"- ⚠️ REGRESSION: {s['label']} Keywords {kw_delta:+.1%} vs base")
        if bert_delta > 0.01:
            lines.append(f"- ✅ IMPROVEMENT: {s['label']} BERTScore {bert_delta:+.4f} vs base")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nComparison saved: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare eval results across models")
    parser.add_argument("--results", default="results/ronda3_eval")
    parser.add_argument("--output", default="results/ronda3_eval/comparison.md")
    args = parser.parse_args()

    summaries = load_summaries(args.results)
    if not summaries:
        print(f"No eval result files found in {args.results}")
        print("Run batch_eval.py first for each model.")
    else:
        write_comparison(summaries, args.output)
        # Also print quick summary to console
        print("\n=== QUICK COMPARISON ===")
        for s in summaries:
            print(f"{s['label']:12s}: bert={s['avg_bert_f1']:.4f} | kw={s['avg_keyword_score']:.1%} | "
                  f"accepted={s['accepted']}/{s['total_questions']} | refusals={s['refusals']}")
