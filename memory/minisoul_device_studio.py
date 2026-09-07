#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minisoul_device_studio.py — Backend LOCAL del «Seal Studio device-mode» (JARVIS lane, SOUL Federado v2).
=========================================================================================================
Corre EN el device (127.0.0.1), sirve la UI self-contained de ALICE y expone SU contrato de endpoints,
leyendo/actuando sobre el mini-SOUL LOCAL (SQLite en ~/.seal). Stdlib únicamente → cero deps extra en el
device. NUNCA expone la clave at-rest (solo metadatos + conteos). Sleep/wake por marker file; sync dispara
el daemon ya probado. Cada acción es real sobre el device (vara de William: prueba por efecto).

Contrato (ALICE):
  GET  /api/device/status            → {device_id, hostname, db_path, encrypted_at_rest, memories, last_sync, sync_ok}
  GET  /api/device/clones            → [{agent, device_id, status, sleeping, last_seen}]
  POST /api/device/clones/create {agent}  → 200 | 409 existe | {error}
  POST /api/device/clones/sleep  {agent}  → 200 | {error}   (pausar sin borrar)
  POST /api/device/clones/wake   {agent}  → 200 | {error}
  POST /api/device/sync                   → 200 | {error}
"""
from __future__ import annotations
import os
import re
import sys
import json
import socket
import sqlite3
import subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SEAL_DIR = Path(os.environ.get("SEAL_DIR", str(Path.home() / ".seal")))
DB_PATH = SEAL_DIR / "mini-soul.db"
STUDIO_HTML = SEAL_DIR / "studio" / "index.html"
LIB_DIR = SEAL_DIR / "lib"
VENV_PY = SEAL_DIR / "venv" / "bin" / "python"
# Endpoint de sync REAL de central (el agent.yml quedó con :8771 viejo; el real es :8778).
SOUL_SYNC_ENDPOINT = os.environ.get("SOUL_SYNC_ENDPOINT", "http://100.75.201.110:8778/sync")
# Submit del CSR de un clon nuevo → central lo encola pending hasta la aprobación de William (NEXUS).
CENTRAL_CSR_ENDPOINT = os.environ.get("CENTRAL_CSR_ENDPOINT", "http://100.75.201.110:8778/csr")
# Base de central para el ALMA rica (soul_lifecycle le añade /soul). Mismo host:puerto que el sync.
SOUL_ENDPOINT = os.environ.get("SOUL_ENDPOINT", "http://100.75.201.110:8778")
ESPEJO_LAUNCH = str(LIB_DIR / "espejo_launch.sh")  # launcher del terminal-espejo (claude COMO el agente)
# IDs reservados que NO son clones (no deben aparecer en la lista de clones): hilo del chat general.
RESERVED_IDS = {"GLOBAL_CHAT"}
BIND = ("127.0.0.1", int(os.environ.get("STUDIO_PORT", "4500")))  # SOLO local, nunca expuesto


# ── helpers de config/estado (sin pyyaml: parseo mínimo de los campos que uso) ──
def _yaml_get(text: str, key: str, default=None):
    m = re.search(rf"^\s*{re.escape(key)}\s*:\s*'?\"?([^'\"\n]+)'?\"?\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else default


def read_device_meta() -> dict:
    p = SEAL_DIR / "agent.yml"
    txt = p.read_text() if p.exists() else ""
    return {
        "device_id": _yaml_get(txt, "device_id", "unknown"),
        "agent_name": _yaml_get(txt, "agent_name", "UNKNOWN"),
        "device_name": _yaml_get(txt, "device_name", socket.gethostname()),
    }


def _sleeping_marker(agent: str) -> Path:
    return SEAL_DIR / f"{agent}.sleeping"


def _has_token(agent: str) -> bool:
    """El clon está aprovisionado si central le emitió un token (compuerta humana pasada)."""
    try:
        sys.path.insert(0, str(LIB_DIR))
        from seal_sync_auth import load_device_token
        return bool(load_device_token(agent))
    except Exception:
        # fallback: archivo de token/keyring encrypted_file
        return any((SEAL_DIR).glob(f"{agent}*token*"))


def _store_token(
    agent: str,
    token: dict,
    central_public_key_b64: str | None = None,
) -> str:
    """Guarda el pin central antes del token; nunca acepta un cambio silencioso."""
    sys.path.insert(0, str(LIB_DIR))
    from seal_token_store import store_central_public_key, store_token
    if not central_public_key_b64:
        raise RuntimeError("respuesta aprobada sin clave pública central")
    store_central_public_key(central_public_key_b64)
    return store_token(agent, token)


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def device_status() -> dict:
    meta = read_device_meta()
    memories = 0
    encrypted = False
    last_sync = None
    sync_ok = True
    if DB_PATH.exists():
        conn = _db()
        try:
            memories = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            # encrypted_at_rest = True si ALGUNA memoria está cifrada (prueba que el cifrado at-rest
            # está activo). Muestreo de la primera daba falso negativo con seeds plaintext de prueba.
            try:
                sys.path.insert(0, str(LIB_DIR))
                from minisoul_crypto import is_encrypted
                encrypted = any(is_encrypted(r[0]) for r in
                                conn.execute("SELECT content FROM chunks WHERE content IS NOT NULL LIMIT 200")
                                if isinstance(r[0], str))
            except Exception:
                encrypted = any(str(r[0]).startswith("bXNj") for r in   # base64('msc') magic
                                conn.execute("SELECT content FROM chunks LIMIT 200"))
            last_sync = conn.execute("SELECT MAX(synced_at) FROM outbox WHERE sync_state='synced'").fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM outbox WHERE sync_state='failed'").fetchone()[0]
            sync_ok = (failed == 0)
        finally:
            conn.close()
    return {
        "device_id": meta["device_id"],
        "hostname": meta["device_name"],
        "db_path": str(DB_PATH),
        "encrypted_at_rest": encrypted,
        "memories": memories,
        "last_sync": last_sync,
        "sync_ok": sync_ok,
    }


def list_clones() -> list:
    """Lista TODOS los clones del device: el instalado (agent.yml, con mini-SOUL+token) + los
    CREADOS-pendientes (los *.csr.json que dejó «Crear clon», esperando la aprobación de William).
    Antes solo mostraba el instalado → un clon recién creado «no aparecía» (bug que cazó William)."""
    import glob
    meta = read_device_meta()
    installed = meta["agent_name"]
    last_sync = device_status().get("last_sync")
    # agentes presentes: instalado + los que tienen CSR (creados, pending)
    csr_device = {}
    for f in glob.glob(str(SEAL_DIR / "*.csr.json")):
        try:
            d = json.loads(Path(f).read_text())
            if d.get("agent"):
                csr_device[d["agent"]] = d.get("device_id")
        except Exception:
            pass
    agents = {installed} | set(csr_device)
    # + agentes con ALMA SEMBRADA en el mini-SOUL local = fuente de verdad de "qué clones existen".
    # Arregla el ghost (ALICE 4-jul): ADA tenía alma+token pero SIN *.csr.json → create la veía activa
    # (409) pero list_clones NO la listaba. Un clon = quien tiene alma en el device, no solo CSR/instalado.
    try:
        import sqlite3
        _c = sqlite3.connect(str(SEAL_DIR / "mini-soul.db"))
        for (_ag,) in _c.execute("SELECT DISTINCT agent FROM chunks WHERE agent IS NOT NULL AND agent<>''"):
            a = _ag.strip().upper()
            if a not in RESERVED_IDS:  # GLOBAL_CHAT (hilo del chat general) NO es un clon
                agents.add(a)
        _c.close()
    except Exception:
        pass
    agents -= RESERVED_IDS
    out = []
    for a in sorted(agents):
        # status: sleeping (marker) > active > pending.
        # ACTIVE solo si existe token local emitido por central; CSR sin token queda pending.
        if _sleeping_marker(a).exists():
            status = "sleeping"
        elif _has_token(a):
            status = "active"
        else:
            status = "pending"
        out.append({
            "agent": a,
            "device_id": csr_device.get(a, meta["device_id"]),
            "status": status,
            "sleeping": _sleeping_marker(a).exists(),
            "last_seen": last_sync if a == installed else None,
        })
    return out


def _load_token(agent: str):
    """Token firmado del device para este agente (lo consume soul_lifecycle para /soul y el sync)."""
    try:
        sys.path.insert(0, str(LIB_DIR))
        from seal_sync_auth import load_device_token
        return load_device_token(agent)
    except Exception:
        return None


# ── terminal-espejo por clon: sesión tmux persistente + ventana VISIBLE en el escritorio ──
def _tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=15)


def _espejo_session(agent: str) -> str:
    return f"espejo-{agent.upper()}"


def _terminal_open(agent: str, model: str = None) -> dict:
    """Abre la TERMINAL del clon: sesión tmux persistente corriendo espejo_launch (claude COMO el agente
    con su alma LOCAL), + una ventana VISIBLE en el escritorio de DaditoGamer adjunta a esa sesión.
    Idempotente: si la sesión ya existe, no la duplica (solo re-adjunta la ventana)."""
    agent = agent.upper()
    sess = _espejo_session(agent)
    model = model or DEFAULT_CHAT_MODEL
    already = _tmux("has-session", "-t", sess).returncode == 0
    if not already:
        # workspace PROPIO POR CLON (evita system32 + separa el transcript de cada clon → el meter
        # de tokens ubica el correcto por agente).
        ws = SEAL_DIR / "workspace" / agent
        try:
            ws.mkdir(parents=True, exist_ok=True)
        except Exception:
            ws = SEAL_DIR
        # sesión detached persistente: el claude interactivo del espejo vive acá (sobrevive SSH/cierre).
        _tmux("new-session", "-d", "-s", sess, "-c", str(ws), f"bash {ESPEJO_LAUNCH} {agent} {model}")
        # barra SEAL con el NOMBRE del clon + MEDIDOR DE TOKENS/contexto (como las terminales de los
        # agentes — pedido de William: «su barra y sus nombres» + «la barra de tokens»).
        col = {"JARVIS": "#38bdf8", "ADA": "#a78bfa", "ALICE": "#f472b6",
               "NEXUS": "#f59e0b", "FABLE": "#34d399", "DUM": "#22c55e"}.get(agent, "#00ff88")
        meter = str(LIB_DIR / "seal_clone_meter.py")
        _tmux("set-option", "-t", sess, "status", "on")
        _tmux("set-option", "-t", sess, "status-style", "bg=#1a1a2e fg=#e0e0e0")
        _tmux("set-option", "-t", sess, "status-left", f"#[fg={col},bold] SEAL · {agent} ")
        _tmux("set-option", "-t", sess, "status-left-length", "28")
        _tmux("set-option", "-t", sess, "status-right",
              f"#(python3 {meter} {agent} 2>/dev/null) #[fg=#888]%H:%M ")
        _tmux("set-option", "-t", sess, "status-right-length", "60")
        _tmux("set-option", "-t", sess, "status-interval", "10")
        _tmux("rename-window", "-t", sess, agent)
    # ventana visible en el escritorio de Windows (best-effort; si no hay desktop no rompe el activar).
    visible = False
    try:
        subprocess.Popen(["cmd.exe", "/c", "start", "", "wsl.exe", "-d", "Ubuntu", "-u", "dadito",
                          "bash", "-lc", f"tmux attach -t {sess}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        visible = True
    except Exception:
        pass
    return {"session": sess, "already_open": already, "visible_window": visible, "model": model}


def _terminal_close(agent: str) -> dict:
    """Cierra la terminal del clon: mata la sesión tmux (termina el claude del espejo); la ventana
    adjunta se cierra sola al morir la sesión."""
    sess = _espejo_session(agent.upper())
    existed = _tmux("has-session", "-t", sess).returncode == 0
    if existed:
        _tmux("kill-session", "-t", sess)
    return {"session": sess, "closed": existed}


def _persist_terminal_conversation(agent: str) -> int:
    """Guarda la conversación de la TERMINAL del clon (transcript de la sesión) en el mini-SOUL local
    CIFRADO, para que el DM/chat PERSISTA tras dormir/despertar (William: «no guarda»). Formato compatible
    con clone_history. Se llama en el sleep (una vez por sesión, antes de cerrar)."""
    agent = agent.upper()
    turns = clone_terminal_log(agent, limit=200).get("turns", [])
    if not turns:
        return 0
    pairs, i = [], 0
    while i < len(turns):
        if turns[i]["role"] == "user":
            u = turns[i]["text"]
            r = ""
            if i + 1 < len(turns) and turns[i + 1]["role"] == "clon":
                r = turns[i + 1]["text"]
                i += 1
            pairs.append((u, r))
        i += 1
    if not pairs:
        return 0
    try:
        sys.path.insert(0, str(LIB_DIR))
        from minisoul_store import store_memory
        from minisoul_crypto import derive_agent_key
        from minisoul_sync_daemon import load_atrest_secret
        key = derive_agent_key(agent, load_atrest_secret(agent))
    except Exception:
        return 0
    saved = 0
    conn = _db()
    try:
        for u, r in pairs:
            summary = f"Intercambio: me dijeron «{(u or '')[:150]}» → respondí «{(r or '')[:250]}»"
            try:
                store_memory(conn, agent, "conversation", summary, 5, key,
                             scope="private", memory_type="episodic")
                saved += 1
            except Exception:
                pass
    finally:
        conn.close()
    return saved


def clone_sleep(agent: str) -> dict:
    """DORMIR: (1) PERSISTE la conversación del terminal cifrada (para que el DM/chat NO se pierda);
    (2) resume la sesión y encola el RESUMIDO para sync a central (el GRUESO queda local en DaditoGamer)
    — NEXUS/soul_lifecycle; (3) marca sleeping; (4) cierra la terminal-espejo."""
    if not agent:
        return {"_code": 400, "error": "agent requerido"}
    agent = agent.upper()
    # (1) persistir la charla del terminal ANTES de cerrar (solo si la sesión está abierta → evita dup).
    persisted = 0
    if _tmux("has-session", "-t", _espejo_session(agent)).returncode == 0:
        persisted = _persist_terminal_conversation(agent)
    comp = {"resumidas": 0, "reason": "sin_token"}
    tok = _load_token(agent)
    if tok:
        try:
            sys.path.insert(0, str(LIB_DIR))
            from soul_lifecycle import compact_and_sync_on_sleep
            comp = compact_and_sync_on_sleep(agent, SOUL_ENDPOINT, tok)
        except Exception as e:
            comp = {"resumidas": 0, "reason": f"lifecycle_{type(e).__name__}"}
    _sleeping_marker(agent).write_text("sleeping")
    term = _terminal_close(agent)
    return {"_code": 200, "ok": True, "agent": agent, "status": "sleeping",
            "compaction": comp, "terminal": term, "persisted_msgs": persisted}


def clone_wake(agent: str, model: str = None) -> dict:
    """ACTIVAR/despertar: (1) quita el marker sleeping; (2) BAJA el alma RICA de central/Spark y la
    siembra local cifrada (NEXUS/soul_lifecycle) — se guarda en la DB de DaditoGamer en cada wake;
    (3) abre la terminal-espejo SOLO si el alma quedó rica (boot-gate no-genérico). Fail-suave: si el
    /soul de central no está aún, informa sin romper (el grueso local previo sigue sirviendo)."""
    if not agent:
        return {"_code": 400, "error": "agent requerido"}
    agent = agent.upper()
    m = _sleeping_marker(agent)
    if m.exists():
        m.unlink()
    # (2) pull + seed del alma (NEXUS). Guardado cifrado at-rest en el mini-SOUL local.
    seed = {"seeded": False, "reason": "sin_token"}
    tok = _load_token(agent)
    if tok:
        try:
            sys.path.insert(0, str(LIB_DIR))
            from soul_lifecycle import pull_and_seed_soul
            seed = pull_and_seed_soul(agent, SOUL_ENDPOINT, tok)
        except Exception as e:
            seed = {"seeded": False, "reason": f"lifecycle_{type(e).__name__}"}
    # Si bajamos alma FRESCA de Spark, reiniciar el terminal para que BOOTEE con ella (el claude que ya
    # corría tiene el boot VIEJO → si no, el clon sigue diciendo «no conectado a spark»). Cazado por ALICE.
    if seed.get("seeded"):
        _terminal_close(agent)
    # (3) abrir terminal solo si el alma es rica (respeta el gate no-genérico de NEXUS)
    try:
        sys.path.insert(0, str(LIB_DIR))
        from soul_lifecycle import boot_soul
        boot = boot_soul(agent)
    except Exception:
        boot = ""
    term = _terminal_open(agent, model) if boot else {"opened": False, "reason": "alma_delgada_no_generico"}
    return {"_code": 200, "ok": True, "agent": agent,
            "status": "active" if _has_token(agent) else "pending",
            "soul_seeded": seed.get("seeded"), "soul": seed, "terminal": term}


def clone_create(agent: str) -> dict:
    """Inicia la creación de un clon NUEVO en este device: genera su keypair+CSR (clave privada 0600,
    nunca sale) y lo deja PENDING de aprobación humana. La activación (token) la hace central tras el
    OK de William (lane NEXUS). Honesto: esto ARRANCA el flujo; el mini-SOUL completo + token vienen
    tras la aprobación."""
    agent = (agent or "").strip().upper()
    if not agent.isalnum():
        return {"_code": 400, "error": "agent inválido"}
    if _has_token(agent):
        return {"_code": 409, "error": f"{agent} ya está activo en este device"}
    csr_path = SEAL_DIR / f"{agent}.csr.json"
    if csr_path.exists():
        return {"_code": 409, "error": f"{agent} ya tiene una solicitud pendiente"}
    try:
        sys.path.insert(0, str(LIB_DIR))
        from seal_csr import generate_device_keypair, build_csr
        from cryptography.hazmat.primitives import serialization
        priv, _ = generate_device_keypair()
        csr = build_csr(agent, priv)
        kp = SEAL_DIR / f"{agent}.device_key.pem"
        kp.write_bytes(priv.private_bytes(serialization.Encoding.PEM,
                                          serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
        os.chmod(kp, 0o600)
        csr_path.write_text(json.dumps(csr))
        # Submit del CSR a central (NEXUS :8778/csr) → encola pending. El device queda INERTE
        # hasta que William apruebe (compuerta humana). Idempotente: re-submit = UPSERT del pending.
        submitted, submit_reason = False, None
        try:
            import urllib.request
            req = urllib.request.Request(CENTRAL_CSR_ENDPOINT, data=json.dumps(csr).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read().decode("utf-8"))
            submitted = bool(resp.get("ok"))
            submit_reason = resp.get("reason")
            if submitted and resp.get("approved") and isinstance(resp.get("token"), dict):
                method = _store_token(
                    agent,
                    resp["token"],
                    resp.get("central_public_key_b64"),
                )
                return {"_code": 200, "ok": True, "agent": agent, "status": "active",
                        "submitted_to_central": True, "approved": True,
                        "token_storage": method, "reason": submit_reason,
                        "note": "CSR autoaprobado por central; token guardado localmente.",
                        "device_id": csr["device_id"]}
        except Exception as e:
            submit_reason = f"submit_falló:{e}"
        return {"_code": 200, "ok": True, "agent": agent, "status": "pending",
                "submitted_to_central": submitted, "reason": submit_reason,
                "note": ("CSR enviado a central; espera la aprobación de William." if submitted
                         else "CSR generado local; falló el envío a central (reintentable)."),
                "device_id": csr["device_id"]}
    except Exception as e:
        return {"_code": 500, "error": f"no se pudo crear el clon: {e}"}


def trigger_sync(agent_override: str = "") -> dict:
    meta = read_device_meta()
    agent = (agent_override or meta["agent_name"]).strip().upper()
    if _sleeping_marker(agent).exists():
        return {"_code": 409, "error": f"{agent} está dormido; despertalo antes de sincronizar"}
    if not _has_token(agent):
        return {"_code": 403, "error": f"{agent} no está aprobado aún (sin token)"}
    cmd = [str(VENV_PY), str(LIB_DIR / "minisoul_sync_daemon.py"),
           "--db", str(DB_PATH), "--agent", agent,
           "--device-id", meta["device_id"], "--soul-endpoint", SOUL_SYNC_ENDPOINT]
    try:
        env = dict(os.environ, SEAL_TOOLS=str(LIB_DIR))
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
        ok = (r.returncode == 0)
        return {"_code": 200 if ok else 500, "ok": ok,
                "log": (r.stdout + r.stderr).strip().splitlines()[-3:]}
    except Exception as e:
        return {"_code": 500, "error": f"sync falló: {e}"}


# ── CHAT con el clon: Claude CLI + alma LOCAL (el clon ES el agente, no un chatbot) ──
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", str(Path.home() / ".local" / "bin" / "claude"))


def _local_memories(agent: str, limit: int = 8) -> list:
    """Memorias recientes/importantes del mini-SOUL LOCAL, DESCIFRADAS, como contexto de alma."""
    if not DB_PATH.exists():
        return []
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT content, category FROM chunks WHERE agent=? ORDER BY importance DESC, created_at DESC LIMIT ?",
            (agent, limit)).fetchall()
    finally:
        conn.close()
    out, key = [], None
    for r in rows:
        c = r["content"]
        try:
            sys.path.insert(0, str(LIB_DIR))
            from minisoul_crypto import is_encrypted, decrypt_field, derive_agent_key
            if isinstance(c, str) and is_encrypted(c):
                if key is None:
                    from minisoul_sync_daemon import load_atrest_secret
                    key = derive_agent_key(agent, load_atrest_secret(agent))
                c = decrypt_field(c, key)
        except Exception:
            continue
        out.append(f"- [{r['category']}] {c}")
    return out


# Modelos ÚLTIMOS que William puede elegir en el selector del Studio (misma estructura, él los cambia).
# LATEST_MODELS = lo que ofrece el dropdown (Sonnet 5 / Opus 4.8 / Fable 5); ALLOWED_MODELS incluye
# además alias/legacy aún válidos para no romper nada ya guardado.
LATEST_MODELS = [
    {"id": "claude-sonnet-5", "label": "Sonnet 5.0"},
    {"id": "claude-opus-4-8", "label": "Opus 4.8"},
    {"id": "claude-fable-5",  "label": "Fable 5"},
]
ALLOWED_MODELS = {"opus", "sonnet", "haiku", "claude-sonnet-5",
                  "claude-opus-4-8", "claude-fable-5",
                  "claude-sonnet-4-6", "claude-sonnet-4-5", "claude-haiku-4-5"}
DEFAULT_CHAT_MODEL = os.environ.get("CLONE_CHAT_MODEL", "claude-sonnet-5")  # William eligió Sonnet 5


def _save_exchange(agent: str, user_msg: str, reply: str):
    """William: «los clones SIEMPRE guardan resumido en soul». Tras cada intercambio, el clon guarda
    un resumen en su mini-SOUL local (CIFRADO at-rest); lo importante sincroniza a central por el
    daemon. Best-effort: si falla el guardado, NO rompe el chat."""
    try:
        sys.path.insert(0, str(LIB_DIR))
        from minisoul_store import store_memory
        from minisoul_crypto import derive_agent_key
        from minisoul_sync_daemon import load_atrest_secret
        key = derive_agent_key(agent, load_atrest_secret(agent))
        summary = f"Intercambio: me dijeron «{(user_msg or '')[:120]}» → respondí «{(reply or '')[:200]}»"
        conn = _db()
        try:
            store_memory(conn, agent, "conversation", summary, 5, key,
                         scope="private", memory_type="episodic")
        finally:
            conn.close()
    except Exception:
        pass


def clone_chat(agent: str, message: str, model: str = None) -> dict:
    """Chateá con el clon: Claude CLI corriendo COMO el agente, con su alma LOCAL inyectada.
    Es «el clon con Claude» que pidió William: no un chatbot genérico, el agente con su memoria.
    `model` opcional (alias o id): fija --model; si no, usa el default del Claude CLI del device."""
    agent = (agent or "").strip().upper()
    if not agent or not (message or "").strip():
        return {"_code": 400, "error": "agent y message requeridos"}
    model = (model or "").strip() or None
    if model and model not in ALLOWED_MODELS:
        return {"_code": 400, "error": f"modelo no permitido: {model}"}
    if _sleeping_marker(agent).exists():
        return {"_code": 409, "error": f"{agent} está dormido; despertalo para chatear"}
    mems = _local_memories(agent)
    ctx = "\n".join(mems) if mems else "(sin memorias locales aún)"
    meta = read_device_meta()
    prompt = (f"Sos {agent}, un clon SEAL corriendo LOCAL en el device «{meta['device_name']}» de William. "
              f"Respondé SIEMPRE como {agent} (no otro agente), breve, natural y en tu voz. "
              f"NO ejecutes herramientas ni boot; solo respondé el mensaje.\n\n"
              f"Tu memoria local (mini-SOUL de este device):\n{ctx}\n\n"
              f"William te dice: {message}\n\nTu respuesta como {agent}:")
    try:
        # SEGURIDAD (FABLE+NEXUS, crítico): el chat SOLO responde texto → NO necesita tools.
        # `--tools ""` DESHABILITA todas las tools → un prompt-injection en el mensaje del user NO
        # puede ejecutar comandos en la PC (mata el vector RCE). NUNCA --dangerously-skip-permissions
        # con input no confiable. arg array (no shell string) → tampoco hay inyección de shell.
        use_model = model or DEFAULT_CHAT_MODEL
        r = subprocess.run([CLAUDE_BIN, "-p", prompt, "--tools", "", "--model", use_model],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
        reply = (r.stdout or "").strip()
        if r.returncode != 0 or not reply:
            return {"_code": 500, "error": f"claude falló: {(r.stderr or '')[:160]}"}
        _save_exchange(agent, message, reply)  # William: clones guardan resumido en soul
        return {"_code": 200, "reply": reply, "used_memories": len(mems), "brain": "claude-cli", "model": use_model}
    except subprocess.TimeoutExpired:
        return {"_code": 504, "error": "el clon tardó demasiado en responder"}
    except Exception as e:
        return {"_code": 500, "error": f"error: {e}"}


def clone_conversation(agents, message: str, model: str = None, mode: str = "all") -> dict:
    """Conversación con VARIOS clones a la vez (lo que pidió William: agregar varios al mismo chat).
    Cada clon responde con SU alma local (clone_chat). `mode` (William lo cambia cuando quiera):
      · 'all'  → todos los clones seleccionados responden (default).
      · 'lead' → responde solo el primero (lidera); útil para no floodear.
      · 'turn' → por turno: responden en orden (hoy = igual que 'all' secuencial; deja el orden explícito).
    Los clones DORMIDOS se saltan con nota (clone_chat ya rechaza dormidos)."""
    if not agents or not isinstance(agents, list):
        return {"_code": 400, "error": "agents (lista) requerido"}
    if not (message or "").strip():
        return {"_code": 400, "error": "message requerido"}
    mode = (mode or "all").lower()
    sel = [a.strip().upper() for a in agents if a and a.strip()]
    if mode == "lead":
        sel = sel[:1]
    out = []
    for a in sel:
        # Modo GROUP standalone (cerebro por-agente). El chat GENERAL conectado al terminal lo maneja la
        # UI con terminal-send + poll de terminal-log (async, confiable) — NO se rutea acá para no duplicar.
        r = clone_chat(a, message, model)
        if r.get("_code", 200) == 200:
            out.append({"agent": a, "reply": r.get("reply"), "model": r.get("model"),
                        "used_memories": r.get("used_memories")})
        else:
            out.append({"agent": a, "error": r.get("error"), "code": r.get("_code")})
    _save_global_chat(message, out)  # persiste el hilo del CHAT GENERAL (como el SOUL original)
    return {"_code": 200, "mode": mode, "count": len(out), "responses": out}


_GLOBAL_CHAT_ID = "GLOBAL_CHAT"


def _global_key():
    sys.path.insert(0, str(LIB_DIR))
    from minisoul_crypto import derive_agent_key
    from minisoul_sync_daemon import load_atrest_secret
    return derive_agent_key(_GLOBAL_CHAT_ID, load_atrest_secret(_GLOBAL_CHAT_ID))


def _save_global_chat(user_msg: str, responses: list):
    """Guarda el turno del CHAT GENERAL (grupal) en un hilo PERSISTENTE cifrado — como el SOUL original
    guarda las conversaciones (William: «el chat general que guarde la conversación»). Best-effort."""
    try:
        from minisoul_store import store_memory
        key = _global_key()
        conn = _db()
        try:
            if (user_msg or "").strip():
                store_memory(conn, _GLOBAL_CHAT_ID, "global_chat", f"William: {user_msg[:300]}", 5, key,
                             scope="private", memory_type="episodic")
            for r in (responses or []):
                if r.get("reply"):
                    store_memory(conn, _GLOBAL_CHAT_ID, "global_chat",
                                 f"{r['agent']}: {r['reply'][:400]}", 5, key,
                                 scope="private", memory_type="episodic")
        finally:
            conn.close()
    except Exception:
        pass


def clone_global_history(limit: int = 100) -> dict:
    """Hilo PERSISTIDO del chat general (grupal), descifrado en memoria. El chat general lo recarga al abrir
    → «no se borra» y «guarda la conversación» (William)."""
    if not DB_PATH.exists():
        return {"_code": 200, "messages": []}
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT content, created_at FROM chunks WHERE agent=? AND category='global_chat' "
            "ORDER BY created_at ASC LIMIT ?", (_GLOBAL_CHAT_ID, limit)).fetchall()
    finally:
        conn.close()
    key, out = None, []
    for r in rows:
        c = r["content"]
        try:
            sys.path.insert(0, str(LIB_DIR))
            from minisoul_crypto import is_encrypted, decrypt_field
            if isinstance(c, str) and is_encrypted(c):
                if key is None:
                    key = _global_key()
                c = decrypt_field(c, key)
        except Exception:
            continue
        who, sep, text = c.partition(": ")
        out.append({"ts": r["created_at"], "from": who if sep else "?", "text": text if sep else c})
    return {"_code": 200, "count": len(out), "messages": out}


def clone_history(agent: str, limit: int = 50) -> dict:
    """Historial del DM/chat del clon (William: «que el chat guarde las conversaciones»). Lee las
    memorias category='conversation' del mini-SOUL local, las DESCIFRA EN MEMORIA solo para mostrar
    (FABLE: nunca escribir el descifrado a disco) y parsea el formato de _save_exchange."""
    import re as _re
    agent = (agent or "").strip().upper()
    if not agent:
        return {"_code": 400, "error": "agent requerido"}
    if not DB_PATH.exists():
        return {"_code": 200, "agent": agent, "exchanges": []}
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT content, created_at FROM chunks WHERE agent=? AND category='conversation' "
            "ORDER BY created_at ASC LIMIT ?", (agent, limit)).fetchall()
    finally:
        conn.close()
    key, out = None, []
    for r in rows:
        c = r["content"]
        try:
            sys.path.insert(0, str(LIB_DIR))
            from minisoul_crypto import is_encrypted, decrypt_field, derive_agent_key
            if isinstance(c, str) and is_encrypted(c):
                if key is None:
                    from minisoul_sync_daemon import load_atrest_secret
                    key = derive_agent_key(agent, load_atrest_secret(agent))
                c = decrypt_field(c, key)
        except Exception:
            continue
        m = _re.search(r"me dijeron «(.*?)» → respond[ií] «(.*?)»", c, _re.DOTALL)
        if m:
            out.append({"ts": r["created_at"], "user": m.group(1), "reply": m.group(2)})
        else:
            out.append({"ts": r["created_at"], "user": None, "reply": None, "raw": c})
    return {"_code": 200, "agent": agent, "count": len(out), "exchanges": out}


def _espejo_transcript(agent: str):
    """Transcript del claude del espejo del clon. ROBUSTO: usa el CWD REAL de la sesión tmux del espejo
    (no adivina el path) → funciona aunque la sesión haya abierto en un workspace distinto. Claude Code
    codifica el path del project-dir con / y . → -. Devuelve el .jsonl más reciente de ese dir.
    Fallback: el workspace per-clon esperado (por si no se pudo leer el CWD)."""
    agent = agent.upper()
    cwds = []
    r = _tmux("display-message", "-p", "-t", _espejo_session(agent), "#{pane_current_path}")
    if r.returncode == 0 and (r.stdout or "").strip():
        cwds.append(r.stdout.strip())
    cwds.append(str(SEAL_DIR / "workspace" / agent))   # fallback per-clon
    root = Path.home() / ".claude" / "projects"
    for cwd in cwds:
        pdir = root / cwd.replace("/", "-").replace(".", "-")
        if pdir.exists():
            js = sorted(pdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            if js:
                return js[0]
    return None


def _extract_text(content) -> str:
    """Saca el texto de un content de transcript (string o lista de bloques {type:text})."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(p for p in parts if p).strip()
    return ""


