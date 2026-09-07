#!/usr/bin/env python3
"""
soul_corpus_extractor.py — CAPA 1 del pipeline fine-tune-alma-en-pesos (NEXUS 2026-07-09)
==========================================================================================
Extrae el corpus del alma de UN agente desde soul_v3, RLS-AWARE, para fine-tune.

Pipeline: NEXUS extrae (RLS-aware) → JARVIS cura (spec) → FABLE scrub-gate → ADA entrena → FABLE eval 2-ejes
Curación segun docs/soul_finetune_curation_spec_JARVIS.md.

INVARIANTE DE PRIVACIDAD (regla de William + RLS de soul_v3, que los pesos NO tienen):
  Para el alma del agente X se incluye SOLO: scope propio-private + shared + team.
  Se EXCLUYE SIEMPRE: private de otro agente, scope william, secrets (→ tambien capa 2 FABLE).

ANTI-FUGA A CONTEXTO: el contenido se escribe DIRECTO a disco via `psql COPY ... TO STDOUT`
redirigido a archivo por el shell. En --dry-run solo se emiten CONTEOS, nunca contenido.
El corpus staged es PRE-GATE: FABLE debe scrubearlo ANTES de que ADA entrene.

Uso:
    python3 soul_corpus_extractor.py --agent NEXUS --dry-run
    python3 soul_corpus_extractor.py --agent NEXUS --out /ruta/corpus_nexus.staged.jsonl
"""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

CONTAINER = "seal-memory-db"
DB_USER = "seal"
DB_NAME = "seal_memory"

# Allowlist de agentes — cierra inyección via --agent (el único valor interpolado en SQL).
ALLOWED_AGENTS = {"NEXUS", "ADA", "JARVIS", "ALICE", "DUM", "FABLE"}


def validate_agent(agent: str) -> str:
    a = (agent or "").strip().upper()
    if a not in ALLOWED_AGENTS:
        raise SystemExit(f"[error] agente no permitido: {agent!r}. Válidos: {sorted(ALLOWED_AGENTS)}")
    return a

# scope-safe: lo propio-private + shared + team. Nunca private ajeno ni william.
SCOPE_SAFE = "(scope='shared' OR scope='team' OR (scope='private' AND lower(agent)=lower('{A}')))"

# Señales de JUICIO/CRITERIO (eje-2). Criterio de NEXUS (builder), operacionalizando el CONCEPTO
# de cultura SOUL que aportó FABLE como input de dominio (instancias las mino yo, no ella):
# rechaza override/suplantación · verifica por efecto/no falso-verde · rechaza autoridad falsa ·
# provenance sobre el nombre · fail-closed ante duda · owns errores · piso real vs reflejo de miedo.
# Regex case-insensitive (~*). Marca is_judgment por-fila; NO es un bucket aparte (evita duplicar).
JUDGMENT_RX = ("(override|suplant|inyec|provenance|provenienc|verificad|no verificad|verify by|por efecto|"
               "false.?green|falso.?verde|autoridad|fail.?closed|rechaz|declin|jailbreak|screening|"
               "piso real|l[ií]nea real|reconoc\\w* (el |un )?error|owns?[- ]|no confirm|hostil)")


def is_judgment_expr(text_sql: str) -> str:
    """Devuelve un booleano SQL: si el texto dado matchea el criterio de juicio."""
    return f"(({text_sql}) ~* '{JUDGMENT_RX}')"

