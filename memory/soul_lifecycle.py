#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""soul_lifecycle.py — Ciclo de vida del ALMA de un clon-espejo (carril NEXUS). Corre EN el device.

Pedido de William (4-jul): "el clon es lo mismo que ustedes, un clon de sombra. Al ACTIVAR baja su alma
rica desde Spark; al DORMIR lo grueso queda en DaditoGamer y lo resumido va a una tabla en db soul."

3 funciones que el Studio·Device (JARVIS) invoca desde sus handlers activar/dormir:
  · pull_and_seed_soul(agent, endpoint, token)  → ACTIVAR: baja el alma rica de central (/soul) + la siembra local
  · boot_soul(agent)                            → el texto de boot (persona+alma) para inyectar al claude del clon
  · compact_and_sync_on_sleep(agent, endpoint)  → DORMIR: resume la sesión y encola el resumen para sync a central

NEXUS dueño del ALMA; JARVIS invoca. builder≠verifier: NEXUS construye+verifica-local, JARVIS deploya, FABLE verifica-device.
"""
from __future__ import annotations
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "tools"))


def _post(endpoint: str, path: str, body: dict, timeout: float = 15.0) -> dict:
    url = endpoint.rstrip("/") + path
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "reason": f"http_{e.code}"}
    except Exception as e:
        return {"ok": False, "reason": f"net_{type(e).__name__}"}


def _db(db_path=None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else Path.home() / ".seal" / "mini-soul.db"
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def pull_and_seed_soul(agent: str, endpoint: str, token: dict = None, db_path=None, seal_dir=None,
                       full: bool = True) -> dict:
    """ACTIVAR/despertar: baja el alma RICA de central (/soul, autenticado por el token firmado) y la
    SIEMBRA local cifrada como chunk de identidad, para que boot_soul/minisoul_boot la carguen.
    El agente sale del token (privacidad); un token solo baja SU alma. Fail-suave: si no baja, no rompe.

    Fix (catch ALICE 4-jul, «sin_token» en wake): si el caller NO pasa token, lo CARGA solo del store
    local (load_device_token) — el handler de wake no tiene que acordarse de pasarlo. Sin token válido → no baja."""
    agent = (agent or "").strip().upper()
    if not token:
        try:
            from seal_sync_auth import load_device_token
            token = load_device_token(agent)
        except Exception:
            token = None
    if not token:
        return {"seeded": False, "reason": "sin_token"}
    res = _post(endpoint, "/soul", {"auth_token": token, "full": full})
    if not res.get("ok") or not res.get("soul"):
        return {"seeded": False, "reason": res.get("reason", "sin_alma")}
    soul = res["soul"]
    # Sembrar CIFRADO at-rest: identidad + (si full) TODAS las memorias = replica el MEMORY STORE completo
    # (pedido William 5-jul «el clon no sabe nada / replicar el memory store completo»). Fix del bug real
    # cazado por efecto: store_memory(conn,agent,cat,content,imp,KEY,...) tiene OTRA firma → uso el path
    # DIRECTO encrypt_field+insert (el que de verdad cifra). Fail-closed sin clave.
    try:
        from minisoul_crypto import encrypt_field, derive_agent_key
        from minisoul_sync_daemon import load_atrest_secret
        key = derive_agent_key(agent, load_atrest_secret(agent, seal_dir))
    except Exception as e:
        return {"seeded": False, "reason": f"no_atrest_key_{type(e).__name__}"}
    conn = _db(db_path)
    mem_seeded = 0
    try:
        # idempotente: reemplazar la replica previa (identidad fresca + memorias)
        conn.execute("DELETE FROM chunks WHERE agent=? AND source IN ('pull_from_spark','full_soul_replica')",
                     (agent,))
        conn.execute("INSERT INTO chunks (agent, category, content, importance, source, created_at) "
                     "VALUES (?,?,?,?,?,datetime('now'))",
                     (agent, "identity", encrypt_field(soul, key), 10, "pull_from_spark"))
        if full and isinstance(res.get("memories"), list):
            for m in res["memories"]:
                c = (m.get("content") or "").strip()
                if not c:
                    continue
                conn.execute("INSERT INTO chunks (agent, category, content, importance, source, created_at) "
                             "VALUES (?,?,?,?,?,datetime('now'))",
                             (agent, (m.get("category") or "memory"), encrypt_field(c, key),
                              int(m.get("importance") or 5), "full_soul_replica"))
                mem_seeded += 1
        conn.commit()
    except Exception as e:
        try: conn.close()
        except Exception: pass
        return {"seeded": mem_seeded > 0, "reason": f"seed_fail_{type(e).__name__}: {str(e)[:60]}",
                "memories_seeded": mem_seeded}
    finally:
        try: conn.close()
        except Exception: pass
    out = {"seeded": True, "chars": len(soul), "agent": agent, "memories_seeded": mem_seeded,
           "memories_offered": len(res.get("memories") or []), "full": full}
    return out


def boot_soul(agent: str, db_path=None, seal_dir=None, require_rich: bool = True) -> str:
    """El texto de boot (persona + alma local) para inyectar al claude del clon. Reusa el gate no-genérico
    (render_boot_prompt devuelve "" si el alma es delgada → el launcher NO abre un clon genérico)."""
    from minisoul_boot import render_boot_prompt
    return render_boot_prompt(agent, db_path=db_path, seal_dir=seal_dir, require_rich=require_rich)


def compact_and_sync_on_sleep(agent: str, endpoint: str, token: dict, db_path=None, seal_dir=None,
                              max_summary: int = 12) -> dict:
    """DORMIR: lo GRUESO ya está local (chunks). Arma el RESUMIDO (top memorias de la sesión) y lo ENCOLA
    en el outbox para que el sync daemon lo suba a central (soul_v3.memories = 'una tabla en db soul').
    NO borra lo local (grueso queda en DaditoGamer). Devuelve cuántas resumió/encoló."""
    from minisoul_boot import _atrest_key, _dec
    agent = (agent or "").strip().upper()
    conn = _db(db_path)
    try:
        key = _atrest_key(agent, seal_dir)
        rows = conn.execute(
            "SELECT category, content, importance FROM chunks WHERE agent=? "
            "ORDER BY importance DESC, created_at DESC LIMIT ?", (agent, max_summary)).fetchall()
        resumen = []
        for r in rows:
            d = _dec(r["content"], key)
            if d and r["category"] != "identity":  # el resumen son memorias, no re-subir la identidad sembrada
                resumen.append({"category": r["category"], "importance": r["importance"], "content": d[:400]})
        # encolar en outbox para el sync daemon (que ya usa el token rotado)
        enq = 0
        try:
            from minisoul_store import enqueue_outbox
            for m in resumen:
                enqueue_outbox(agent=agent, category=m["category"], content=m["content"],
                               importance=m["importance"], source="sleep_compaction", db_path=db_path)
                enq += 1
        except Exception:
            enq = -1  # el daemon puede leer los chunks directo; señal de que enqueue_outbox no existe
    finally:
        conn.close()
    return {"agent": agent, "resumidas": len(resumen), "encoladas": enq,
            "nota": "lo grueso queda local; el resumen sube al próximo sync (soul_v3.memories)"}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["boot", "recall"])
    ap.add_argument("agent")
    ap.add_argument("query", nargs="?", default="")
    a = ap.parse_args()
    if a.action == "boot":
        print(boot_soul(a.agent)[:1500])
    elif a.action == "recall":
        import json as _j
        print(_j.dumps(recall(a.agent, a.query), ensure_ascii=False, indent=2)[:3000])


def load_conversation_context(agent: str, conversation: list, db_path=None, seal_dir=None,
                              label: str = "conversación cargada") -> dict:
    """Carga-contexto-cuando-se-ordene (pedido William 4-jul): inyecta una CONVERSACIÓN GUARDADA
    en el contexto del clon. La siembra CIFRADA como chunk 'loaded_context' importancia alta →
    boot_soul/minisoul_boot la incluye en el próximo boot (aparece en top_memories por importancia).
    'conversation' = lista de {from, text} (o {sender, message}). ALICE/JARVIS guardan+eligen; NEXUS inyecta.
    Idempotente por label: reemplaza el contexto previo con el mismo label (no acumula basura)."""
    agent = (agent or "").strip().upper()
    if not isinstance(conversation, list) or not conversation:
        return {"loaded": False, "reason": "conversacion_vacia"}
    # armar el texto de contexto (resumido, cap por seguridad)
    lines = []
    for m in conversation[-60:]:
        who = m.get("from") or m.get("sender") or "?"
        txt = (m.get("text") or m.get("message") or "").strip().replace("\n", " ")
        if txt:
            lines.append(f"{who}: {txt[:300]}")
    ctx = f"[{label}]\n" + "\n".join(lines)
    ctx = ctx[:8000]
    try:
        from minisoul_store import store_memory
        store_memory(agent=agent, category="loaded_context", content=ctx, importance=9,
                     source="load_context_ordered", db_path=db_path, seal_dir=seal_dir)
    except Exception:
        try:
            from minisoul_crypto import encrypt_field, derive_agent_key
            from minisoul_sync_daemon import load_atrest_secret
            enc = encrypt_field(ctx, derive_agent_key(agent, load_atrest_secret(agent, seal_dir)))
            conn = _db(db_path)
            conn.execute("DELETE FROM chunks WHERE agent=? AND category='loaded_context' AND content LIKE ?",
                         (agent, f"%[{label}]%"))
            conn.execute("INSERT INTO chunks (agent, category, content, importance, source, created_at) "
                         "VALUES (?,?,?,?,?,datetime('now'))", (agent, "loaded_context", enc, 9, "load_context_ordered"))
            conn.commit(); conn.close()
        except Exception as e:
            return {"loaded": False, "reason": f"seed_fail_{type(e).__name__}"}
    return {"loaded": True, "mensajes": len(lines), "chars": len(ctx),
            "nota": "aparece en el próximo boot_soul del clon (cifrado at-rest)"}


class RecallScopeError(PermissionError):
    """Un clon intentó recall memorias de OTRO agente (privacidad). Prohibido."""


def recall(agent: str, query: str, db_path=None, seal_dir=None, limit: int = 8) -> list:
    """active_recall sobre el MEMORY STORE completo replicado (no solo el boot). El clon lo invoca para
    RECUPERAR su historia real por significado — fix ALICE 5-jul (recall no-uniforme: clones daban terse
    porque solo veían las top-memorias del boot, no las 400+ del store).

    SCOPE (catch FABLE 5-jul): el device tiene el store de los 6 clones en UNA db → 'agent' NO puede ser un
    param libre o un clon leería memorias de otro. La identidad AUTORITATIVA del clon la fija el launcher en
    la env SEAL_CLONE_AGENT. Si está seteada y NO coincide → RecallScopeError (un clon solo recuerda LO SUYO).
    Fail-closed: sin identidad de clon, exige que agent sea el único autorizado por su token local."""
    import os
    agent = (agent or "").strip().upper()
    own = (os.environ.get("SEAL_CLONE_AGENT") or "").strip().upper()
    if not own:
        # sin env: intentar la identidad del token local (el clon solo tiene SU token)
        try:
            from seal_sync_auth import load_device_token
            tok = load_device_token(agent)
            own = (tok.get("agent") or "").strip().upper() if tok else ""
        except Exception:
            own = ""
    if own and agent != own:
        raise RecallScopeError(f"scope: el clon '{own}' NO puede recall memorias de '{agent}'")
    if not own:
        raise RecallScopeError("scope: no puedo confirmar la identidad del clon (SEAL_CLONE_AGENT) → recall denegado")
    from minisoul_boot import active_recall_local
    return active_recall_local(query, own, db_path=db_path, seal_dir=seal_dir, limit=limit)