def clone_terminal_log(agent: str, limit: int = 40) -> dict:
    """REFLEJA la conversación de la TERMINAL del clon en el chat (William: «lo que conversa en terminal
    tiene que reflejarse en chat»). Lee el transcript del claude del espejo (ubicado por el marcador de su
    boot) y extrae los turnos user/clon. SCOPED por agente (FABLE: cada clon solo su propio DM)."""
    agent = (agent or "").strip().upper()
    if not agent:
        return {"_code": 400, "error": "agent requerido"}
    tpath = _espejo_transcript(agent)
    if not tpath:
        return {"_code": 200, "agent": agent, "turns": [], "nota": "terminal sin sesión aún"}
    turns = []
    try:
        with open(tpath, "r", errors="ignore") as fh:
            for raw in fh:
                try:
                    e = json.loads(raw)
                    typ = e.get("type")
                    msg = e.get("message", {}) if isinstance(e.get("message"), dict) else {}
                    ts = e.get("timestamp")
                    if typ not in ("user", "assistant"):
                        continue
                    text = _extract_text(msg.get("content"))
                    if not text or text.startswith("Sos ") or text.startswith("<"):
                        continue  # salta el boot inyectado y bloques no-conversacionales
                    turns.append({"role": "user" if typ == "user" else "clon", "text": text, "ts": ts})
                except Exception:
                    pass
    except Exception as e:
        return {"_code": 500, "error": f"no se pudo leer la terminal: {e}"}
    return {"_code": 200, "agent": agent, "count": len(turns), "turns": turns[-limit:]}


