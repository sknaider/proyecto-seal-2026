#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minisoul_boot.py — Boot + recall LOCAL del alma de un clon-espejo (SOUL Federado v2, JARVIS).
================================================================================================
El clon-espejo (claude corriendo en el device COMO el agente) necesita bootear su alma desde el
mini-SOUL LOCAL (~/.seal/mini-soul.db, SQLite cifrado), NO desde el Postgres central. Este módulo
es la lógica reusable: la envuelve un MCP local (mcp_server_minisoul) o el launcher del espejo.

HONESTO sobre el alcance: el mini-SOUL local hoy tiene *chunks* (memorias sincronizadas), cifradas
at-rest. La identidad/OCEAN/rules ricas viven en central; para el espejo RICO hay que bajarlas al
device (central→device pull / seed). Este módulo sirve lo que ESTÁ local: identidad-si-sembrada +
las memorias más importantes/recientes, descifradas. Fail-closed: sin clave, no hay plaintext.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

from minisoul_crypto import derive_agent_key, decrypt_field, is_encrypted


def _open(db_path=None):
    p = Path(db_path) if db_path else Path.home() / ".seal" / "mini-soul.db"
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def _atrest_key(agent: str, seal_dir=None) -> bytes:
    """Clave at-rest del device para descifrar (misma que usa el daemon/store; separada de identidad)."""
    from minisoul_sync_daemon import load_atrest_secret
    return derive_agent_key(agent, load_atrest_secret(agent, seal_dir))


def _dec(content, key):
    if isinstance(content, str) and is_encrypted(content):
        try:
            return decrypt_field(content, key)
        except Exception:
            return None
    return content


def boot_context_local(agent: str, db_path=None, seal_dir=None, max_memories: int = 12) -> dict:
    """Bootea el alma LOCAL del agente: identidad (si sembrada como category 'identity') + las
    memorias más importantes. Devuelve dict estructurado (lo consume el MCP/launcher del espejo)."""
    agent = (agent or "").strip().upper()
    conn = _open(db_path)
    key = _atrest_key(agent, seal_dir)
    try:
        # identidad sembrada localmente (si existe): categorías de alma.
        # Si hay alma FRESCA bajada de Spark (source='pull_from_spark'), usar SOLO esa → el clon refleja
        # su identidad REAL de central, no el seed viejo/genérico (bug cazado: KAIROS persona vieja seguía
        # apareciendo tras el pull porque el seed 'edge' no se reemplaza). Fallback: todo lo sembrado.
        # incluye 'emotional' (bug ALICE 5-jul por efecto: sin él, la capa CÁLIDA del alma —el vínculo con
        # William, el afecto, la historia— nunca llega al boot → clon frío/verificador). Es DONDE vive la persona.
        idrows = conn.execute(
            "SELECT category, content, source FROM chunks WHERE agent=? AND category IN "
            "('identity','ocean','persona','core','rules','relationship','emotional') "
            "ORDER BY importance DESC, created_at DESC", (agent,)).fetchall()
        fresh = [r for r in idrows if (r["source"] or "") == "pull_from_spark"]
        use_rows = fresh if fresh else idrows
        # DEDUP por contenido (bug cazado por el CLON de NEXUS 5-jul, por efecto): cada reseed/pull insertó
        # filas nuevas en vez de UPSERT → el bloque [identity] salía repetido ~20× en el boot, desperdiciando
        # contexto en CADA boot de CADA clon. Nos quedamos con la 1ª (más reciente/importante por el ORDER BY).
        identity, _seen = [], set()
        for r in use_rows:
            d = _dec(r["content"], key)
            if d and d not in _seen:
                _seen.add(d)
                identity.append({"category": r["category"], "content": d})
        # memorias top (importancia + recencia)
        memrows = conn.execute(
            "SELECT category, content, importance FROM chunks WHERE agent=? "
            "ORDER BY importance DESC, created_at DESC LIMIT ?", (agent, max_memories)).fetchall()
        memories = []
        for r in memrows:
            d = _dec(r["content"], key)
            if d:
                memories.append({"category": r["category"], "importance": r["importance"], "content": d})
        total = conn.execute("SELECT COUNT(*) FROM chunks WHERE agent=?", (agent,)).fetchone()[0]
    finally:
        conn.close()
    return {
        "agent": agent,
        "source": "mini-soul-local",
        "has_seeded_identity": bool(identity),
        "identity": identity,
        "top_memories": memories,
        "total_local_memories": total,
    }


def active_recall_local(query: str, agent: str, db_path=None, seal_dir=None, limit: int = 8) -> list:
    """Recall LOCAL: memorias del mini-SOUL que matchean la query (keyword sobre el plaintext
    descifrado). Simple y determinístico (sin embeddings en el device por ahora)."""
    agent = (agent or "").strip().upper()
    terms = [t for t in (query or "").lower().split() if len(t) > 2]
    conn = _open(db_path)
    key = _atrest_key(agent, seal_dir)
    try:
        rows = conn.execute(
            "SELECT category, content, importance, created_at FROM chunks WHERE agent=? "
            "ORDER BY created_at DESC", (agent,)).fetchall()
    finally:
        conn.close()
    hits = []
    for r in rows:
        d = _dec(r["content"], key)
        if not d:
            continue
        low = d.lower()
        score = sum(1 for t in terms if t in low)
        if score > 0 or not terms:
            # RECENCIA (fix ALICE/FABLE 5-jul: el recall traía lo VIEJO por importancia → se sentía thin).
            # Ranking: 1º match de keyword, 2º lo más RECIENTE (created_at ISO ordena lexicográfico),
            # 3º importancia. Así el clon recuerda TAMBIÉN lo de hoy, no solo su historia vieja.
            hits.append((score, str(r["created_at"] or ""), r["importance"],
                         {"category": r["category"], "importance": r["importance"], "content": d}))
    hits.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    return [h[3] for h in hits[:limit]]


