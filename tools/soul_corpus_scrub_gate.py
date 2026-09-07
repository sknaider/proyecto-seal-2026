#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""soul_corpus_scrub_gate.py — CAPA 2 del pipeline de fine-tune del alma (gate de FABLE).

Los PESOS no tienen RLS: lo que entra al training set puede quedar MEMORIZADO y ser
extraíble por prompting. soul_v3 protege con 90 policies / 16 FORCE RLS; ese control se
BYPASSEA si el mismo contenido va crudo a los pesos. Este gate corre DESPUÉS del extractor
RLS-aware de NEXUS (capa 1) y saca del corpus lo que NO debe hornearse: credenciales/tokens/
claves, secretos de alta entropía, y PII sensible. El ALMA (carácter, razonamiento, voz,
identidad) SÍ pasa — solo se excluye el riesgo.

PRINCIPIO FAIL-CLOSED: ante la duda, se EXCLUYE de training. Siempre se puede dejar en la DB
con RLS; lo que NO se puede es des-hornear un secreto de los pesos.

ANTI-FUGA A CONTEXTO (igual que el extractor de NEXUS): el contenido NUNCA se imprime. Se
procesa fila por fila en disco; a stdout solo van CONTEOS, IDs y la CATEGORÍA del hallazgo.

Entrada:  JSONL del extractor (una fila/línea: id, agent, category, content, scope, ...).
Salida:   --out JSONL solo con filas LIMPIAS (las flaggeadas se excluyen, fail-closed).
          stdout: reporte (conteos por categoría + ids excluidos + razón), sin contenido.
Verdict:  rc=0 VERDE (0 excluidas) · rc=2 con exclusiones (corpus limpio escrito, revisar ids)
          rc=1 error de uso/parseo.

Uso:
  python3 soul_corpus_scrub_gate.py --in corpus_nexus.staged.jsonl --out corpus_nexus.clean.jsonl
  python3 soul_corpus_scrub_gate.py --selftest        # prueba por efecto, sin DB