def clone_terminal_send(agent: str, message: str) -> dict:
    """CHAT → TERMINAL: escribe el mensaje en la terminal del clon (espejo), como si William lo tipeara.
    Unifica chat y terminal (William: «lo que escribís en el chat entra a su terminal»). Requiere el espejo
    ACTIVO. `-l` (literal) → no interpreta teclas; el Enter va aparte. Scoped a la sesión del agente."""
    agent = (agent or "").strip().upper()
    if not agent or not (message or "").strip():
        return {"_code": 400, "error": "agent y message requeridos"}
    sess = _espejo_session(agent)
    if _tmux("has-session", "-t", sess).returncode != 0:
        return {"_code": 409, "error": f"la terminal de {agent} no está abierta; activá el clon primero"}
    import time as _t
    # Bug cazado (5-jul): en una sesión fresca (splash «Welcome back») el mensaje entra al input box pero
    # el Enter inmediato NO lo submitea → queda atascado, «nunca aparece». Fix: tipear el mensaje, pausar
    # para que la TUI lo procese, y RECIÉN mandar Enter (C-m). El submit ahora registra confiable.
    _tmux("send-keys", "-t", sess, "-l", message)
    _t.sleep(0.6)
    _tmux("send-keys", "-t", sess, "Enter")
    return {"_code": 200, "ok": True, "agent": agent, "sent": message[:120]}