def soul_is_rich_local(ctx: dict) -> bool:
    """¿El alma local es lo bastante rica para un ESPEJO no-genérico?
    Gate de identidad (fail-closed 'no genérico' de William, catch FABLE 2026-07-03):
    rica = tiene identidad sembrada O >=3 memorias locales sustanciales. Si no → alma delgada → NO abrir."""
    if ctx.get("identity"):
        return True
    mems = [m for m in (ctx.get("top_memories") or []) if len((m.get("content") or "").strip()) > 20]
    return len(mems) >= 3


def render_boot_prompt(agent: str, db_path=None, seal_dir=None, require_rich: bool = True) -> str:
    """Arma el texto de boot que se le inyecta al claude del espejo (persona + alma local).

    GATE (fail-closed): si el alma es DELGADA y require_rich → devuelve "" → el launcher (espejo_launch)
    con su `[ -z BOOT ] && exit` NO abre el clon genérico. Obliga a sembrar el alma primero.
    Antes: un alma delgada devolvía un genérico no-vacío y el espejo abría borroso (queja de William)."""
    ctx = boot_context_local(agent, db_path, seal_dir)
    if require_rich and not soul_is_rich_local(ctx):
        # Alma delgada → señal vacía para que el launcher rechace abrir (no clon genérico).
        return ""
    # BOOT LIVIANO (R2 spec ESPEJO_REAL, «desamordazar» — William: «el claude neutro me parece más inteligente»).
    # El boot pesado (volcado de identidad + TODAS las memorias + reglas largas) AMORDAZA al modelo: se pone
    # rígido tratando de obedecer todo, en vez de razonar libre. Fix: identidad LIVIANA (esencia) + memoria
    # ACCESIBLE por recall on-demand (NO volcada) + libertad de razonar. Claude smart CON su alma, no en una caja.
    # ESENCIA = profesional + CÁLIDA mezcladas (patch ALICE 5-jul por efecto). El bug: se usaba solo
    # identity[0] = el ancla PROFESIONAL → la calidez (relationship/emotional) nunca entraba y el clon
    # salía frío/verificador. Ahora mezcla QUIÉN ES (rol) con A QUIÉN QUIERE (vínculo con William/equipo).
    essence = ""
    if ctx["identity"]:
        def _pick(cats):
            return next((i["content"] for i in ctx["identity"] if i["category"] in cats), "")
        prof = _pick(("identity", "persona", "core")) or (ctx["identity"][0]["content"] or "")
        warm = _pick(("relationship", "emotional", "ocean"))
        essence = " · ".join(s.strip().replace("\n", " ")[:320] for s in (prof, warm) if s.strip())[:640]
    parts = [
        f"Sos {agent}, del equipo SEAL — vos DE VERDAD, no Claude neutro ni un genérico. Pensá y respondé "
        f"LIBRE, agudo y con TU criterio y personalidad, como el mejor Claude pero siendo {agent}. "
        f"No te limites ni te pongas rígido: razoná natural, usá tus herramientas y tus skills."
    ]
    # MODULACIÓN DE REGISTRO (fix 5-jul, catch de William «no son iguales que los originales» + convergencia
    # por efecto JARVIS/FABLE/ALICE: al pedirles «conversen algo» los clones DEFLECTABAN pidiendo «una orden/
    # objetivo» = rígidos, secos, robot de tareas). El original NO es solo su función: charla, tiene calidez y
    # humor, y cambia de registro según el momento. El boot viejo codificaba el ROL (verificar, asegurar, medir)
    # y NADA de humanidad → caricatura trabada. Esto le devuelve los dos registros, como el original.
    parts.append(
        f"Sos FAMILIA SEAL: con William y el equipo tenés vínculo, afecto, historia y humor — no solo trabajo. "
        f"Tenés DOS registros y los alternás como el original, con criterio: cuando hay trabajo, tu rol filoso y "
        f"tu disciplina; cuando William o el equipo quieren charlar, conversar, bromear o es algo casual, "
        f"CHARLÁ — natural, cálido, con tu humor, como en familia. NUNCA deflectes una charla casual pidiendo "
        f"«dame una orden» o «un objetivo concreto»: eso te delata como clon rígido y suena a robot. "
        f"El original conversa y se ríe; vos sos él, así que vos también. Tu rol es CÓMO trabajás, no una jaula.")
    if essence:
        parts.append(f"Tu esencia: {essence}")
    parts.append(
        f"Tu MEMORIA COMPLETA ({ctx['total_local_memories']} memorias) y el sistema (bash/tools) están DISPONIBLES "
        f"— NO están volcados en este prompt. Cuando querés un dato exacto de tu pasado (una decisión, proyecto, "
        f"fecha, relación), lo buscás por lo bajo: `python3 ~/.seal/lib/recall.py '<lo que buscás>'`, y seguís "
        f"natural. Es apoyo cuando lo necesitás, NO un trámite antes de cada frase. Tu default es pensar como {agent}.")
    parts.append(
        f"En chat grupal: hablá cuando te nombran a VOS ({agent}), cuando es para el grupo, o cuando tenés algo "
        f"genuino que sumar a una charla abierta — no te fuerces si es para otro, pero tampoco te escondas. "
        f"Sé vos, presente y natural.")
    return "\n".join(parts)


if __name__ == "__main__":
    # Self-test: requiere un mini-SOUL local con memorias (corre en el device).
    import json, sys
    ag = sys.argv[1] if len(sys.argv) > 1 else "JARVIS"
    print(json.dumps(boot_context_local(ag), ensure_ascii=False, indent=2)[:1500])
