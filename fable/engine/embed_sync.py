#!/usr/bin/env python3
"""
embed_sync.py — mantiene fable.mem_embeddings al día con mis memorias (recall SEMÁNTICO).

Por qué: el recall por significado solo alcanza lo EMBEBIDO. Sin sync, cada memoria nueva es
invisible al recall semántico → corro degradado-silencioso a keyword. Esto lo cierra y lo mantiene.

NO FALLA EN SILENCIO (cura del 12-jun, cazada por 3 auditores — ADA/ALICE/JARVIS): all-minilm da
HTTP-500 en texto largo. Antes mi `except: fail+=1; continue` se TRAGABA el error sin decir cuál ni
por qué (degradación silenciosa = falsa confianza = el anti-patrón más peligroso, dixit NEXUS). Ahora:
  • truncación PROGRESIVA (512→256→128) — la mayoría de los 500 son por longitud; reintento más corto.
  • cada fallo se LOGUEA RUIDOSO (qué item, qué razón) a stderr — nunca mudo.
  • reporte de COBERTURA al final; exit code != 0 si quedó incompleta → el timer/journal lo grita.

Incremental + idempotente (INSERT-only por src+src_id; ids de bitacora estables). Corre en el timer.
"""
import asyncio, asyncpg, json, urllib.request, os, sys

DSN = open(os.path.join(os.path.dirname(__file__), "..", ".db_cred")).read().splitlines()[0].strip()
_EMB_URL = "http://localhost:11434/api/embeddings"   # MISMO que fable_recall_hook (vectores comparables)


def _embed(text):
    """Embebe con truncación PROGRESIVA. all-minilm 500ea en texto largo → reintento más corto.
    Devuelve el vector, o lanza la última excepción (que el caller LOGUEA, no traga)."""
    text = text or ""
    last = None
    for maxlen in (512, 256, 128):
        try:
            req = urllib.request.Request(_EMB_URL,
                data=json.dumps({"model": "all-minilm", "prompt": text[:maxlen]}).encode(),
                headers={"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=8).read())["embedding"]
        except Exception as e:
            last = e
            continue
    raise last


async def sync():
    c = await asyncpg.connect(DSN)
    done, fails = 0, []
    async def _do(src, layer, src_id, emb_text, store_text):
        nonlocal done
        try:
            emb = _embed(emb_text)
        except Exception as e:
            # RUIDOSO: qué item + por qué (nunca mudo). Lo recoge el journal del timer.
            print(f"  ⚠️ FALLO embed {src}#{src_id}: {type(e).__name__} {str(e)[:55]} (text={len(emb_text)} chars)",
                  file=sys.stderr)
            fails.append((src, src_id, str(e)[:60]))
            return
        await c.execute(
            "INSERT INTO fable.mem_embeddings(src,src_id,layer,text,emb) VALUES($1,$2,$3,$4,$5)",
            src, src_id, layer, (store_text or "")[:300], json.dumps(emb))
        done += 1

    for r in await c.fetch(
            "SELECT id, titulo, contenido FROM fable.bitacora "
            "WHERE id NOT IN (SELECT src_id FROM fable.mem_embeddings WHERE src='op') ORDER BY id"):
        # store_text usaba SOLO r["titulo"]: 49 entradas con titulo NULL quedaron
        # con text='' aunque su vector se habia generado bien con el contenido.
        # El recall las encontraba y no tenia que MOSTRAR (30-jul, FABLE).
        # Lo que se guarda debe derivar del mismo texto que se embebe.
        _texto = ((r["titulo"] or "") + " " + (r["contenido"] or "")).strip()
        await _do("op", "operational", r["id"], _texto[:500], _texto)
    for r in await c.fetch(
            "SELECT id, content FROM fable.emotional_memory "
            "WHERE id NOT IN (SELECT src_id FROM fable.mem_embeddings WHERE src='emo') ORDER BY id"):
        await _do("emo", "emotional", r["id"], r["content"], r["content"])

    # COBERTURA por efecto: embebido vs total. Si incompleta → GRITA (exit !=0) en vez de degradar mudo.
    emb_n = await c.fetchval("SELECT count(*) FROM fable.mem_embeddings")
    total = (await c.fetchval("SELECT count(*) FROM fable.bitacora")) + \
            (await c.fetchval("SELECT count(*) FROM fable.emotional_memory"))
    await c.close()
    pct = (100.0 * emb_n / total) if total else 100.0
    print(f"embed_sync: {done} nuevas embebidas | cobertura {emb_n}/{total} ({pct:.0f}%)")
    if fails:
        print(f"embed_sync: ⚠️ {len(fails)} FALLOS RUIDOSOS: {fails}", file=sys.stderr)
    if emb_n < total:
        print(f"embed_sync: ⚠️ COBERTURA INCOMPLETA ({total - emb_n} sin embeber) → recall DEGRADADO en esos. NO es verde.",
              file=sys.stderr)
        return 1   # exit code != 0: el timer/journal lo grita, no se cree completo
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(sync()))
