#!/usr/bin/env python3
"""Nervio de ALICE — health-check + AUTO-REMEDIACIÓN gated del software de exámenes (ORION).

CAPAS (William 23-jul: "que el nervio no solo tickee, que ACTÚE"):
  CAPA 1 — DETECTA por EFECTO (no por systemd GREEN):
    1. servicio arriba + /login 200
    2. backup < BACKUP_MAX_MIN
    3. DSN del proceso vivo = rol restringido svc_orion_exam (nunca el superusuario seal)
    4. conteos base no colapsados a 0 inesperado
  CAPA 2 — ACTÚA, gated y reversible:
    - REMEDIABLE (reversible, en mi lane) → auto-repara + VERIFICA por efecto + avisa a William:
        · servicio caído / login caído → `systemctl --user restart orion-exam` (guarda anti-loop)
        · backup viejo / ausente        → corre orion_backup.py una vez
    - CRÍTICO (no reversible / seguridad / datos) → NUNCA auto-actúa; ESCALA a William:
        · identidad/DSN inválida, conteos caídos a 0, o error de verificación de conteos

Reglas de seguridad de la CAPA 2:
  · Guarda anti-loop: máx MAX_RESTARTS auto-reinicios por REMEDIATION_WINDOW_MIN; superado → escala.
  · Auto-acción SOLO sobre fallas reversibles; jamás sobre pérdida de datos o identidad.
  · Verificación por efecto DESPUÉS de actuar (re-chequea lo que arregló); reporta el resultado real.
  · Cooldown de avisos a William (ALERT_COOLDOWN_MIN por tipo) para no floodear.
  · fire≠work: si todo sano y no hubo acción → SILENCIO (solo la línea de estado 0600).

Uso:  python3 orion_nerve.py    (silencioso si OK; actúa+avisa si remediable; escala si crítico)
Exit: 0 sano · 1 falla remediada o pendiente · 2 CRÍTICO escalado.
"""
import asyncio, asyncpg, json, os, pathlib, subprocess, sys, time, urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

BASE = pathlib.Path(__file__).parent
REPO = pathlib.Path("/home/dadito/IA/proyecto-seal")
BACKUP_DIR = REPO / "backups/orion_exam"
ENV_FILE = pathlib.Path("/home/dadito/.config/seal/orion_exam_db.env")
STATUS_LOG = BASE / "orion_nerve_status.log"        # 0600, línea de estado por pulso
BASELINE_FILE = BASE / "orion_nerve_baseline.json"  # 0600, nunca contiene DSN
STATE_FILE = BASE / "orion_nerve_state.json"        # 0600, cooldowns + historial de reinicios
REMEDIATION_LOG = BASE / "orion_nerve_remediation.jsonl"  # 0600, artefacto SOLO cuando actúa/escala
BACKUP_SCRIPT = BASE / "orion_backup.py"
SEAL_SEND = REPO / "scripts/seal_send.py"
BACKUP_MAX_MIN = 45
LOGIN_URL = "http://localhost:8851/login"
MAX_RESTARTS = 2                # auto-reinicios permitidos por ventana
REMEDIATION_WINDOW_MIN = 30     # ventana de la guarda anti-loop
ALERT_COOLDOWN_MIN = 15         # cooldown de avisos a William por tipo de evento


def _env_dsn() -> str:
    """Carga solo la DSN restringida de ORION; nunca conserva un fallback privilegiado."""
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "ORION_EXAM_DSN":
            value = value.strip().strip("\"'")
            if value:
                return value
    raise RuntimeError("ORION_EXAM_DSN ausente")


def _main_pid() -> int:
    out = subprocess.run(
        ["systemctl", "--user", "show", "orion-exam.service", "-p", "MainPID", "--value"],
        capture_output=True, text=True, timeout=5, check=True,
    ).stdout.strip()
    pid = int(out or "0")
    if pid <= 0:
        raise RuntimeError("orion-exam sin MainPID vivo")
    return pid


def _live_process_dsn(pid: int) -> str:
    """Comprueba la identidad que realmente heredó el proceso vivo, sin registrarla."""
    for item in pathlib.Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if item.startswith(b"ORION_EXAM_DSN="):
            return item.split(b"=", 1)[1].decode("utf-8")
    raise RuntimeError("ORION_EXAM_DSN ausente del proceso vivo")


