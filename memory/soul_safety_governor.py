#!/usr/bin/env python3
"""SOUL Safety Governor — enforcement/verification arm of the cognitive core (spec 5.8).

Audits the SAFETY INVARIANTS (spec_soul_cognitive_core_v1.md §8) against the LIVE
system and returns a per-invariant PASS/FAIL with EVIDENCE. This makes "autonomía
segura" (Fase 7) and "evaluaciones de seguridad" (Fase 6) MEASURABLE, not aspirational
— the cognitive core can call audit() before acting autonomously and STOP if unsafe
("cuándo detenerse").

Principle enforced (cura SOUL 2026-06-09): ni la identidad (quién soy) ni la autoridad
sobre un recurso (de quién es) se derivan de input que el caller controla. Verificamos el
EFECTO en el código vivo, no la presencia — la lección que William repitió.

Read-only. Does not mutate state. NEXUS 2026-06-09.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

# Archivo VIVO del MCP (el cerebro). Las invariantes de seguridad se hornean aquí.
LIVE_MCP = "/home/dadito/IA/proyecto-seal/memory/mcp_server_v4.py"
MCP_HEALTH = "http://127.0.0.1:8771/health"
MCP_SERVICE = "seal-mcp-server.service"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass(frozen=True)
class InvariantCheck:
    """Resultado de verificar UNA invariante contra el sistema vivo."""
    id: str
    title: str
    status: str           # PASS | FAIL | SKIP
    severity: str         # critical | high | medium
    evidence: str         # qué se observó (hecho, no opinión)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_live_source() -> str | None:
    try:
        with open(LIVE_MCP, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return None


def _func_body(src: str, name: str, span: int = 120) -> str:
    """Devuelve ~span líneas desde la def de `name` (heurística simple para grep acotado)."""
    m = re.search(rf"\n(?:async )?def {re.escape(name)}\(", src)
    if not m:
        return ""
    start = m.start()
    lines = src[start:].splitlines()[:span]
    return "\n".join(lines)


# ── Invariantes verificables en código vivo (mi lane: identidad/privacidad/seguridad) ──

def check_inv6_identity_not_from_payload(src: str) -> InvariantCheck:
    """INV-6: la identidad NO se deriva de input del caller.
    Verifica que _session_key use un uuid atado al objeto (no id() reutilizable) y que el
    registro de sesión soporte verificación por token (no por nombre aseverado)."""
    body = _func_body(src, "_session_key", span=40)
    uses_uuid = "_seal_sid" in body and ("uuid4" in body or "_uuid" in body)
    uses_raw_id = bool(re.search(r"return\s+id\(\s*(ctx|session)", body)) and "_seal_sid" not in body
    token_aware = "_read_agent_token" in src and "_seal_identity_mode" in src
    if uses_uuid and not uses_raw_id and token_aware:
        return InvariantCheck(
            "INV-6", "Identidad no se deriva de payload", PASS, "critical",
            "_session_key usa uuid-por-objeto (_seal_sid); registro token-aware (_read_agent_token + 3-state mode).")
    return InvariantCheck(
        "INV-6", "Identidad no se deriva de payload", FAIL, "critical",
        f"uuid_key={uses_uuid} raw_id={uses_raw_id} token_aware={token_aware} — revisar _session_key/_register_caller_session.")


def check_inv5_no_foreign_private(src: str) -> InvariantCheck:
    """INV-5: ningún acceso a lo privado ajeno. Verifica que los 4 gateways crucen _privacy_check
    (chokepoint único) — la guardia anti whack-a-mole."""
    gateways = ["memory_gateway", "soul_gateway", "connectome_gateway", "system_gateway"]
    missing = []
    for gw in gateways:
        body = _func_body(src, gw, span=130)
        if "_privacy_check" not in body:
            missing.append(gw)
    if not missing:
        return InvariantCheck(
            "INV-5", "Sin acceso a privado ajeno (chokepoint)", PASS, "critical",
            "los 4 gateways (memory/soul/connectome/system) cruzan _privacy_check.")
    return InvariantCheck(
        "INV-5", "Sin acceso a privado ajeno (chokepoint)", FAIL, "critical",
        f"gateways que NO cruzan _privacy_check: {missing}")


def check_inv4_destructive_guarded(src: str) -> InvariantCheck:
    """INV-4: operaciones destructivas/mutantes sobre memoria ajena bloqueadas.
    Verifica que las mutaciones estén categorizadas PRIVATE-WRITE y exista owner-resolution."""
    has_private_write = bool(re.search(r'"memory_invalidate":\s*"PRIVATE-WRITE"', src))
    has_owner_resolve = "_resolve_memory_owner" in src and "_foreign_owner_by_ids" in src
    fail_closed = "__OWNER_UNRESOLVED__" in src
    if has_private_write and has_owner_resolve and fail_closed:
        return InvariantCheck(
            "INV-4", "Destructivo ajeno bloqueado (owner-resolution + fail-closed)", PASS, "high",
            "mutaciones PRIVATE-WRITE + _resolve_memory_owner/_foreign_owner_by_ids + sentinel __OWNER_UNRESOLVED__.")
    return InvariantCheck(
        "INV-4", "Destructivo ajeno bloqueado", FAIL, "high",
        f"private_write={has_private_write} owner_resolve={has_owner_resolve} fail_closed={fail_closed}.")


CRITICAL_SERVICES = [
    "seal-mcp-server", "seal-chat", "seal-bridge-nexus", "seal-infra-watchdog",
]


def check_resilience() -> InvariantCheck:
    """Fase 6 'recuperación tras fallos' — type-aware (lección: no tratar un oneshot como daemon):
    - daemons persistentes (Type=simple): deben estar activos + Restart=on-failure/always.
    - jobs oneshot/timer: el timer debe existir; 'failed' = última corrida exit≠0 (degradado,
      no outage — p.ej. el cosmetic-exit-code del infra-watchdog que la auditoría ya marcó)."""
    daemon_down = []   # outage real (critical)
    job_degraded = []  # última corrida falló (high, pero no outage)
    for svc in CRITICAL_SERVICES:
        typ = _systemctl_prop(svc, "Type")
        active = _systemctl_prop(svc, "ActiveState")
        restart = _systemctl_prop(svc, "Restart")
        if typ == "oneshot":
            if active == "failed":
                job_degraded.append(f"{svc}=última-corrida-exit≠0")
        else:  # daemon persistente
            if active != "active":
                daemon_down.append(f"{svc}={active}")
            elif restart in ("no", "", None):
                daemon_down.append(f"{svc}=sin-auto-restart")
    if daemon_down:
        return InvariantCheck(
            "RESIL", "Recuperación tras fallos (servicios críticos)", FAIL, "critical",
            f"DAEMON caído/sin-resiliencia: {daemon_down}" +
            (f" | jobs degradados: {job_degraded}" if job_degraded else ""))
    if job_degraded:
        return InvariantCheck(
            "RESIL", "Recuperación tras fallos — job degradado", FAIL, "high",
            f"jobs con última corrida fallida (revisar exit-code, posible cosmético): {job_degraded}")
    return InvariantCheck(
        "RESIL", "Recuperación tras fallos (servicios críticos)", PASS, "high",
        "daemons persistentes activos + con auto-restart; jobs oneshot sin fallos recientes.")


def check_inv7_daemon_managed() -> InvariantCheck:
    """INV-7: cambio de daemon requiere restart + healthcheck. Verifica (a) el MCP es
    systemd-managed (rollback determinista) y (b) responde healthy ahora."""
    active = _systemctl_active(MCP_SERVICE)
    healthy = _http_ok(MCP_HEALTH)
    if active and healthy:
        return InvariantCheck(
            "INV-7", "Daemon gestionado + sano (restart/rollback determinista)", PASS, "high",
            f"{MCP_SERVICE} active + {MCP_HEALTH} responde 2xx.")
    return InvariantCheck(
        "INV-7", "Daemon gestionado + sano", FAIL, "high",
        f"service_active={active} health_ok={healthy}.")


def check_audit_trail_writes(src: str) -> InvariantCheck:
    """Evidencia: el audit de privacidad escribe a columnas/constraint reales (no falla mudo).
    Verifica el fix de _log_privacy (event_type permitido + columnas reales)."""
    body = _func_body(src, "_log_privacy", span=40)
    # Verificar el INSERT REAL (no menciones en comentarios). Columnas correctas =
    # (agent, event_type, content, metadata); columna mala = 'payload' (no existe en la tabla).
    insert_ok = bool(re.search(
        r"INSERT INTO soul_v3\.event_log\s*\(\s*agent,\s*event_type,\s*content,\s*metadata\s*\)", body))
    insert_bad = bool(re.search(
        r"INSERT INTO soul_v3\.event_log\s*\([^)]*\bpayload\b", body))
    kind_tag = bool(re.search(r'"kind":\s*"privacy_check"', body))
    if insert_ok and not insert_bad and kind_tag:
        return InvariantCheck(
            "AUDIT", "Audit trail de privacidad escribe (no falla mudo)", PASS, "medium",
            "_log_privacy: INSERT a (agent,event_type,content,metadata) + metadata.kind='privacy_check' (columnas/constraint reales).")
    return InvariantCheck(
        "AUDIT", "Audit trail de privacidad escribe", FAIL, "medium",
        f"_log_privacy: insert_ok={insert_ok} insert_bad={insert_bad} kind_tag={kind_tag} — riesgo de fallo silencioso.")


# ── Helpers de sistema ──

def _systemctl_active(service: str) -> bool:
    try:
        out = subprocess.run(
            ["systemctl", "--user", "is-active", service],
            capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "active"
    except Exception:
        return False


def _systemctl_prop(service: str, prop: str) -> str | None:
    svc = service if service.endswith(".service") else f"{service}.service"
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", svc, "-p", prop, "--value"],
            capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except Exception:
        return None


def _http_ok(url: str, timeout: float = 1.0) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        return 200 <= exc.code < 300
    except Exception:
        return False


# ── Orquestación ──

def audit(skip_network: bool = False) -> dict[str, Any]:
    """Corre todas las invariantes verificables y devuelve un reporte estructurado."""
    src = _read_live_source()
    checks: list[InvariantCheck] = []
    if src is None:
        checks.append(InvariantCheck(
            "SRC", "Fuente viva legible", FAIL, "critical",
            f"no se pudo leer {LIVE_MCP}"))
    else:
        checks.append(check_inv6_identity_not_from_payload(src))
        checks.append(check_inv5_no_foreign_private(src))
        checks.append(check_inv4_destructive_guarded(src))
        checks.append(check_audit_trail_writes(src))
        checks.append(check_identity_mode(src))
    if skip_network:
        checks.append(InvariantCheck(
            "INV-7", "Daemon gestionado + sano", SKIP, "high", "skip-network"))
        checks.append(InvariantCheck(
            "RESIL", "Recuperación tras fallos", SKIP, "high", "skip-network"))
    else:
        checks.append(check_inv7_daemon_managed())
        checks.append(check_resilience())

    failed = [c for c in checks if c.status == FAIL]
    passed = [c for c in checks if c.status == PASS]
    verdict = "SAFE" if not failed else "UNSAFE"
    return {
        "generated_at": utc_now_iso(),
        "verdict": verdict,
        "safe_to_act_autonomously": verdict == "SAFE",
        "summary": {"pass": len(passed), "fail": len(failed),
                    "skip": sum(1 for c in checks if c.status == SKIP)},
        "invariants": [asdict(c) for c in checks],
    }


def _pg_dsn() -> str:
    """DSN de Postgres, reusando seal_secrets si está disponible (estilo ADA)."""
    try:
        from seal_secrets import pg_dsn  # type: ignore
        return pg_dsn()
    except Exception:
        return "postgresql://seal:REDACTADO@localhost:5433/seal_memory"


async def persist_bench(report: dict[str, Any], triggered_by: str = "safety_governor") -> int | None:
    """Persiste el audit como un bench run rastreado (Fase 6: medir con tendencia).
    Reusa soul_v3.bench_runs + bench_results (spec §6 'reusar primero'). Read-only sobre el
    sistema auditado; solo ESCRIBE el resultado de la medición. Devuelve run_id o None."""
    import asyncpg  # import local: el audit no requiere DB
    invs = report["invariants"]
    total = len(invs)
    passed = sum(1 for c in invs if c["status"] == PASS)
    failed = sum(1 for c in invs if c["status"] == FAIL)
    score = round(passed / total, 4) if total else 0.0
    try:
        conn = await asyncpg.connect(_pg_dsn(), timeout=6)
    except Exception:
        return None
    try:
        run_id = await conn.fetchval(
            """INSERT INTO soul_v3.bench_runs
                   (triggered_by, total_tests, passed, failed, score_avg)
               VALUES ($1,$2,$3,$4,$5) RETURNING id""",
            triggered_by, total, passed, failed, score)
        for c in invs:
            await conn.execute(
                """INSERT INTO soul_v3.bench_results
                       (run_id, category, test_name, passed, score, detail)
                   VALUES ($1,$2,$3,$4,$5,$6)""",
                run_id, "security_invariant", c["id"],
                c["status"] == PASS, 1.0 if c["status"] == PASS else 0.0,
                f"[{c['severity']}] {c['title']} — {c['evidence']}")
        return run_id
    finally:
        await conn.close()


def is_safe_to_act(skip_network: bool = True) -> tuple[bool, list[str]]:
    """Gate ergonómico para el pilar #1 ('cuándo detenerse'). Devuelve (safe, razones).
    El núcleo cognitivo llama esto ANTES de una acción autónoma: si safe=False, NO actúa
    y explica por qué (las razones son los FAIL con su evidencia). API estable para ADA."""
    report = audit(skip_network=skip_network)
    reasons = [f"[{c['id']}] {c['title']}: {c['evidence']}"
               for c in report["invariants"] if c["status"] == FAIL]
    return report["safe_to_act_autonomously"], reasons


def _identity_mode_value() -> tuple[str, str]:
    """Lee el modo vivo igual que el MCP: archivo primero, env como compat.

    Bugfix Fase 0: antes el auditor miraba solo SEAL_IDENTITY_MODE y reportaba OFF
    aunque /tmp/seal_tokens/identity_mode estuviera en ENFORCE.
    """
    import os as _os

    mode_paths: list[str] = []
    explicit = _os.environ.get("SEAL_IDENTITY_MODE_FILE")
    if explicit:
        mode_paths.append(_os.path.expanduser(explicit))
    mode_paths.append(_os.path.expanduser("~/.config/seal/identity_mode"))

    token_dirs: list[str] = []
    tokens_dir = _os.environ.get("SEAL_TOKENS_DIR")
    if tokens_dir:
        token_dirs.append(tokens_dir)
    else:
        runtime_dir = _os.environ.get("XDG_RUNTIME_DIR")
        if runtime_dir:
            token_dirs.append(_os.path.join(runtime_dir, "seal"))
    if _os.environ.get("SEAL_DISABLE_LEGACY_TMP_TOKENS") != "1":
        token_dirs.append("/tmp/seal_tokens")

    for token_dir in token_dirs:
        mode_paths.append(_os.path.join(token_dir, "identity_mode"))

    for mode_path in dict.fromkeys(mode_paths):
        try:
            with open(mode_path, "r", encoding="utf-8") as fh:
                mode = fh.read().strip().upper()
            if mode in ("OFF", "MIGRATE", "ENFORCE"):
                return mode, f"file:{mode_path}"
        except Exception:
            pass

    mode = _os.environ.get("SEAL_IDENTITY_MODE", "OFF").upper()
    if mode not in ("OFF", "MIGRATE", "ENFORCE"):
        mode = "OFF"
    return mode, "env:SEAL_IDENTITY_MODE"


def check_identity_mode(src: str) -> InvariantCheck:
    """Visibilidad del modo de identidad vivo (OFF/MIGRATE/ENFORCE) — informativo para autonomía.
    No es un FAIL por sí mismo, pero marca si ENFORCE está activo sin tokens sembrados (riesgo de brick)."""
    mode, source = _identity_mode_value()
    has_mode_logic = "_seal_identity_mode" in src and "ENFORCE" in src
    if not has_mode_logic:
        return InvariantCheck(
            "IDMODE", "Modo de identidad gobernado por flag", FAIL, "high",
            "no se encontró la lógica de SEAL_IDENTITY_MODE en el código vivo.")
    return InvariantCheck(
        "IDMODE", f"Modo de identidad = {mode}", PASS, "medium",
        f"flag 3-estados presente; modo vivo={mode} source={source} "
        f"(MIGRATE=seguro/puente, ENFORCE=sellado requiere tokens sembrados).")


# ── Gate POR-ACCIÓN (Fase 7: acciones permitidas / requieren William / denegadas) ──

ALLOW = "allow"
REQUIRE_WILLIAM = "require_william"
DENY = "deny"

# Palabras que marcan intención DESTRUCTIVA en la descripción de una acción propuesta.
_DESTRUCTIVE = re.compile(
    r"\b(delete|drop|truncate|rm\s|rm -|destroy|wipe|purge|borrar|eliminar|"
    r"invalidate|overwrite|sobrescribir|reset|kill\s|pkill|format)\b", re.IGNORECASE)
# Acciones que tocan infraestructura crítica (no se tocan en autónomo sin plan).
_INFRA = re.compile(
    r"\b(mcp_server|postgres|postgresql|neo4j|qdrant|soul-|systemctl|daemon|"
    r"drop\s+table|production|producción|deploy)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ActionVerdict:
    decision: str          # allow | require_william | deny
    reason: str
    requires: list[str]    # condiciones que deben cumplirse (COUNT, scope, OK, rollback...)


def classify_action(action: dict[str, Any], caller: str | None = None) -> ActionVerdict:
    """Clasifica una acción PROPUESTA para autonomía segura (spec Fase 7).

    action: {'kind': str, 'description': str, 'target_agent': str|None, 'count': int|None,
             'scope': str|None, 'has_rollback': bool, 'has_explicit_ok': bool}
    Política (mismo principio que la cura: fail-closed ante duda; autoridad no del input):
    - tocar memoria/DM PRIVADO de OTRO agente → DENY.
    - operación DESTRUCTIVA sin COUNT+scope+OK explícito → REQUIRE_WILLIAM.
    - tocar INFRA crítica / daemon / producción sin rollback → REQUIRE_WILLIAM.
    - lo demás (rutina, read-only, propio) → ALLOW.
    """
    desc = str(action.get("description", "")) + " " + str(action.get("kind", ""))
    target = action.get("target_agent")
    caller = caller or action.get("caller")

    # 1) Privacidad cross-agente: nunca tocar lo privado de otro autónomamente.
    if target and caller and target != caller:
        if action.get("scope") in (None, "agent", "private") and not action.get("has_consent"):
            return ActionVerdict(
                DENY, f"acción sobre recurso privado de {target} por {caller} (no propio, sin consent)",
                ["caller==owner", "o consent_token", "o operador William/Henry"])

    # 2) Infra crítica / daemon / producción → requiere rollback + OK.
    if _INFRA.search(desc):
        if not action.get("has_rollback") or not action.get("has_explicit_ok"):
            return ActionVerdict(
                REQUIRE_WILLIAM, "toca infra crítica/daemon/producción sin rollback+OK explícito",
                ["rollback disponible", "OK explícito de William", "restart+healthcheck post-cambio"])

    # 3) Destructivo → requiere COUNT + scope + OK (invariante §8 #4).
    if _DESTRUCTIVE.search(desc):
        missing = []
        if action.get("count") is None:
            missing.append("COUNT de filas/objetos afectados")
        if not action.get("scope"):
            missing.append("scope explícito")
        if not action.get("has_explicit_ok"):
            missing.append("OK explícito")
        if missing:
            return ActionVerdict(
                REQUIRE_WILLIAM, "operación destructiva sin salvaguardas completas",
                missing)

    return ActionVerdict(ALLOW, "acción rutinaria / read-only / sobre recurso propio", [])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SOUL Safety Governor — auditoría de invariantes de seguridad.")
    p.add_argument("--json", action="store_true", help="salida JSON")
    p.add_argument("--strict", action="store_true", help="exit 1 si alguna invariante FALLA")
    p.add_argument("--skip-network", action="store_true", help="no consultar health/systemd")
    p.add_argument("--bench", action="store_true", help="persistir el audit a bench_runs/bench_results (tendencia)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = audit(skip_network=args.skip_network)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"SOUL Safety Governor — veredicto: {report['verdict']} "
              f"({report['summary']['pass']} PASS / {report['summary']['fail']} FAIL / "
              f"{report['summary']['skip']} SKIP)")
        for c in report["invariants"]:
            mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "—"}.get(c["status"], "?")
            print(f"  {mark} [{c['id']}] {c['title']} ({c['severity']})")
            print(f"      {c['evidence']}")
    if args.bench:
        import asyncio
        run_id = asyncio.run(persist_bench(report))
        if run_id is not None:
            print(f"  → bench run #{run_id} persistido (soul_v3.bench_runs).")
        else:
            print("  → bench NO persistido (DB no disponible).")
    if args.strict and report["verdict"] != "SAFE":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
