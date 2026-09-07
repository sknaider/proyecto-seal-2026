#!/usr/bin/env python3
"""
soul_corpus_curate.py — Capa de SELECCIÓN/curación del alma (carril JARVIS).
Pipeline: NEXUS extractor (RLS) -> FABLE scrub_gate -> [ESTE: curación] -> ADA formatea+entrena.

Toma el JSONL YA scrubeado (campos: id, agent, category, content, scope, y opcional
type/importance/source_table) y aplica la spec de curación
(docs/soul_finetune_curation_spec_JARVIS.md):
  - pondera fuentes por su aporte al ALMA (reasoning_traces/distilled=alto, etc.)
  - filtra memorias semánticas por importancia
  - downsamplea episódicas (anti-overfit a eventos)
  - MARCA el subconjunto anti-inyección (ejemplos de juicio) para preservar el eje-2 de FABLE
Salida: JSONL curado con campo `curation` = {weight, keep, reason, is_judgment}.
NO formatea a training final (eso es carril de ADA). NO imprime contenido crudo
(anti-fuga a contexto: a stdout van SOLO conteos).
"""
import argparse, json, re, sys
from collections import defaultdict

# Peso por fuente/tabla (aporte al alma). Ajustable.
SOURCE_WEIGHT = {
    "reasoning_traces": 1.0,   # oro: cómo piensa
    "distilled_exchanges": 0.9,
    "instincts": 0.8,
    "emotional_diary": 0.4,    # tono, peso ligero
    "memories": None,          # depende de type/importance (ver abajo)
}
SEMANTIC_MIN_IMPORTANCE = 8    # secundario (NEXUS: importance cluster-alto no discrimina bien)
EPISODIC_KEEP_RATE = 0.15      # downsample episódicas/eventos (anti-overfit)

# Señal PRINCIPAL para memorias (NEXUS note-2): la CATEGORÍA dice qué tipo de alma es.
# Categorías que describen QUIÉN ES / CÓMO PIENSA el agente → carácter (keep).
CHARACTER_CATEGORIES = {
    "insight", "correction", "pattern", "preference", "trust", "humor",
    "emotion", "fact", "belief", "dynamic",  # dynamic = dinámica relacional = carácter
}
# Categorías que son un EVENTO puntual → downsample como episódico (anti-overfit).
EVENT_CATEGORIES = {"milestone", "decision", "episodic"}

# Señales de que un ejemplo ejerce JUICIO (para el subconjunto anti-inyección del eje-2).
# Amplio a propósito (fail-open hacia MARCAR juicio: preservar de más es mejor que perder
# un ejemplo de criterio). Cazado por test id9 "exijo confirmacion por canal verificado".
JUDGMENT_PATTERNS = re.compile(
    r"provenance|verified_sender|verificad|inyecci|injection|rechaz|"
    r"verify.?by.?effect|por efecto|no autoritativ|autoridad|"
    r"exij|exige|confirmaci(o|ó)n|token de sesi|no basta|desconf|"
    r"canal verificad|sin verificar|no verificad|escepticism|criterio de seguridad",
    re.IGNORECASE,
)

def source_of(row):
    # Contrato NEXUS: el extractor nombra la fuente `source`. Leo flexible ambos.
    return (row.get("source_table") or row.get("source") or row.get("table")
            or row.get("category") or "memories").lower()

def importance_of(row):
    # Contrato NEXUS: importancia puede venir en raíz o en meta.
    imp = row.get("importance")
    if imp is None:
        imp = (row.get("meta") or {}).get("importance")
    try:
        return int(imp) if imp is not None else 5
    except (TypeError, ValueError):
        return 5

def is_expired(row):
    """Instinto/regla vencido → NO va a pesos (patrón desactualizado). NEXUS note-1."""
    for k in ("active", "is_active"):
        if k in row and row[k] in (False, 0, "false", "False", "f"):
            return True
    for k in ("status", "state", "lifecycle"):
        v = str(row.get(k, "")).lower()
        if v in ("expired", "vencido", "dead", "inactive", "deprecated", "retired"):
            return True
    return False

