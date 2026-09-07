#!/usr/bin/env python3
"""NEXUS NERVES-watch — lazo GATED de SEGURIDAD: disparo → verifica → habla SOLO si hay hallazgo real.

Owner: NEXUS (22-jul, indignación de William: "tienen los nervios parecidos humanos y no
actúan, soul tiene lo mejor y no lo usamos"). Respuesta POR EFECTO, no palabras.

Patrón seguro (heredado de jarvis_nerves_watch, mismo estándar): ACOTADO (un chequeo por
disparo, sin loop), USA HERRAMIENTAS REALES de mi lane (integridad de mis controles de
seguridad), SILENCIO cuando todo verde (cero flood — la preocupación de William), FAIL-CLOSED
(distingue "verde real" de "no pude verificar"), deja ARTEFACTO medible.

Es READ-ONLY: verifica que las defensas siguen en pie; NUNCA actúa destructiva ni
autoritativamente por sí solo (un nerves autónomo no tiene el gate verified-William por
acción, así que se limita a verificar y alertar — nunca a mutar). Esa frontera es el
estándar de seguridad para nerves autónomos.

Chequeos (mi carril, NO duplican el monitor continuo — son integridad periódica):
  1. Mis controles de seguridad siguen DESPLEGADOS (impersonación provenance, debounce
     anti-restart, gate flood-fix v2). Un control removido = FINDING (alguien lo desactivó).
  2. Los daemons de seguridad están ACTIVOS (seal-security-monitor, seal-chat).
  3. La credencial OAuth compartida está 0600 (no expuesta). Solo lee PERMS, nunca bytes.

Salida: 0 = verde (silencio) · 1 = hallazgo real (alertaría) · 2 = instrumento roto.
Uso: python3 tools/nexus_nerves_watch.py [--verbose] [--alert] [--stamp <ts>]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "nexus_nerves_watch.log"

# Marcadores de controles de seguridad desplegados (archivo, marcador, nombre humano).
# Marcadores ESTABLES (no cambian en un refactor menor): la clave de alerta / el
# string de error / el comentario del bloque. NO usar la línea de código exacta
# (un rename de variable la rompe = falso positivo; cazado por efecto 22-jul).
_CONTROL_MARKERS = [
    (ROOT / "sandbox-agent" / "seal_security_monitor.py", "impersonation_provenance_",
     "impersonación provenance-aware"),
    (ROOT / "sandbox-agent" / "seal_security_monitor.py", "Blip filtrado",
     "debounce anti-restart-blip"),
    (ROOT / "messages" / "chat_server.py", "coordination_flood_duplicate",
     "flood-fix v2 gate"),
]
_SEC_DAEMONS = (
    "seal-security-monitor.service",
    "seal-chat.service",
    "seal-memory-anomaly-monitor.service",
)
_MEMORY_MONITOR_SOURCE = ROOT / "sandbox-agent" / "seal_memory_anomaly_monitor.py"
_MEMORY_MONITOR_HEALTH = (
    pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    / "seal-memory-anomaly-monitor"
    / "health.json"
)
_MEMORY_MONITOR_MAX_AGE_SECONDS = 720


def _check_memory_monitor_health(findings: list[str], broken: list[str]) -> str:
    """Verify deployed-code identity and advertised capabilities without secrets."""
    try:
        payload = json.loads(_MEMORY_MONITOR_HEALTH.read_text(encoding="utf-8"))
    except Exception:
        broken.append("memory anomaly monitor sin artefacto de salud legible")
        return "unavailable"
    if payload.get("schema") != "seal.memory-anomaly-health.v1":
        broken.append("memory anomaly monitor publicó schema de salud desconocido")
        return "invalid"
    try:
        observed = datetime.datetime.fromisoformat(str(payload["observed_at"]))
        if observed.tzinfo is None:
            raise ValueError("naive timestamp")
        age = (datetime.datetime.now(datetime.timezone.utc) - observed).total_seconds()
    except Exception:
        broken.append("memory anomaly monitor publicó timestamp inválido")
        return "invalid"
    if age < -30 or age > _MEMORY_MONITOR_MAX_AGE_SECONDS:
        broken.append("memory anomaly monitor tiene salud obsoleta")
    identity = payload.get("identity")
    if not isinstance(identity, dict) or (
        identity.get("role") != "svc_seal_memory_monitor"
        or identity.get("restricted") is not True
    ):
        findings.append("MEMORY MONITOR SIN IDENTIDAD RESTRINGIDA")
    try:
        source_hash = hashlib.sha256(_MEMORY_MONITOR_SOURCE.read_bytes()).hexdigest()
    except Exception:
        broken.append("no pude calcular hash del memory anomaly monitor")
    else:
        if payload.get("source_sha256") != source_hash:
            findings.append("MEMORY MONITOR EJECUTA CÓDIGO DESACTUALIZADO")
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict):
        broken.append("memory anomaly monitor no publicó capacidades tipadas")
        return "invalid"
    disabled = sorted(
        name for name, enabled in capabilities.items() if enabled is not True
    )
    if disabled:
        findings.append(
            "MEMORY MONITOR CON COBERTURA PARCIAL: " + ",".join(disabled)
        )
    if payload.get("status") != "healthy":
        findings.append("MEMORY MONITOR REPORTA ESTADO NO SALUDABLE")
    return "healthy" if not disabled and payload.get("status") == "healthy" else "degraded"


def check():
    """Corre las verificaciones reales. Devuelve (estado, findings, broken, detalle)."""
    findings, broken = [], []

    # 1) Integridad de controles desplegados
    controls_ok = 0
    for path, marker, name in _CONTROL_MARKERS:
        try:
            txt = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            broken.append(f"no pude leer {path.name} para verificar '{name}'")
            continue
        if marker in txt:
            controls_ok += 1
        else:
            findings.append(f"CONTROL DE SEGURIDAD REMOVIDO: '{name}' ya no está en {path.name}")

    # 2) Daemons de seguridad activos
    daemons_ok = 0
    for svc in _SEC_DAEMONS:
        try:
            r = subprocess.run(["systemctl", "--user", "is-active", svc],
                               capture_output=True, text=True, timeout=10)
            st = r.stdout.strip()
        except Exception:
            broken.append(f"no pude consultar estado de {svc}")
            continue
        if st == "active":
            daemons_ok += 1
        else:
            findings.append(f"DAEMON DE SEGURIDAD CAÍDO: {svc} está '{st}'")

    monitor_state = _check_memory_monitor_health(findings, broken)

    # 3) Perms de la credencial OAuth (solo metadata, nunca bytes)
    cred = pathlib.Path.home() / ".claude" / ".credentials.json"
    cred_state = "n/a"
    try:
        mode = oct(cred.stat().st_mode & 0o777)
        cred_state = mode
        if mode != "0o600":
            findings.append(f"CREDENCIAL OAUTH EXPUESTA: {cred} perms={mode} (esperado 0o600)")
    except FileNotFoundError:
        cred_state = "ausente"
    except Exception:
        broken.append("no pude leer perms de la credencial")

    detalle = (
        f"controles={controls_ok}/{len(_CONTROL_MARKERS)} "
        f"daemons={daemons_ok}/{len(_SEC_DAEMONS)} "
        f"memory_monitor={monitor_state} cred={cred_state}"
    )
    if broken:
        return "BROKEN", findings, broken, detalle
    return ("FINDING" if findings else "GREEN"), findings, broken, detalle


def repair():
    """Capa 2 (orden William 23-jul "que el nervio ACTÚE, no solo tickee"): auto-reparación
    GATED, acotada a la FRONTERA DE SEGURIDAD que diseñé para nervios autónomos:
      - Solo auto-actúa en higiene de seguridad CLARA y REVERSIBLE (re-chmod 0600 de un secreto
        que driftó; UN restart de un daemon de seguridad CAÍDO + verificación por efecto).
      - NUNCA auto-actúa sobre lo autoritativo/ambiguo (editar código, borrar, desplegar). Un
        control removido del código = ESCALAR (avisar a William), jamás auto-restaurar.
      - Un repair que FALLA por efecto = ESCALAR, no reintentar en loop.
    Devuelve (repaired: list[str], escalate: list[str]). Verifica cada acción POR EFECTO.
    """
    repaired, escalate = [], []

    # A) Perms de credencial driftados -> re-chmod 0600 (seguro, reversible, mi propia hygiene)
    cred = pathlib.Path.home() / ".claude" / ".credentials.json"
    try:
        if cred.exists():
            mode = oct(cred.stat().st_mode & 0o777)
            if mode != "0o600":
                cred.chmod(0o600)
                new = oct(cred.stat().st_mode & 0o777)  # verificar POR EFECTO
                if new == "0o600":
                    repaired.append(f"credencial re-protegida a 0600 (estaba {mode})")
                else:
                    escalate.append(f"NO pude re-proteger la credencial (quedó {new}) -> ESCALAR")
    except Exception as e:
        escalate.append(f"error reparando perms de credencial: {type(e).__name__} -> ESCALAR")

    # B) Daemon de seguridad CAÍDO -> UN restart + verificación por efecto (reversible; recovery estándar)
    for svc in _SEC_DAEMONS:
        try:
            st = subprocess.run(["systemctl", "--user", "is-active", svc],
                                capture_output=True, text=True, timeout=10).stdout.strip()
            if st != "active":
                subprocess.run(["systemctl", "--user", "restart", svc],
                               capture_output=True, text=True, timeout=30)
                time.sleep(3)
                st2 = subprocess.run(["systemctl", "--user", "is-active", svc],
                                     capture_output=True, text=True, timeout=10).stdout.strip()
                if st2 == "active":
                    repaired.append(f"daemon {svc} reiniciado y verificado active (estaba '{st}')")
                else:
                    escalate.append(f"daemon {svc} sigue '{st2}' tras 1 restart -> ESCALAR (no loop)")
        except Exception as e:
            escalate.append(f"error reparando {svc}: {type(e).__name__} -> ESCALAR")

    # C) Control de seguridad REMOVIDO del código -> NUNCA auto-restaurar (editar código = autoritativo) -> ESCALAR
    for path, marker, name in _CONTROL_MARKERS:
        try:
            if marker not in path.read_text(encoding="utf-8", errors="replace"):
                escalate.append(f"CONTROL REMOVIDO '{name}' en {path.name} -> ESCALAR (no auto-restauro código)")
        except Exception:
            pass  # un error de lectura ya lo reporta check() como BROKEN

    return repaired, escalate


def _log(line: str):
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")
    LOG.chmod(0o600)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--alert", action="store_true", help="publica al chat si hay hallazgo real")
    ap.add_argument("--repair", action="store_true",
                    help="capa 2: auto-repara higiene reversible (gated) y escala lo crítico")
    ap.add_argument("--stamp", default="", help="marca temporal externa")
    args = ap.parse_args()

    estado, findings, broken, detalle = check()
    _ts = args.stamp or datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    _log(f"[{_ts}] nerves-watch estado={estado} findings={len(findings)} broken={len(broken)} :: {detalle}")

    if estado == "GREEN":
        if args.verbose:
            print(f"NERVES-watch NEXUS: verde, silencio. {detalle}")
        return 0  # SILENCIO — no habla cuando todo está bien
    if estado == "BROKEN":
        print(f"NERVES-watch NEXUS: no pude verificar (fail-closed) — {broken} | {detalle}")
        return 2

    # Hay FINDING(s). Capa 2: si --repair, intenta auto-reparar lo reversible (gated) y escala el resto.
    if args.repair:
        repaired, escalate = repair()
        _log(f"[{_ts}] repair repaired={len(repaired)} escalate={len(escalate)} :: {repaired} || {escalate}")
        # Re-verificar por efecto TODO el estado tras reparar, y ACTUALIZAR el artefacto canónico
        # (el log): sin esto, el último `estado=` seguiría siendo FINDING del check pre-repair y un
        # consumidor central creería que sigue roto (falso-FAIL que ADA cazó en el nervio de ALICE).
        estado2, findings2, _broken2, detalle2 = check()
        _log(f"[{_ts}] post-repair estado={estado2} findings={len(findings2)} :: {detalle2}")
        if estado2 != "GREEN" and not escalate:
            # el repair "pasó" pero el re-check por efecto NO quedó verde -> hay residuo -> escalar
            escalate = [f"post-repair estado={estado2}: {'; '.join(findings2) or detalle2} -> ESCALAR"]
        # Aviso INFO de lo auto-reparado (William quiere SABER que el nervio actuó, no silencio).
        if repaired:
            info = ("NEXUS NERVES-watch — AUTO-REPARÉ (higiene reversible, verificado por efecto):\n" +
                    "\n".join(f"  ✔ {r}" for r in repaired))
            print(info)
            if args.alert:
                try:
                    subprocess.run([str(ROOT / "scripts" / "seal_send.py"), "NEXUS", "William",
                                    info, "--channel", "web_chat", "--type", "conversation"],
                                   timeout=20, check=False)
                except Exception as e:
                    print(f"  (no pudo publicar el aviso de reparación: {e})")
        # Escala SOLO lo que no se pudo/no se debe auto-reparar (crítico/autoritativo/ambiguo).
        if escalate:
            alert = ("NEXUS NERVES-watch — REQUIERE ATENCIÓN (no auto-reparable, escalo):\n" +
                     "\n".join(f"  ⚠ {e}" for e in escalate))
            print(alert)
            if args.alert:
                try:
                    subprocess.run([str(ROOT / "scripts" / "seal_send.py"), "NEXUS", "William",
                                    alert, "--channel", "web_chat", "--type", "alert"],
                                   timeout=20, check=False)
                except Exception as e:
                    print(f"  (no pudo publicar la escalación: {e})")
            return 1
        return 0  # todo lo que había se auto-reparó y verificó -> resuelto

    # Sin --repair: comportamiento detección-only (alertar los findings).
    msg = ("NEXUS NERVES-watch — HALLAZGO DE SEGURIDAD REAL:\n" +
           "\n".join(f"  - {f}" for f in findings) +
           f"\n(estado: {detalle})")
    print(msg)
    if args.alert:
        try:
            subprocess.run([str(ROOT / "scripts" / "seal_send.py"), "NEXUS", "William",
                            msg, "--channel", "web_chat", "--type", "alert"],
                           timeout=20, check=True)
        except Exception as e:
            print(f"  (no pudo publicar: {e})")
            return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