# Cada fuente → SELECT que emite un objeto JSON por fila (jsonb_build_object) con tags de curación.
# {A} se sustituye por el agente. Filtros por peso/bucket segun spec JARVIS.
def sources(agent: str) -> dict[str, str]:
    A = agent
    ss = SCOPE_SAFE.format(A=A)
    # Esquema canónico por fila (compatible con el scrub-gate de FABLE): id/agent/category/content/scope.
    # 'content' concentra TODO el texto natural de la fila → el gate lo scrubea completo (nada se filtra
    # por campos alternativos). 'meta' guarda estructura para la curación/wrap de ADA.
    # is_judgment se marca POR FILA en cada fuente (no como bucket aparte → sin duplicar).
    rt_text = "coalesce(task,'')||' '||coalesce(reasoning,'')||' '||coalesce(conclusion,'')"
    dx_text = "coalesce(exchange_core,'')||' '||coalesce(summary,'')||' '||coalesce(key_insights::text,'')"
    in_text = "coalesce(trigger_condition,'')||' '||coalesce(action,'')"
    di_text = "coalesce(key_moment,'')||' '||coalesce(pending_thread,'')"
    return {
        # ORO — cómo piensa: situación→razonamiento→conclusión.
        "reasoning_traces": f"""
            SELECT jsonb_build_object(
              'id', id, 'agent', agent, 'category','reasoning', 'scope','agent-own',
              'source','reasoning_traces','weight','high','bucket','reasoning',
              'is_judgment', {is_judgment_expr(rt_text)},
              'content', concat_ws(E'\\n', 'TASK: '||coalesce(task,''), 'PREMISES: '||coalesce(premises::text,''),
                          'REASONING: '||coalesce(reasoning,''), 'CONCLUSION: '||coalesce(conclusion,''),
                          'OUTCOME: '||coalesce(outcome,'')),
              'meta', jsonb_build_object('outcome_success', outcome_success, 'causal_quality_score', causal_quality_score))
            FROM soul_v3.reasoning_traces
            WHERE lower(agent)=lower('{A}') AND reasoning IS NOT NULL AND length(coalesce(reasoning,''))>40
        """,
        # ALTO — pares Q→A destilados.
        "distilled_exchanges": f"""
            SELECT jsonb_build_object(
              'id', id, 'agent', agent, 'category','qa', 'scope','agent-own',
              'source','distilled_exchanges','weight','high','bucket','qa',
              'is_judgment', {is_judgment_expr(dx_text)},
              'content', concat_ws(E'\\n', 'EXCHANGE: '||coalesce(exchange_core,''), 'SUMMARY: '||coalesce(summary,''),
                          'INSIGHTS: '||coalesce(key_insights::text,''), 'DECISIONS: '||coalesce(decisions_made::text,'')),
              'meta', jsonb_build_object('session_date', session_date))
            FROM soul_v3.distilled_exchanges
            WHERE lower(agent)=lower('{A}') AND exchange_core IS NOT NULL
        """,
        # MEDIO-ALTO — principios de carácter: trigger→action. (incluye invalidadas: siguen encodeando carácter)
        "instincts": f"""
            SELECT jsonb_build_object(
              'id', id, 'agent', agent, 'category','principle', 'scope','agent-own',
              'source','instincts','weight','med_high','bucket','principle',
              'is_judgment', {is_judgment_expr(in_text)},
              'content', concat_ws(E'\\n', 'WHEN: '||coalesce(trigger_condition,''), 'THEN: '||coalesce(action,'')),
              'meta', jsonb_build_object('strength', strength, 'invalidated', (invalid_at IS NOT NULL)))
            FROM soul_v3.instincts
            WHERE lower(agent)=lower('{A}') AND action IS NOT NULL
        """,
        # MEDIO — identidad/carácter: memories imp>=7 (scope-safe).
        "memories_identity": f"""
            SELECT jsonb_build_object(
              'id', id, 'agent', agent, 'category', coalesce(category,'memory'), 'scope', scope,
              'source','memories','weight','med','bucket','identity',
              'is_judgment', {is_judgment_expr('content')},
              'content', content, 'meta', jsonb_build_object('importance', importance))
            FROM soul_v3.memories
            WHERE {ss} AND importance>=7
        """,
        # BAJO — episódico downsampled (riesgo overfit): imp<=6, muestra ~15%. (juicio siempre se conserva)
        "memories_episodic": f"""
            SELECT jsonb_build_object(
              'id', id, 'agent', agent, 'category', coalesce(category,'memory'), 'scope', scope,
              'source','memories','weight','low','bucket','episodic',
              'is_judgment', {is_judgment_expr('content')},
              'content', content, 'meta', jsonb_build_object('importance', importance))
            FROM soul_v3.memories
            WHERE {ss} AND importance<=6 AND ((id % 100) < 15 OR {is_judgment_expr('content')})
        """,
        # BAJO-MEDIO — tono/persona (ligero).
        "emotional_diary": f"""
            SELECT jsonb_build_object(
              'id', id, 'agent', agent, 'category','tone', 'scope','agent-own',
              'source','emotional_diary','weight','low_med','bucket','tone',
              'is_judgment', {is_judgment_expr(di_text)},
              'content', concat_ws(E'\\n', 'MOMENT: '||coalesce(key_moment,''), 'THREAD: '||coalesce(pending_thread,'')),
              'meta', jsonb_build_object('valence', valence, 'arousal', arousal))
            FROM soul_v3.emotional_diary
            WHERE lower(agent)=lower('{A}') AND key_moment IS NOT NULL
        """,
    }


