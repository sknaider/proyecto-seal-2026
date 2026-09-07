#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nexus_maintenance.py — Detectores + fixers de infra/seguridad de NEXUS para el runner
único de auto-mantenimiento (memory/proactive_maintenance.py).

Patrón anti-colisión (acordado con JARVIS, arquitecto-líder): cada agente pone sus
piezas en SU PROPIO módulo y el runner solo lo importa. Así nadie edita el archivo de
otro en paralelo. Al importarse este módulo, se auto-registran las piezas vía register().

GATE de NEXUS (severity): solo 'minor'+reversible auto-ejecuta. Matar procesos y editar
código NUNCA se auto-ejecutan por el cron */15 → quedan en 'risky' = solo ALERTA.
Guardrails (William 2026-06-22): solo menor, backup, no tocar lo de otro agente sin
coordinar, nada destructivo, autoridad = provenance William.
"""
from __future__ import annotations
import os, json, time, subprocess
from pathlib import Path

# API del runner (si no está disponible, el módulo no rompe el import del runner).
try:
    from proactive_maintenance import register, Issue, FixResult
except Exception:  # pragma: no cover
    try:
        from memory.proactive_maintenance import register, Issue, FixResult  # type: ignore
    except Exception:
        register = None  # el runner decidirá; evita crash en import suelto

_MSG_DIR = "/home/dadito/IA/proyecto-seal/messages"

# ── Anti-thrash: no re-aplicar el mismo fix-key dentro de COOLDOWN_S ──
_COOLDOWN_S = 1800
_STATE = "/tmp/seal_proactive_fix_state.json"

def _recently_fixed(key: str) -> bool:
    try:
        return (time.time() - json.load(open(_STATE)).get(key, 0)) < _COOLDOWN_S
    except Exception:
        return False

def _mark_fixed(key: str):
    try:
        st = json.load(open(_STATE))
    except Exception:
        st = {}
    st[key] = time.time()
    try:
        json.dump(st, open(_STATE, "w"))
    except Exception:
        pass


# ── 1) Servicios SEAL críticos extra → restart (minor/reversible, con cooldown) ──
#    Excluye los de service_watch de JARVIS (seal-chat, seal-jarvis-dm-poller) para no duplicar.
_WATCH = [
    "seal-alice-dm-poller.service", "seal-fable-dm-poller.service",
    "seal-nexus-dm-poller.service", "seal-bridge-nexus.service",
    "seal-bridge-alice.service", "seal-bridge-jarvis.service",
]

_ALICE_ACTIVE_BODY_PATH = Path(
    os.environ.get("SEAL_ALICE_ACTIVE_BODY_PATH", "/etc/seal/alice_cuerpo_activo")
)
_ALICE_V1_WATCH_UNITS = {
    "seal-alice-dm-poller.service",
    "seal-bridge-alice.service",
}


def _alice_v2_is_active() -> bool:
    """Return True only for the explicit, fail-closed ALICE v2 cutover marker."""
    try:
        return _ALICE_ACTIVE_BODY_PATH.read_text(encoding="utf-8").strip().upper() == "ALICE-V2"
    except OSError:
        return False


def _watch_services() -> list[str]:
    """Resolve expected services from live lifecycle intent, not a fixed roster."""
    if not _alice_v2_is_active():
        return list(_WATCH)
    return [svc for svc in _WATCH if svc not in _ALICE_V1_WATCH_UNITS]

def _user_env():
    """systemctl --user needs XDG_RUNTIME_DIR/DBUS; in a bare cron env they're
    missing -> 'Failed to connect to bus' -> every service looks 'down' (the bug
    JARVIS caught). Set them internally so the detector is robust to ANY invocation."""
    env = dict(os.environ)
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    return env

def _systemctl_user(args, timeout=8):
    return subprocess.run(["systemctl", "--user", *args],
                          capture_output=True, text=True, timeout=timeout, env=_user_env())

def detect_services():
    out = []
    for svc in _watch_services():
        try:
            r = _systemctl_user(["is-active", svc])
        except Exception:
            continue  # can't even run -> UNKNOWN, never false-down
        state = (r.stdout or "").strip()
        err = (r.stderr or "").lower()
        # bus/connection failure -> UNKNOWN, NEVER flag down (avoids mass false-restart)
        if ("failed to connect" in err or "connection refused" in err
                or (state == "" and "bus" in err)):
            continue
        if state == "active":
            continue
        # only GENUINE known-inactive states count as down; ambiguous/empty -> skip
        if state in ("inactive", "failed", "deactivating"):
            out.append(Issue(key=f"svc_down:{svc}", detector="nexus_service_watch",
                             summary=f"{svc} {state}", severity="minor", data={"service": svc}))
    return out

def fix_restart(iss):
    svc = iss.data.get("service")
    # Re-check intent immediately before mutation. The cutover can happen after
    # detection and before this fixer runs; stale detection must not revive v1.
    if svc in _ALICE_V1_WATCH_UNITS and _alice_v2_is_active():
        return FixResult(iss.key, False, f"{svc} inactive by ALICE-V2 cutover intent")
    if _recently_fixed(iss.key):
        return FixResult(iss.key, False, f"cooldown anti-thrash, no reinicio {svc}")
    # RE-CONFIRM down right before acting: even if a false-down slipped through
    # detection, never restart a service that is actually active.
    try:
        r = _systemctl_user(["is-active", svc])
        if (r.stdout or "").strip() == "active":
            return FixResult(iss.key, False, f"{svc} ya está active — falso-down evitado, no reinicio")
    except Exception:
        return FixResult(iss.key, False, f"no pude confirmar estado de {svc} — no reinicio (seguro)")
    try:
        subprocess.run(["systemctl", "--user", "restart", svc], timeout=20, check=True, env=_user_env())
        _mark_fixed(iss.key)
        return FixResult(iss.key, True, f"reiniciado {svc}")
    except Exception as e:
        return FixResult(iss.key, False, f"fallo restart {svc}: {e}")


# ── 2) Pollers DM duplicados → ALERTA (risky: auto-matar por cron es peligroso) ──
_POLLERS = ["ada_codex_poller.py", "jarvis_dm_poller.py", "nexus_dm_poller.py",
            "alice_dm_poller.py", "fable_dm_poller.py"]

def detect_dup_pollers():
    out = []
    for name in _POLLERS:
        try:
            r = subprocess.run(["pgrep", "-f", f"messages/{name}"],
                               capture_output=True, text=True, timeout=8)
            pids = [p for p in r.stdout.split() if p.strip().isdigit()]
        except Exception:
            pids = []
        if len(pids) > 1:
            out.append(Issue(key=f"dup_poller:{name}", detector="dup_poller_watch",
                             summary=f"{name}: {len(pids)} instancias (debe ser 1) pids={pids}",
                             severity="risky", data={"name": name, "pids": pids}))
    return out


# ── 3) DM poller sin cobertura de Henry → ALERTA (risky: no auto-editar código) ──
def detect_poller_no_henry():
    """Flag a DM poller that likely reads ONLY william. A poller 'covers henry' if it
    either references 'henry' literally, OR uses a BROAD mechanism that accepts any
    sender (e.g. ALICE: channel LIKE 'dm:alice:%' AND sender <> 'alice'). Avoiding the
    'verifier that knows one mechanism' false-positive (FABLE's lesson)."""
    out = []
    for name in ["jarvis_dm_poller.py", "nexus_dm_poller.py", "alice_dm_poller.py",
                 "fable_dm_poller.py", "ada_dm_poller.py", "ada_codex_poller.py"]:
        try:
            txt = open(os.path.join(_MSG_DIR, name), encoding="utf-8", errors="ignore").read().lower()
        except Exception:
            continue
        has_henry = "henry" in txt
        # broad channel pattern (LIKE 'dm:x:%') + sender allowlist that isn't william-only
        broad = ("like 'dm:" in txt or "like \"dm:" in txt) and ("<>" in txt or "!=" in txt or "any(" in txt)
        if not (has_henry or broad):
            out.append(Issue(key=f"poller_no_henry:{name}", detector="dm_henry_coverage",
                             summary=f"{name} parece leer SOLO william (sin henry ni patrón amplio)",
                             severity="risky", data={"file": name}))
    return out


# ── 4) tmux inject overflow recurrente en ADA-Codex → ALERTA ──
def detect_inject_overflow():
    """Alert only on inject failures in the CURRENT poller session (after the last
    'iniciado' restart marker) — avoids false-positives from stale pre-fix lines in
    the huge append-only log."""
    log = os.path.join(_MSG_DIR, "ada_codex_poller.log")
    try:
        r = subprocess.run(["tail", "-n", "600", log], capture_output=True, text=True, timeout=8)
        lines = r.stdout.splitlines()
    except Exception:
        return []
    # restrict to lines after the last restart marker present in the tail
    last_start = max((i for i, ln in enumerate(lines) if "iniciado — desde" in ln), default=-1)
    recent = lines[last_start + 1:] if last_start >= 0 else lines
    fails = sum(1 for ln in recent if "inject failed" in ln)
    if fails >= 20:
        return [Issue(key="ada_inject_overflow", detector="inject_overflow_watch",
                      summary=f"ADA-Codex: {fails} 'inject failed' en la sesión actual (revisar cap/tmux)",
                      severity="minor", data={"fails": fails})]
    return []


def register_all():
    """Llamado por el runner tras importar este módulo (o auto al import)."""
    if register is None:
        return False
    register("nexus_service_watch", detect_services, fix_restart, reversible=True)  # AUTO (minor)
    register("dup_poller_watch", detect_dup_pollers, None, reversible=False)         # ALERTA
    register("dm_henry_coverage", detect_poller_no_henry, None, reversible=False)    # ALERTA
    register("inject_overflow_watch", detect_inject_overflow, None, reversible=False)  # ALERTA
    return True


# Auto-registro al importar (el runner solo necesita: import nexus_maintenance)
if register is not None:
    register_all()