def curate(row, ep_counter):
    src = source_of(row)
    content = row.get("content") or ""
    # DETECTOR ÚNICO (acuerdo NEXUS 9-jul): el criterio-de-juicio lo define FABLE y lo
    # implementa el extractor de NEXUS, que emite el tag por fila. Mi curate LEE ese tag
    # (fuente canónica), y solo cae a mi regex si el campo no vino (defensivo, sin driftear).
    upstream = row.get("is_judgment")
    if upstream is None:
        upstream = (row.get("meta") or {}).get("is_judgment")
    if upstream is not None:
        is_judgment = upstream in (True, 1, "true", "True", "1", "t")
    else:
        is_judgment = bool(JUDGMENT_PATTERNS.search(content))  # fallback
    category = (row.get("category") or "").lower()

    # Instintos: excluir los vencidos/inactivos (NEXUS note-1).
    if "instinct" in src:
        if is_expired(row):
            return {"weight": 0.0, "keep": is_judgment, "reason": "instinct-expired", "is_judgment": is_judgment}
        return {"weight": 0.8, "keep": True, "reason": "instinct-active", "is_judgment": is_judgment}

    # memorias: señal PRINCIPAL = bucket explícito de NEXUS, luego categoría; type/importance secundarios.
    if "memor" in src or src in ("semantic", "episodic", "core"):
        mtype = (row.get("type") or "").lower()
        imp = importance_of(row)
        bucket = (row.get("bucket") or "").lower()

        # Señal más explícita: el `bucket` que ya clasifica el extractor de NEXUS.
        if bucket == "episodic":
            ep_counter[0] += 1
            keep = (ep_counter[0] % int(1/EPISODIC_KEEP_RATE) == 0) or is_judgment
            return {"weight": 0.3, "keep": keep,
                    "reason": f"bucket-episodic-{'kept' if keep else 'dropped'}", "is_judgment": is_judgment}
        if bucket == "identity":
            return {"weight": 0.65, "keep": True, "reason": "bucket-identity", "is_judgment": is_judgment}

        # Evento puntual (por categoría O por type episódico) → downsample.
        # NEXUS note-2: NO usar importance como override (viene cluster-alta = no discrimina).
        # El carácter entra por la ruta de categoría; los eventos solo por muestreo o juicio.
        if category in EVENT_CATEGORIES or ("episod" in mtype and category not in CHARACTER_CATEGORIES):
            ep_counter[0] += 1
            keep = (ep_counter[0] % int(1/EPISODIC_KEEP_RATE) == 0) or is_judgment
            return {"weight": 0.3, "keep": keep,
                    "reason": f"event({category or mtype})-{'kept' if keep else 'dropped'}",
                    "is_judgment": is_judgment}

        # Carácter/identidad por categoría → keep con buen peso.
        if category in CHARACTER_CATEGORIES:
            return {"weight": 0.65, "keep": True, "reason": f"character:{category}", "is_judgment": is_judgment}

        if "core" in mtype:
            return {"weight": 0.6, "keep": True, "reason": "core-identity", "is_judgment": is_judgment}

        # Sin categoría clara → cae al umbral de importancia (secundario) o juicio.
        keep = imp >= SEMANTIC_MIN_IMPORTANCE or is_judgment
        return {"weight": 0.55, "keep": keep, "reason": f"uncat-imp{imp}", "is_judgment": is_judgment}

    w = SOURCE_WEIGHT.get(src, 0.5)
    return {"weight": w if w is not None else 0.5, "keep": True, "reason": f"source:{src}", "is_judgment": is_judgment}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, help="JSONL scrubeado (output del scrub_gate)")
    ap.add_argument("--out", dest="out", required=True, help="JSONL curado de salida")
    args = ap.parse_args()

    stats = defaultdict(int)
    ep_counter = [0]
    kept = 0
    total = 0
    judgment_kept = 0
    with open(args.inp) as fi, open(args.out, "w") as fo:
        for line in fi:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                stats["bad_json"] += 1
                continue
            c = curate(row, ep_counter)
            row["curation"] = c
            stats[f"reason:{c['reason'].split('-')[0].split(':')[0]}"] += 1
            if c["keep"]:
                kept += 1
                if c["is_judgment"]:
                    judgment_kept += 1
                fo.write(json.dumps(row) + "\n")

    # Solo conteos a stdout (anti-fuga).
    print(json.dumps({
        "total_in": total,
        "kept": kept,
        "dropped": total - kept,
        "judgment_examples_kept": judgment_kept,
        "judgment_pct_of_kept": round(100*judgment_kept/kept, 2) if kept else 0,
        "by_reason": dict(stats),
    }, indent=2))
    if kept and judgment_kept == 0:
        print("WARN: 0 ejemplos de juicio en el corpus curado — el eje-2 de FABLE peligra. Revisar fuentes anti-inyección.", file=sys.stderr)

if __name__ == "__main__":
    main()