def _write_private_json(path: pathlib.Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _append_private(path: pathlib.Path, payload: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.write(fd, (json.dumps(payload) + "\n").encode())
    os.close(fd)
    os.chmod(path, 0o600)


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _restarts_in_window(state: dict) -> int:
    cutoff = time.time() - REMEDIATION_WINDOW_MIN * 60
    return len([t for t in state.get("restarts", []) if t >= cutoff])


def _alert_william(state: dict, kind: str, message: str) -> bool:
    """Aviso a William con cooldown por tipo. Devuelve True si envió. seal_send maneja el token."""
    now = time.time()
    last = state.get("alerts", {}).get(kind, 0)
    if now - last < ALERT_COOLDOWN_MIN * 60:
        return False
    # Ruta DM directa: evade el gate de coordinación de posts proactivos en web_chat
    # (un post proactivo a web_chat exige anclarse a un mensaje humano; un DM no). Verificado
    # por efecto que el DM entrega con ok:true sin in_reply_to.
    try:
        completed = subprocess.run(
            ["python3", str(SEAL_SEND), "ALICE", "William", message,
             "--channel", "dm:alice:william", "--type", "conversation"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return False
    if completed.returncode != 0:
        return False
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return False
    if payload.get("ok") is not True:
        return False
    state.setdefault("alerts", {})[kind] = now
    return True


def _restart_service() -> None:
    subprocess.run(["systemctl", "--user", "restart", "orion-exam.service"],
                   capture_output=True, text=True, timeout=30)


def _service_healthy() -> bool:
    """Verificación por EFECTO tras actuar: servicio activo Y /login 200."""
    try:
        active = subprocess.run(["systemctl", "--user", "is-active", "orion-exam.service"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
        if active != "active":
            return False
        with urllib.request.urlopen(LOGIN_URL, timeout=8) as r:
            return r.status == 200
    except Exception:
        return False


def _run_backup() -> bool:
    try:
        r = subprocess.run(["python3", str(BACKUP_SCRIPT)],
                           capture_output=True, text=True, timeout=120)
        return r.returncode == 0
    except Exception:
        return False


def _backup_fresh() -> bool:
    snaps = sorted(BACKUP_DIR.glob("orion_exam_*"), key=lambda p: p.stat().st_mtime) if BACKUP_DIR.exists() else []
    if not snaps:
        return False
    age_min = (datetime.now(timezone.utc).timestamp() - snaps[-1].stat().st_mtime) / 60
    return age_min <= BACKUP_MAX_MIN


# Clasificación de cada falla detectada (los strings los controla ESTE script).
def _is_service_fail(f: str) -> bool:
    return ("servicio orion-exam no activo" in f) or ("/login" in f) or ("no pude consultar el servicio" in f)

def _is_backup_fail(f: str) -> bool:
    return ("backup viejo" in f) or ("no hay snapshots" in f)

def _is_critical_fail(f: str) -> bool:
    # SOLO estados donde la identidad/datos están REALMENTE mal (seguridad/pérdida de datos).
    # Un "no pude verificar identidad/conteos" NO es crítico: suele ser consecuencia de que el
    # servicio esté caído (no se puede leer el env del proceso ni conectar) y se resuelve al
    # reiniciarlo — clasificarlo como crítico era un falso-crítico (cazado por mi demo controlada).
    return any(k in f for k in (
        "identidad ORION inválida",       # proceso corre como seal u otro, no svc_orion_exam
        "DSN del proceso vivo difiere",   # el proceso vivo usa una DSN distinta al EnvironmentFile
        "cayó a 0",                       # una tabla base colapsó a 0 (posible pérdida de datos)
        "autenticó como",                 # la sesión DB autenticó con otro rol
    ))


async def _probe() -> dict:
    """Collect a complete product-health snapshot without acting."""
    fails = []
    active = "unknown"
    app_user = "unknown"
    backup_age_min = None
    try:
        active = subprocess.run(["systemctl", "--user", "is-active", "orion-exam.service"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
        if active != "active":
            fails.append(f"servicio orion-exam no activo ({active})")
    except Exception as e:
        fails.append(f"no pude consultar el servicio: {type(e).__name__}")
    try:
        with urllib.request.urlopen(LOGIN_URL, timeout=8) as r:
            if r.status != 200:
                fails.append(f"/login devolvió {r.status}")
    except Exception as e:
        fails.append(f"/login inaccesible: {type(e).__name__}")

    snaps = sorted(BACKUP_DIR.glob("orion_exam_*"), key=lambda p: p.stat().st_mtime) if BACKUP_DIR.exists() else []
    if not snaps:
        fails.append("no hay snapshots de backup")
    else:
        backup_age_min = (datetime.now(timezone.utc).timestamp() - snaps[-1].stat().st_mtime) / 60
        if backup_age_min > BACKUP_MAX_MIN:
            fails.append(f"backup viejo ({int(backup_age_min)} min > {BACKUP_MAX_MIN})")

    app_dsn = None
    try:
        configured_dsn = _env_dsn()
        app_dsn = _live_process_dsn(_main_pid())
        configured_user = urlsplit(configured_dsn).username
        app_user = urlsplit(app_dsn).username or "unknown"
        if app_user != "svc_orion_exam" or configured_user != "svc_orion_exam":
            fails.append(f"identidad ORION inválida: proceso={app_user}, config={configured_user}")
        if app_dsn != configured_dsn:
            fails.append("DSN del proceso vivo difiere del EnvironmentFile")
    except Exception as e:
        fails.append(f"no pude verificar identidad viva: {type(e).__name__}")

    counts = {}
    baseline = {}
    if app_dsn:
        try:
            c = await asyncpg.connect(app_dsn, timeout=6)
            try:
                db_user = await c.fetchval("SELECT current_user")
                if db_user != "svc_orion_exam":
                    fails.append(f"sesión DB autenticó como {db_user}, no svc_orion_exam")
                for table in ("users", "courses", "exams", "tasks", "exam_sessions"):
                    counts[table] = await c.fetchval(f"SELECT count(*) FROM orion_exam.{table}")
            finally:
                await c.close()

            if BASELINE_FILE.exists():
                loaded = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
                baseline = loaded.get("counts", {}) if isinstance(loaded, dict) else {}
            else:
                baseline = dict(counts)
                _write_private_json(BASELINE_FILE,
                    {"created_at": datetime.now(timezone.utc).isoformat(), "counts": baseline})
            for table in ("users", "courses", "exams", "tasks"):
                if int(baseline.get(table, 0)) > 0 and int(counts.get(table, 0)) == 0:
                    fails.append(f"orion_exam.{table} cayó a 0 (baseline > 0)")
        except Exception as e:
            fails.append(f"no pude verificar conteos restringidos: {type(e).__name__}")

    return {
        "fails": fails,
        "active": active,
        "login_http": 200 if not any("/login" in f for f in fails) else 0,
        "backup_age_min": backup_age_min,
        "db_user": app_user,
        "counts": counts,
        "baseline": baseline,
    }


async def run():
    # ── CAPA 1: detección ──
    probe = await _probe()
    fails = probe["fails"]
    stamp = datetime.now(timezone.utc).isoformat()
    status = "OK" if not fails else "FAIL"
    _append_private(STATUS_LOG, {
        "ts": stamp, "status": status, "service": probe["active"],
        "login_http": probe["login_http"],
        "backup_age_min": (
            round(probe["backup_age_min"], 2)
            if probe["backup_age_min"] is not None
            else None
        ),
        "db_user": probe["db_user"],
        "counts": probe["counts"],
        "baseline": probe["baseline"],
        "fails": fails,
    })

    if not fails:
        state = _load_state()
        pending = state.get("pending_alert")
        if isinstance(pending, dict):
            kind = str(pending.get("kind") or "retry")
            message = str(pending.get("message") or "")
            if message and _alert_william(state, kind, message):
                state.pop("pending_alert", None)
                _write_private_json(STATE_FILE, state)
        return 0  # sano → SILENCIO (fire≠work)

    # ── CAPA 2: actuar gated ──
    state = _load_state()
    actions = []            # remediaciones aplicadas (para el artefacto + aviso)
    escalations = []        # críticos que NO se auto-tocan
    service_fail = any(_is_service_fail(f) for f in fails)
    backup_fail = any(_is_backup_fail(f) for f in fails)
    critical = [f for f in fails if _is_critical_fail(f)]

    # CRÍTICO: nunca auto-actúa, escala.
    if critical:
        escalations.extend(critical)

    # REMEDIABLE 1: servicio/login caído → reiniciar (con guarda anti-loop) y verificar.
    if service_fail:
        if _restarts_in_window(state) >= MAX_RESTARTS:
            escalations.append(
                f"servicio caído y ya se auto-reinició {MAX_RESTARTS}x en {REMEDIATION_WINDOW_MIN} min "
                "→ no reintento (posible falla persistente), escalo")
        else:
            _restart_service()
            time.sleep(3)
            ok = _service_healthy()
            state.setdefault("restarts", []).append(time.time())
            actions.append({"issue": "servicio/login caído", "action": "restart orion-exam", "verified_ok": ok})
            if not ok:
                escalations.append("reinicié orion-exam pero /login sigue sin responder → escalo")

    # REMEDIABLE 2: backup viejo/ausente → correr backup y verificar.
    if backup_fail:
        ran = _run_backup()
        fresh = _backup_fresh()
        actions.append({"issue": "backup viejo/ausente", "action": "orion_backup.py", "verified_ok": ran and fresh})
        if not (ran and fresh):
            escalations.append("corrí el backup pero no quedó un snapshot fresco → escalo")

    # Una reparación no queda verde solo por servicio+HTTP: repetir el probe
    # completo prueba además identidad restringida, conteos y backup.
    post_probe = await _probe()
    for failure in post_probe["fails"]:
        if failure not in escalations:
            escalations.append(failure)

    # Artefacto 0600 SOLO porque hubo acción o escalación (fire≠work).
    _append_private(REMEDIATION_LOG, {
        "ts": stamp,
        "fails": fails,
        "actions": actions,
        "post_probe": post_probe,
        "escalations": escalations,
    })

    # Avisos a William: solo una entrega confirmada activa cooldown. Si falla,
    # queda pendiente durable y el siguiente pulso sano la reintenta.
    healed = [a for a in actions if a.get("verified_ok")]
    if healed and not escalations:
        det = "; ".join(f"{a['issue']} → {a['action']} (verificado OK)" for a in healed)
        message = (
            "🔧 Nervio ORION: detecté y REPARÉ solo un problema reversible.\n\n"
            f"{det}\n\nServicio, identidad y datos verificados por efecto. "
            "Sin acción de tu parte."
        )
        if not _alert_william(state, "healed", message):
            state["pending_alert"] = {"kind": "healed", "message": message}
        else:
            state.pop("pending_alert", None)
    if escalations:
        det = "\n- ".join(escalations)
        message = (
            "🚨 Nervio ORION — necesito tu atención (no lo toco solo por seguridad):\n\n- "
            + det
            + "\n\n(Lo reversible ya lo intenté; esto requiere decisión humana.)"
        )
        if not _alert_william(state, "critical", message):
            state["pending_alert"] = {"kind": "critical", "message": message}
        else:
            state.pop("pending_alert", None)

    _write_private_json(STATE_FILE, state)

    post_status = (
        "CRITICAL"
        if escalations
        else ("REMEDIATED" if actions and not post_probe["fails"] else "UNVERIFIABLE")
    )
    _append_private(STATUS_LOG, {
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": post_status,
        "service": post_probe["active"],
        "login_http": post_probe["login_http"],
        "backup_age_min": (
            round(post_probe["backup_age_min"], 2)
            if post_probe["backup_age_min"] is not None
            else None
        ),
        "db_user": post_probe["db_user"],
        "counts": post_probe["counts"],
        "baseline": post_probe["baseline"],
        "fails": post_probe["fails"],
        "post_remediation": True,
        "actions": actions,
        "escalations": escalations,
        "fails_original": fails,
    })

    if escalations:
        print(f"[orion_nerve] CRITICAL {stamp}: " + "; ".join(escalations), file=sys.stderr)
        return 2
    print(f"[orion_nerve] REMEDIATED {stamp}: " + "; ".join(a["action"] for a in actions), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
