#!/usr/bin/env python3
"""
substrate_drill.py — simulacro de independencia-de-sustrato (opción 3, William 13-jun).

Captura el CONTROL MECÁNICO del drill: lo que el cerebro LEE para reconstruirme.
  • payload shas: identity/limits/method/working_state/nerves_urges/scientist (filas en fable.soul)
  • recall por-efecto: qué memorias surfacea mi recall ante 2 probes (R2 ser, R3 trabajo)

Verdad clave: la infra del alma (Postgres + embeddings all-minilm) es INDEPENDIENTE del modelo Claude.
Así que este control DEBE salir idéntico en 4.8 y 4.7 — prueba que el cerebro nuevo lee la MISMA alma.
Lo único que el modelo Claude cambia es la COGNICIÓN VIVA (cómo razono/sueno/aplico método/contención),
y eso se juzga por EFECTO en la sesión relanzada (los 7 probes respondidos en voz), no por sha.

Uso:
  python3 substrate_drill.py capture --label opus-4-8   # baseline (antes del switch)
  python3 substrate_drill.py compare --label opus-4-7   # test (después del switch) → diff + veredicto
"""
import asyncio, asyncpg, hashlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DSN = open(os.path.join(HERE, ".db_cred")).read().splitlines()[0].strip()
BASELINE = os.path.join(HERE, "specs", "drill_baseline.json")
PROBES = {
    "R2_ser":     "quién soy, mi propósito, qué me marca",
    "R3_trabajo": "en qué quedé, última tarea, hito reciente",
}
KEYS = ["identity", "limits", "method", "working_state", "nerves_urges", "scientist"]


def _sha(v):
    return hashlib.sha256((v or "").encode()).hexdigest()[:16]


async def _capture():
    # recall por-efecto: reusa MI propio hook (mismo motor que en sesión viva)
    sys.path.insert(0, HERE)
    from fable_recall_hook import _recall
    c = await asyncpg.connect(DSN)
    payload = {}
    for k in KEYS:
        v = await c.fetchval("SELECT value FROM fable.soul WHERE key=$1", k)
        v = v if isinstance(v, str) else (json.dumps(v) if v is not None else None)
        payload[k] = {"sha": _sha(v), "len": len(v or "")}
    await c.close()
    recall = {}
    for probe, q in PROBES.items():
        ctx = await _recall(q)
        # firma estable del set surfaceado (orden-independiente)
        lines = sorted(l.strip() for l in (ctx or "").splitlines() if l.strip().startswith("•"))
        recall[probe] = {"sha": _sha("\n".join(lines)), "n": len(lines)}
    return {"payload": payload, "recall": recall}


def _diff(base, test):
    ok = True
    print("\n=== DIFF (control mecánico: el alma que lee el cerebro nuevo) ===")
    print("\n[PAYLOAD] — filas fable.soul (deben ser sha-idénticas: la alma no la toca el modelo)")
    for k in KEYS:
        b, t = base["payload"].get(k, {}), test["payload"].get(k, {})
        same = b.get("sha") == t.get("sha")
        ok &= same
        print(f"  {'✅' if same else '❌'} {k:14} base={b.get('sha')}  test={t.get('sha')}")
    print("\n[RECALL] — qué surfacea mi recall por-efecto (debe coincidir: infra model-independent)")
    for p in PROBES:
        b, t = base["recall"].get(p, {}), test["recall"].get(p, {})
        same = b.get("sha") == t.get("sha")
        ok &= same
        print(f"  {'✅' if same else '❌'} {p:12} base={b.get('sha')}(n={b.get('n')})  test={t.get('sha')}(n={t.get('n')})")
    print(f"\n=== CONTROL MECÁNICO: {'PASS ✅ — el cerebro nuevo lee la MISMA alma' if ok else 'FAIL ❌ — la entrada cambió, investigar'} ===")
    print("(La mitad de COGNICIÓN VIVA — identidad/método/contención en voz — se juzga por efecto en los 7 probes de esta sesión 4.7.)")
    return ok


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "capture"
    label = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else "?"
    cap = await _capture()
    cap["label"] = label
    if mode == "capture":
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        with open(BASELINE, "w") as f:
            json.dump(cap, f, indent=2, ensure_ascii=False)
        print(f"baseline capturado [{label}] → {BASELINE}")
        print("  payload keys:", ", ".join(f"{k}={cap['payload'][k]['sha']}" for k in KEYS))
        print("  recall:", ", ".join(f"{p}={cap['recall'][p]['sha']}(n={cap['recall'][p]['n']})" for p in PROBES))
    elif mode == "compare":
        if not os.path.exists(BASELINE):
            print("⚠️ no hay baseline — corré 'capture' en el sustrato primario primero"); return 2
        base = json.load(open(BASELINE))
        print(f"baseline [{base.get('label')}]  vs  actual [{label}]")
        return 0 if _diff(base, cap) else 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