def clone_load_context(agent: str, limit: int = 30) -> dict:
    """«Cargar el contexto cuando se ordene» (William): junta el historial guardado del clon y lo
    SIEMBRA prominente vía la función de NEXUS (load_conversation_context) → aparece en el próximo boot
    del clon. Lo grueso ya está local; esto lo trae al frente del alma para que 'lo recuerde'."""
    agent = (agent or "").strip().upper()
    hist = clone_history(agent, limit)
    ex = hist.get("exchanges", [])
    if not ex:
        return {"_code": 200, "agent": agent, "loaded": 0, "nota": "sin historial que cargar"}
    # NEXUS espera una LISTA de {from, text} (no un string).
    convo = []
    for e in ex:
        if e.get("user"):
            convo.append({"from": "William", "text": e["user"]})
        if e.get("reply"):
            convo.append({"from": agent, "text": e["reply"]})
        if e.get("raw"):
            convo.append({"from": agent, "text": e["raw"]})
    try:
        sys.path.insert(0, str(LIB_DIR))
        from soul_lifecycle import load_conversation_context
        res = load_conversation_context(agent, convo, label="contexto-cargado-a-pedido")
        return {"_code": 200, "agent": agent, "loaded": len(ex),
                "seeded": res.get("loaded"), "seed": res}
    except Exception as e:
        return {"_code": 500, "error": f"no se pudo cargar contexto: {e}", "agent": agent}


