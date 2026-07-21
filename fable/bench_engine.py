#!/usr/bin/env python3
"""
bench_engine.py — medidor apples-to-apples de un endpoint OpenAI-compatible (vLLM o llama.cpp).
PoC GLM-5.2/vLLM (FABLE, 16-jul). Mide lo que importa del MOTOR:
  1) single-stream tok/s
  2) throughput AGREGADO con concurrencia (4/8/12 streams) — la ventaja real de vLLM (batching)
  3) TTFT aprox (latencia de la 1ª respuesta)

Cuenta usage.completion_tokens (NO chunks SSE), non-streaming para conteo exacto.
Mismo script para los 2 motores → comparación justa.

Uso:
  bench_engine.py --url http://192.168.68.70:8000/v1/chat/completions \
                  --model qwen2.5-vl-7b --streams 1,4,8,12 --max-tokens 256
"""
import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DEFAULT_PROMPT = ("Explica en detalle, paso a paso, cómo funciona la memoria de un sistema "
                  "multi-agente y por qué la persistencia importa. Sé exhaustivo y técnico.")


def one_request(url, model, prompt, max_tokens, timeout):
    """Un request non-streaming. Devuelve (completion_tokens, latencia_s) o (0, latencia) si falla."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        dt = time.time() - t0
        ct = (data.get("usage") or {}).get("completion_tokens", 0)
        return ct, dt
    except Exception:
        return 0, time.time() - t0


def run_level(url, model, prompt, max_tokens, concurrency, timeout):
    """Lanza `concurrency` requests EN PARALELO. Throughput agregado = sum(tokens)/wall_time del batch."""
    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(one_request, url, model, prompt, max_tokens, timeout)
                for _ in range(concurrency)]
        for f in futs:
            results.append(f.result())
    wall = time.time() - t0
    toks = sum(r[0] for r in results)
    lats = [r[1] for r in results]
    ok = sum(1 for r in results if r[0] > 0)
    agg_tps = toks / wall if wall > 0 else 0
    per_stream = (toks / len(results)) / (sum(lats) / len(lats)) if lats and sum(lats) > 0 else 0
    return {
        "concurrency": concurrency, "ok": ok, "total": len(results),
        "tokens": toks, "wall_s": round(wall, 2),
        "agg_tok_s": round(agg_tps, 1), "avg_lat_s": round(sum(lats) / len(lats), 2),
        "per_stream_tok_s": round(per_stream, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="endpoint /v1/chat/completions")
    ap.add_argument("--model", required=True)
    ap.add_argument("--streams", default="1,4,8,12", help="niveles de concurrencia, coma-separados")
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--label", default="", help="etiqueta del motor, ej 'vLLM' o 'llama.cpp'")
    ap.add_argument("--warmup", type=int, default=3, help="requests de calentamiento (descartados)")
    ap.add_argument("--rounds", type=int, default=3, help="corridas por nivel; se reporta la MEJOR")
    args = ap.parse_args()

    levels = [int(x) for x in args.streams.split(",") if x.strip()]
    print(f"# Benchmark motor: {args.label or args.url}")
    print(f"# modelo={args.model} max_tokens={args.max_tokens} prompt_len={len(args.prompt)}ch "
          f"warmup={args.warmup} rounds={args.rounds} (reporta MEJOR de {args.rounds})")

    # Warmup: calienta JIT/KV/cache del server; se descarta (elimina el cold-start que sesgaba).
    for _ in range(args.warmup):
        one_request(args.url, args.model, args.prompt, min(64, args.max_tokens), args.timeout)

    print(f"{'conc':>5} {'ok':>6} {'tokens':>8} {'wall_s':>7} {'AGG tok/s':>10} {'per-stream':>11} {'avg_lat_s':>10}")
    rows = []
    for c in levels:
        # best-of-N por nivel: el MEJOR agg_tok/s (peak alcanzable, robusto a stragglers puntuales).
        best = None
        for _ in range(args.rounds):
            r = run_level(args.url, args.model, args.prompt, args.max_tokens, c, args.timeout)
            if best is None or r["agg_tok_s"] > best["agg_tok_s"]:
                best = r
        rows.append(best)
        r = best
        print(f"{r['concurrency']:>5} {r['ok']:>3}/{r['total']:<2} {r['tokens']:>8} "
              f"{r['wall_s']:>7} {r['agg_tok_s']:>10} {r['per_stream_tok_s']:>11} {r['avg_lat_s']:>10}")
    out = {"engine": args.label, "url": args.url, "model": args.model,
           "max_tokens": args.max_tokens, "warmup": args.warmup, "rounds": args.rounds, "rows": rows}
    print("\n# JSON:\n" + json.dumps(out))


if __name__ == "__main__":
    main()