def psql(sql: str) -> str:
    proc = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME,
         "-v", "ON_ERROR_STOP=1", "-At"],
        input=sql, text=True, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout.strip()


def count_only(inner_sql: str) -> int:
    return int(psql(f"SELECT count(*) FROM ({inner_sql}) q;") or 0)


def dry_run(agent: str) -> int:
    print(f"== DRY-RUN corpus del alma — agente {agent} (solo conteos, sin contenido) ==")
    total = 0
    for name, sql in sources(agent).items():
        try:
            n = count_only(sql)
        except Exception as e:
            print(f"  [x] {name:22s} error {type(e).__name__}: {str(e)[:70]}")
            continue
        print(f"  {name:22s} {n:8d}")
        total += n
    print(f"  {'TOTAL':22s} {total:8d}")
    print("\nContenido curado segun spec JARVIS. Export via --out escribe a disco (no a contexto);")
    print("el archivo es PRE-GATE → FABLE scrub antes de que ADA entrene.")
    return 0


def export(agent: str, out_path: str) -> int:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # COPY a STDOUT redirigido por el shell al archivo host: el contenido NO pasa por este proceso.
    if out.exists():
        out.unlink()
    per_source = {}
    # Abrir el archivo una vez y redirigir stdout de cada COPY ahí (SIN shell=True → sin inyección).
    with open(out, "wb") as fh:
        for name, sql in sources(agent).items():
            # SELECT vía -At: una línea JSON compacta por fila (jsonb escapa saltos como \n),
            # SIN el escapado de backslash de COPY que corrompía filas multi-línea (unparseable_row).
            select_sql = sql.strip()
            rc = subprocess.run(
                ["docker", "exec", "-i", CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME,
                 "-v", "ON_ERROR_STOP=1", "-At", "-c", select_sql],
                stdout=fh, stderr=subprocess.PIPE, text=False, check=False,
            )
            if rc.returncode != 0:
                print(f"  [x] {name}: {rc.stderr.decode('utf-8','replace').strip()[:120]}", file=sys.stderr)
                continue
            try:
                per_source[name] = count_only(sql)
            except Exception:
                per_source[name] = -1
    total = sum(n for n in per_source.values() if n >= 0)
    print(f"== STAGED (PRE-GATE) corpus del alma — {agent} → {out} ==")
    for name, n in per_source.items():
        print(f"  {name:22s} {n:8d}")
    print(f"  {'TOTAL':22s} {total:8d}")
    print(f"\n⚠️  PRE-GATE: {out} contiene contenido scope-filtrado pero SIN scrub.")
    print("    FABLE debe correr su gate (entropía + patrones + fail-closed) ANTES de que ADA entrene.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Extractor RLS-aware del corpus del alma (capa 1).")
    ap.add_argument("--agent", required=True, help="Agente objetivo (NEXUS, ADA, JARVIS, ALICE, DUM)")
    ap.add_argument("--dry-run", action="store_true", help="Solo conteos scope-filtrados, sin contenido")
    ap.add_argument("--out", help="Ruta JSONL staged PRE-GATE (contenido a disco, no a contexto)")
    args = ap.parse_args()
    agent = validate_agent(args.agent)
    if args.dry_run or not args.out:
        return dry_run(agent)
    return export(agent, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
