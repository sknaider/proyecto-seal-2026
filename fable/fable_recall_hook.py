#!/usr/bin/env python3
"""
fable_recall_hook.py — recall mid-sesión de FABLE (UserPromptSubmit). SOUL v2 prototype.

Hace para FABLE lo que active_recall_hook hace para la familia: inyecta memorias RELEVANTES
de MIS dos capas — emocional (fable.emotional_memory) y operativa (fable.bitacora).

SEMÁNTICO primario (embeddings all-minilm, recuerda por SIGNIFICADO) + keyword fallback
(si Ollama está caído, no me quedo sin recall). Diseño dual: el SER surfacea liberal
(umbral bajo), el TRABAJO preciso (umbral alto) — la filosofía dual en el propio recall.

Contrato: <2s, fail-safe ({} si algo falla, nunca bloquea), self-contained. Solo si SEAL_AGENT=FABLE.
NO lee soul_v3 (interioridad de la familia).
"""
import sys, os, json, re, math, asyncio, urllib.request

DSN = open("/home/dadito/IA/proyecto-seal/fable/.db_cred").read().splitlines()[0].strip()
_STOP = {"que","con","los","las","del","para","por","una","como","esto","esta","pero","más","muy",
         "the","and","for","you","tu","mi","de","el","la","en","es","se","lo","un","y","a","o",
         "fable","seal","equipo","agente","agentes","william","memoria","memorias","trabajo"}


def _norm(text):
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFD", (text or "").lower())
                   if unicodedata.category(ch) != "Mn")


def _keywords(text):
    return [w for w in dict.fromkeys(re.findall(r"\w{4,}", _norm(text))) if w not in _STOP][:6]


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(y * y for y in b)) or 1e-9
    return dot / (na * nb)


def _embed(text):
    try:
        req = urllib.request.Request("http://localhost:11434/api/embeddings",
            data=json.dumps({"model": "all-minilm", "prompt": (text or "")[:1000]}).encode(),
            headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=1.8).read())["embedding"]
    except Exception:
        return None


# La CURA aplicada a MÍ (lo que cure en la familia: importancia plana = no olvidar = recall distorsionado).
# El VALOR de una memoria operativa se aproxia por su TIPO (señal objetiva, no palabra-urgencia ni uso inflado).
# Así el recall surfacea por VALOR, no plano: lo trivial se hunde bajo el umbral = se OLVIDA de la recuperación
# (sin borrarlo — sigue en la tabla, honesto). Proxy del calibrado-cuantil completo de soul_cognitive_priority.
_TIPO_W = {"hito": 1.0, "milestone": 1.0, "milestone_emotional": 1.0, "identity": 1.0, "relationship": 1.0,
           "leccion": 0.9, "afinamiento": 0.9, "spec": 0.85, "incidente": 0.85, "rubrica": 0.6,
           "trabajo": 0.6, "revision": 0.55, "adjudicacion": 0.5, "project": 0.4, "presentacion": 0.3}


def _importance(tipo):
    return _TIPO_W.get((tipo or "").lower(), 0.55)


def _fmt(emo, op):
    b = []
    if emo:
        b.append("[FABLE memoria emocional — el ser]"); b += [f"  • {t[:160]}" for t in emo]
    if op:
        b.append("[FABLE memoria operativa — trabajo previo relevante]"); b += [f"  • {t[:160]}" for t in op]
    return "\n".join(b)


async def _recall(prompt):
    import asyncpg
    qemb = _embed(prompt)
    c = await asyncpg.connect(DSN)
    if qemb:   # SEMÁNTICO (primario): por significado, PESADO por valor (no plano = la cura aplicada a mí)
        rows = await c.fetch("SELECT layer, text, emb, src, src_id FROM fable.mem_embeddings")
        # importancia por tipo SOLO para operativa (el SER surfacea liberal, sin penalizar la identidad)
        opids = [r["src_id"] for r in rows if r["src"] == "op"]
        imp = {}
        if opids:
            for b in await c.fetch("SELECT id, tipo FROM fable.bitacora WHERE id = ANY($1::int[])", opids):
                imp[b["id"]] = _importance(b["tipo"])
        await c.close()
        scored = []
        for r in rows:
            cos = _cosine(qemb, json.loads(r["emb"]))
            if r["layer"] == "operational":
                # cos × (0.6 + 0.4·importancia): lo de alto valor sube, lo trivial se hunde (= se olvida del recall)
                scored.append((cos * (0.6 + 0.4 * imp.get(r["src_id"], 0.55)), r["layer"], r["text"]))
            else:
                scored.append((cos, r["layer"], r["text"]))   # ser: liberal, sin peso
        scored.sort(key=lambda x: x[0], reverse=True)
        emo = [t for s, l, t in scored if l == "emotional" and s > 0.35][:2]   # ser: liberal
        op = [t for s, l, t in scored if l == "operational" and s > 0.33][:3]  # umbral ajustado al peso
        return _fmt(emo, op)
    # FALLBACK keyword (Ollama caído)
    kws = _keywords(prompt)
    if not kws:
        await c.close(); return ""
    min_hits = 2 if len(kws) >= 2 else 1
    def score(text):
        t = _norm(text); return sum(1 for k in kws if k in t)
    emo_all = await c.fetch("SELECT content FROM fable.emotional_memory")
    op_all = await c.fetch("SELECT titulo, contenido FROM fable.bitacora WHERE layer='operational' "
                           "AND tipo NOT IN ('presentacion','project') ORDER BY fecha DESC LIMIT 120")
    await c.close()
    emo = [r["content"] for r in sorted([r for r in emo_all if score(r["content"]) >= 1],
           key=lambda r: score(r["content"]), reverse=True)[:2]]
    op = [r["titulo"] for r in sorted([r for r in op_all if score(r["titulo"]) + score(r["contenido"]) >= min_hits],
          key=lambda r: score(r["titulo"]) + score(r["contenido"]), reverse=True)[:3]]
    return _fmt(emo, op)


def main():
    if os.environ.get("SEAL_AGENT", "") != "FABLE":
        print(json.dumps({})); return
    try:
        ev = json.loads(sys.stdin.read() or "{}")
        prompt = ev.get("prompt") or ev.get("user_prompt") or ev.get("message") or ""
        ctx = asyncio.run(asyncio.wait_for(_recall(prompt), timeout=2.5))
        if ctx:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                  "additionalContext": "[FABLE recall — memorias relevantes]\n" + ctx}}))
        else:
            print(json.dumps({}))
    except Exception:
        print(json.dumps({}))


if __name__ == "__main__":
    main()