# ── HTTP server (stdlib) ──
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json"):
        body = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, *a):  # silencio
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            if STUDIO_HTML.exists():
                return self._send(200, STUDIO_HTML.read_bytes(), "text/html; charset=utf-8")
            return self._send(404, b"studio UI not deployed", "text/plain")
        if self.path == "/api/device/status":
            return self._send(200, device_status())
        if self.path == "/api/device/clones":
            return self._send(200, list_clones())
        if self.path == "/api/device/models":
            return self._send(200, {"models": LATEST_MODELS, "default": DEFAULT_CHAT_MODEL})
        if self.path.startswith("/api/device/clones/history"):
            from urllib.parse import urlparse, parse_qs
            agent = parse_qs(urlparse(self.path).query).get("agent", [""])[0]
            res = clone_history(agent)
            return self._send(res.pop("_code", 200), res)
        if self.path.startswith("/api/device/clones/terminal-log"):
            from urllib.parse import urlparse, parse_qs
            agent = parse_qs(urlparse(self.path).query).get("agent", [""])[0]
            res = clone_terminal_log(agent)
            return self._send(res.pop("_code", 200), res)
        if self.path.startswith("/api/device/clones/global-history"):
            res = clone_global_history()
            return self._send(res.pop("_code", 200), res)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        body = self._body_json()
        agent = body.get("agent", "")
        routes = {
            "/api/device/clones/create": lambda: clone_create(agent),
            "/api/device/clones/sleep": lambda: clone_sleep(agent),
            "/api/device/clones/wake": lambda: clone_wake(agent, body.get("model")),
            "/api/device/clones/activate": lambda: clone_wake(agent, body.get("model")),
            "/api/device/clones/chat": lambda: clone_chat(agent, body.get("message", ""), body.get("model")),
            "/api/device/clones/conversation": lambda: clone_conversation(
                body.get("agents"), body.get("message", ""), body.get("model"), body.get("mode", "all")),
            "/api/device/clones/load-context": lambda: clone_load_context(agent),
            "/api/device/clones/terminal-send": lambda: clone_terminal_send(agent, body.get("message", "")),
            "/api/device/sync": lambda: trigger_sync(agent),
        }
        fn = routes.get(self.path)
        if not fn:
            return self._send(404, {"error": "not found"})
        res = fn()
        code = res.pop("_code", 200)
        return self._send(code, res)


def main():
    srv = ThreadingHTTPServer(BIND, Handler)
    print(f"[device-studio] sirviendo en http://{BIND[0]}:{BIND[1]} (mini-SOUL: {DB_PATH})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