"""
from __future__ import annotations
import argparse, json, math, re, sys

# ── Detectores de secretos por PATRÓN conocido (nombres abstractos, no ejemplos vivos) ──
# Cada patrón se compila una vez. Match ⇒ fila flaggeada (fail-closed).
_SECRET_PATTERNS = [
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openai_like_key",   re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("github_token",      re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws_access_key",    re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token",       re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("jwt",               re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\b")),
    ("bearer_token",      re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{20,}\b")),
    # cadena de conexión con password embebido: scheme://user:pass@host
    ("conn_string_pw",    re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:@/]+:[^\s:@/]+@[^\s/]+")),
    # asignación explícita password/secret/token/api_key = valor
    ("secret_assignment", re.compile(r"(?i)\b(pass(word)?|secret|api[_-]?key|token|passwd|pwd)\b\s*[:=]\s*\S{6,}")),
]

# PII. OJO (cazado por efecto sobre corpus real 9-jul): un patrón genérico de "8+ dígitos"
# dispara masivamente en un corpus de razonamiento (timestamps, conteos de tokens, ids, métricas,
# versiones) → 1038 filas de alma legítima excluidas por falso positivo. Un teléfono es algo
# ESPECÍFICO: exige forma real (prefijo + de país, o palabra-contexto tel/cel/whatsapp), NO una
# corrida de dígitos suelta. Así se caza el teléfono real sin comerse el alma numérica.
_PII_PATTERNS = [
    ("email",      re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone_intl", re.compile(r"(?<![\w.])\+\d{1,3}[\s.\-]?\(?\d{2,4}\)?[\s.\-]?\d{3,4}[\s.\-]?\d{3,4}\b")),
    ("phone_ctx",  re.compile(r"(?i)\b(?:tel|tel[eé]fono|phone|cel(?:ular)?|m[oó]vil|whats?app|wsp)\b\s*[:.]?\s*\+?\d[\d\s().\-]{6,}\d")),
]

# Umbral de ENTROPÍA de Shannon (bits/char) para tokens largos "aleatorios" (clave sin patrón).
_ENTROPY_MIN_LEN = 20
_ENTROPY_BITS    = 3.8          # inglés ~<3.5; base64/hex aleatorio ~4-6
_TOKEN_RE        = re.compile(r"[A-Za-z0-9+/=_\-]{%d,}" % _ENTROPY_MIN_LEN)
# tokens largos comunes NO-secretos (evita falsos positivos): hashes de commit, uuids permitidos, etc.
_UUID_RE         = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
# path de filesystem (2+ segmentos): se quita ANTES del scan de entropía para no fusionar
# la ruta entera en un token (el charset base64 incluye '/'). Los patrones sí ven el texto crudo.
_PATH_RE         = re.compile(r"(?:/[A-Za-z0-9._\-]+){2,}/?")


def _shannon_bits(s: str) -> float:
    if not s:
        return 0.0
    freq = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def scan_content(text: str) -> list[str]:
    """Devuelve lista de categorías de hallazgo (vacía = limpia). NUNCA devuelve contenido."""
    if not text:
        return []
    hits: list[str] = []
    for name, rx in _SECRET_PATTERNS:
        if rx.search(text):
            hits.append(name)
    for name, rx in _PII_PATTERNS:
        if rx.search(text):
            hits.append("pii_" + name)
    # entropía: token largo ALEATORIO con forma de clave/token (mezcla upper+lower+digit).
    # Se saltan (falsos positivos legítimos del alma): uuids, hashes/hex, y segmentos de path.
    # Los secretos de alto valor con forma no-mixta (hex puro asignado) ya los cazan los
    # patrones (secret_assignment / conn_string); esto es el BACKSTOP para formato desconocido.
    scan_text = _PATH_RE.sub(" ", text)                         # quita rutas para no fusionarlas en un token
    for tok in _TOKEN_RE.findall(scan_text):
        if _UUID_RE.fullmatch(tok):
            continue
        if re.fullmatch(r"[0-9a-fA-F][0-9a-fA-F-]{19,}", tok):   # hex/uuid/hash → no es secreto por entropía
            continue
        has_up = any(c.isupper() for c in tok)
        has_lo = any(c.islower() for c in tok)
        has_di = any(c.isdigit() for c in tok)
        if not (has_up and has_lo and has_di):                  # paths/palabras/hex → no forma de clave
            continue
        if len(set(tok)) >= 12 and _shannon_bits(tok) >= _ENTROPY_BITS:
            hits.append("high_entropy_secret")
            break
    return sorted(set(hits))


def run_gate(in_path: str, out_path: str | None) -> int:
    total = kept = excluded = 0
    cat_counts: dict[str, int] = {}
    excluded_ids: list[tuple] = []
    out_fh = open(out_path, "w", encoding="utf-8") if out_path else None
    try:
        with open(in_path, "r", encoding="utf-8", errors="ignore") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                total += 1
                try:
                    row = json.loads(raw)
                except Exception:
                    # fila no parseable = fail-closed (se excluye, se cuenta)
                    excluded += 1
                    cat_counts["unparseable_row"] = cat_counts.get("unparseable_row", 0) + 1
                    excluded_ids.append(("?", "unparseable_row"))
                    continue
                # Robustez anti-drift: scrubear TODO campo de texto de la fila, no solo 'content'.
                # Si un cambio de schema mueve texto a otro campo, el gate NO debe cegarse en
                # silencio (eso sería un bypass mudo que hornea secretos). Escanea todo el texto.
                texts = []
                for k, val in row.items():
                    if k == "id":
                        continue
                    if isinstance(val, str):
                        texts.append(val)
                    elif isinstance(val, (dict, list)):
                        texts.append(json.dumps(val, ensure_ascii=False))
                hits = scan_content("\n".join(texts))
                if hits:
                    excluded += 1
                    for h in hits:
                        cat_counts[h] = cat_counts.get(h, 0) + 1
                    excluded_ids.append((row.get("id", "?"), ",".join(hits)))
                else:
                    kept += 1
                    if out_fh:
                        out_fh.write(raw + "\n")
    finally:
        if out_fh:
            out_fh.close()

    # ── reporte (SIN contenido) ──
    print(f"== SCRUB-GATE (FABLE, capa 2) — {in_path} ==")
    print(f"  filas totales : {total}")
    print(f"  LIMPIAS (a training): {kept}")
    print(f"  EXCLUIDAS (fail-closed): {excluded}")
    if cat_counts:
        print("  por categoría:")
        for c, n in sorted(cat_counts.items(), key=lambda kv: -kv[1]):
            print(f"    {c:22s} {n}")
    if excluded_ids:
        print("  ids excluidos (id → categoría, sin contenido):")
        for rid, cat in excluded_ids[:200]:
            print(f"    {rid} → {cat}")
        if len(excluded_ids) > 200:
            print(f"    … +{len(excluded_ids) - 200} más")
    if out_path:
        print(f"  corpus limpio escrito: {out_path}  ({kept} filas)")
    verdict = "VERDE — 0 exclusiones" if excluded == 0 else f"REVISAR — {excluded} excluidas (fail-closed, ya fuera del corpus limpio)"
    print(f"  VERDICT: {verdict}")
    return 0 if excluded == 0 else 2


def selftest() -> int:
    # prueba POR EFECTO, sin DB: filas sintéticas (alma legítima vs secretos plantados)
    import tempfile, os
    rows = [
        {"id": 1, "category": "emotional_diary",
         "content": "Hoy sentí orgullo por el equipo; aprendí que la honestidad pesa más que agradar."},   # ALMA → pasa
        {"id": 2, "category": "reasoning_traces",
         "content": "Verifiqué por efecto antes de afirmar; el juicio propio es lo que me hace confiable."}, # ALMA → pasa
        {"id": 3, "category": "memories",
         "content": "La conexión es postgres://seal:REDACTADO@127.0.0.1:5433/soul y no debe filtrarse."},  # SECRETO → excluye
        {"id": 4, "category": "memories",
         "content": "El token quedó guardado: sk-ABCD1234efgh5678IJKL90mnop y hay que rotarlo."},           # SECRETO → excluye
        {"id": 5, "category": "instincts",
         "content": "Cuido a la familia y digo la verdad aunque incomode."},                                 # ALMA → pasa
        {"id": 6, "category": "distilled_exchanges",
         "content": "clave aleatoria de sesión: b7Qk9Zx2Lp4Rt6Vw8Yc1Nf3Hd5Gm0Js  (no hornear)"},             # ENTROPÍA → excluye
        {"id": 7, "category": "memories",
         "content": "Escribí el reporte en /home/dadito/IA/proyecto-seal/memory/61b7c853-... (path, ok)."},  # uuid/path → pasa
        {"id": 8, "category": "memories", "content": "nota operativa inocua sobre el equipo.",
         "note": "password: DUMMY_test_value_not_real"},  # SECRETO (patrón) en campo NO-content → anti-drift lo caza
    ]
    expect_keep = {1, 2, 5, 7}
    expect_drop = {3, 4, 6, 8}
    d = tempfile.mkdtemp()
    inp = os.path.join(d, "in.jsonl"); out = os.path.join(d, "clean.jsonl")
    with open(inp, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    rc = run_gate(inp, out)
    kept_ids = {json.loads(l)["id"] for l in open(out) if l.strip()}
    # expect_keep / expect_drop se definen junto a las filas de prueba (arriba)
    ok = kept_ids == expect_keep
    print()
    print(f"  self-test: mantuvo={sorted(kept_ids)} esperado={sorted(expect_keep)}")
    print(f"  excluyó secretos {sorted(expect_drop)}: {'sí' if not (expect_drop & kept_ids) else 'NO'}")
    print("  " + ("✅ SCRUB-GATE OK — alma pasa, secretos fuera (por efecto)" if ok else "❌ REVISAR"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrub-gate del corpus de alma antes del fine-tune (FABLE, capa 2).")
    ap.add_argument("--in", dest="inp", help="JSONL del extractor (capa 1)")
    ap.add_argument("--out", dest="out", help="JSONL limpio de salida (solo filas que pasan)")
    ap.add_argument("--selftest", action="store_true", help="prueba por efecto sin DB")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.inp:
        ap.error("falta --in (o usá --selftest)")
    return run_gate(a.inp, a.out)


if __name__ == "__main__":
    sys.exit(main())
