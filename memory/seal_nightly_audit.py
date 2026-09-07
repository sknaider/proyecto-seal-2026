#!/usr/bin/env python3
"""SEAL Nightly Audit — NEXUS 2026-05-17

Runs every night via systemd timer. Health checks across:
  - DB: size, top tables, bloat, long queries, connections
  - Services: critical systemd units active
  - Logs: recurring errors in last 24h
  - Recall: BM25 sanity, missing embeddings
  - Bloat: dead tuple ratio per table

Output:
  - /home/dadito/IA/proyecto-seal/memory/nightly_audit.jsonl (append)
  - POST to web_chat with summary (NEXUS author) only if RED/YELLOW

Designed to be quiet on GREEN (no chat spam) and loud on regressions.
"""

import asyncio
import asyncpg
from seal_secrets import pg_dsn
import json
import subprocess
import urllib.request
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path("/home/dadito/IA/proyecto-seal/memory/nightly_audit.jsonl")
WEBCHAT_URL = "http://localhost:8765/api/agents/send"

# Services that must be active for SEAL to be healthy
CRITICAL_SERVICES = [
    "seal-chat.service",
    "seal-studio-backend.service",
    "seal-mcp-server.service",
]

# Retired after efficacy audit (2026-07-22): the bridge mirrored every webchat
# message into event_log and a capped /tmp queue, but no live cortex consumed
# that queue and no eligible NEXUS instinct existed.  DOWN is the desired state;
# resurrection is reported as noise/drift.
RETIRED_SERVICES = [
    "seal-event-bus.service",
    "seal-webchat-bridge.service",
]

# External chat transports are intentionally deployable/disableable.  A disabled
# optional bridge must not turn the health of the canonical SEAL runtime RED.
OPTIONAL_SERVICES = [
    "seal-mm-bridge.service",
    "seal-matrix-bridge.service",
]

# Tables that should NEVER have last_vacuum=None (autovacuum should touch them)
WATCHED_TABLES = [
    "memories", "chat_messages", "event_log", "inner_monologue",
    "instinct_activations", "memory_retrieval_log",
]

# Thresholds — adjust as system grows
THRESHOLD_DB_SIZE_MB = 5000          # alert if DB exceeds 5GB
THRESHOLD_DEAD_PCT_WARN = 30.0       # warn if dead/live > 30%
THRESHOLD_DEAD_PCT_CRIT = 100.0      # critical if dead/live > 100%
# Edad máxima de la estadística de la que sale el denominador del bloat.
# Más vieja que esto y el porcentaje no se reporta: se declara UNMEASURABLE.
STATS_MAX_AGE_S = 6 * 3600
THRESHOLD_CONNECTIONS_WARN = 70      # warn if connections > 70/100
THRESHOLD_LONG_QUERY_SECONDS = 60    # warn on queries > 60s
THRESHOLD_ERROR_LINES_24H = 500      # warn if >500 error lines / day


def resolve_db_dsn() -> str:
    return pg_dsn(required=True)


async def check_db(conn) -> dict:
    findings = []
    metrics = {}

    # DB size
    size_mb = await conn.fetchval(
        "SELECT pg_database_size(current_database()) / 1024 / 1024"
    )
    metrics["db_size_mb"] = int(size_mb)
    if size_mb > THRESHOLD_DB_SIZE_MB:
        findings.append({
            "sev": "WARN",
            "msg": f"DB size {size_mb}MB exceeds threshold {THRESHOLD_DB_SIZE_MB}MB",
        })

    # Connections
    conn_rows = await conn.fetch(
        "SELECT state, COUNT(*) FROM pg_stat_activity WHERE datname='seal_memory' GROUP BY state"
    )
    conn_total = sum(r["count"] for r in conn_rows)
    metrics["connections_total"] = conn_total
    if conn_total > THRESHOLD_CONNECTIONS_WARN:
        findings.append({
            "sev": "WARN",
            "msg": f"DB connections {conn_total} approaching limit (max 100)",
        })

    # Long-running queries
    long_q = await conn.fetch(
        "SELECT pid, EXTRACT(EPOCH FROM (NOW() - query_start)) AS age "
        "FROM pg_stat_activity WHERE datname='seal_memory' AND state='active' "
        f"AND NOW() - query_start > INTERVAL '{THRESHOLD_LONG_QUERY_SECONDS} seconds'"
    )
    metrics["long_running_count"] = len(long_q)
    if long_q:
        findings.append({
            "sev": "CRIT",
            "msg": f"{len(long_q)} query(es) running > {THRESHOLD_LONG_QUERY_SECONDS}s — possible lock or runaway",
        })

    # Bloat per table
    # `n_live_tup` es un ESTIMADO que sólo ANALYZE/autoanalyze actualiza, así que un
    # porcentaje construido sobre él miente justo cuando esa estadística está vieja —
    # es decir, exactamente en las tablas que este audit debería vigilar.
    #
    # Medido el 28-ago: reporté `memories bloat 1438% (live=637 dead=9160)` con 152.714
    # filas reales. El denominador estaba desactualizado, no la tabla inflada. JARVIS
    # corrió ANALYZE y el mismo cálculo dio 22,5%. Un CRIT falso a las 03:15, y encima
    # el más alarmante del reporte.
    #
    # La cura no es afinar el umbral: es preguntar si la estadística sirve ANTES de
    # derivar un porcentaje de ella. Sin frescura, el veredicto es UNMEASURABLE.
    bloat_rows = await conn.fetch("""
        SELECT relname, n_live_tup, n_dead_tup,
               CASE WHEN n_live_tup > 0 THEN (n_dead_tup::float / n_live_tup) * 100 ELSE 0 END AS dead_pct,
               pg_total_relation_size('soul_v3.'||relname) AS bytes,
               GREATEST(last_analyze, last_autoanalyze) AS stats_at,
               EXTRACT(EPOCH FROM (now() - GREATEST(last_analyze, last_autoanalyze))) AS stats_age_s
        FROM pg_stat_user_tables WHERE schemaname='soul_v3' AND relname=ANY($1)
    """, WATCHED_TABLES)
    bloat_summary = []
    for r in bloat_rows:
        bloat_summary.append({
            "table": r["relname"],
            "live": r["n_live_tup"],
            "dead": r["n_dead_tup"],
            "dead_pct": round(r["dead_pct"], 1),
            "size_kb": r["bytes"] // 1024,
        })
        # Frescura del denominador ANTES del veredicto. Sin estadística reciente el
        # porcentaje no es un dato malo: no es un dato. Se dice, no se convierte en CRIT.
        stats_age = r["stats_age_s"]
        stale = stats_age is None or stats_age > STATS_MAX_AGE_S
        bloat_summary[-1]["stats_age_s"] = None if stats_age is None else int(stats_age)
        bloat_summary[-1]["stats_stale"] = stale

        if stale:
            edad = "nunca analizada" if stats_age is None else f"stats de hace {int(stats_age)//3600} h"
            findings.append({
                "sev": "UNMEASURABLE",
                "msg": (f"{r['relname']}: no puedo calcular bloat — {edad} "
                        f"(n_live_tup={r['n_live_tup']} es un estimado sin refrescar). "
                        f"Correr ANALYZE soul_v3.{r['relname']} y volver a medir."),
            })
        elif r["dead_pct"] > THRESHOLD_DEAD_PCT_CRIT:
            findings.append({
                "sev": "CRIT",
                "msg": f"{r['relname']} bloat {r['dead_pct']:.0f}% (live={r['n_live_tup']} dead={r['n_dead_tup']}, stats {int(stats_age)//60} min)",
            })
        elif r["dead_pct"] > THRESHOLD_DEAD_PCT_WARN:
            findings.append({
                "sev": "WARN",
                "msg": f"{r['relname']} bloat {r['dead_pct']:.0f}% (live={r['n_live_tup']} dead={r['n_dead_tup']}, stats {int(stats_age)//60} min)",
            })
    metrics["bloat"] = bloat_summary

    # BM25 missing — active memories without embedding_bm25
    bm25_missing = await conn.fetchval("""
        SELECT COUNT(*) FROM soul_v3.memories
        WHERE invalid_at IS NULL AND embedding_bm25 IS NULL
    """)
    metrics["bm25_missing_active"] = bm25_missing
    if bm25_missing > 0:
        findings.append({
            "sev": "WARN",
            "msg": f"{bm25_missing} active memorias sin embedding_bm25 — recall lexical incompleto",
        })

    return {"findings": findings, "metrics": metrics}


def check_services() -> dict:
    findings = []
    states = {}
    for svc in CRITICAL_SERVICES:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "is-active", svc],
                capture_output=True, text=True, timeout=5,
            )
            state = r.stdout.strip()
            states[svc] = state
            if state != "active":
                findings.append({
                    "sev": "CRIT",
                    "msg": f"Critical service {svc} is '{state}'",
                })
        except Exception as e:
            findings.append({
                "sev": "CRIT",
                "msg": f"Service check failed for {svc}: {e}",
            })

    for svc in OPTIONAL_SERVICES:
        try:
            enabled = subprocess.run(
                ["systemctl", "--user", "is-enabled", svc],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if enabled not in {"enabled", "enabled-runtime", "linked", "linked-runtime"}:
                states[svc] = f"disabled ({enabled or 'unknown'}; optional)"
                continue
            state = subprocess.run(
                ["systemctl", "--user", "is-active", svc],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            states[svc] = state
            if state != "active":
                findings.append({
                    "sev": "WARN",
                    "msg": f"Enabled optional service {svc} is '{state}'",
                })
        except Exception as e:
            findings.append({
                "sev": "WARN",
                "msg": f"Optional service check failed for {svc}: {e}",
            })

    for svc in RETIRED_SERVICES:
        try:
            active = subprocess.run(
                ["systemctl", "--user", "is-active", svc],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            enabled = subprocess.run(
                ["systemctl", "--user", "is-enabled", svc],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            states[svc] = f"{active or 'unknown'} ({enabled or 'unknown'}; retired)"
            if active == "active" or enabled in {"enabled", "enabled-runtime"}:
                findings.append({
                    "sev": "WARN",
                    "msg": f"Retired service {svc} resurrected: active={active}, enabled={enabled}",
                })
        except Exception as e:
            findings.append({
                "sev": "WARN",
                "msg": f"Retired service check failed for {svc}: {e}",
            })
    return {"findings": findings, "metrics": {"services": states}}


# Ruido benigno conocido (NO son fallas del sistema SEAL — no deben inflar errors/24h).
_LOG_NOISE = (
    "ui/compositor", "services/audio", "media/audio", "shared_memory_switch",
    "Failed global descriptor lookup", "brave-browser", "update-notifier",
    "gpu/command_buffer", "viz/", "GpuChannelHost",
)
# Servicios con crashloop conocido → contar AGREGADO (1 causa raíz, no N errores distintos).
_LOG_CRASHLOOP_SVCS = ("seal-codex-app-windows-tunnel",)


def check_logs() -> dict:
    findings = []
    metrics = {}
    try:
        r = subprocess.run(
            ["journalctl", "--user", "--since", "24 hours ago", "--no-pager"],
            capture_output=True, text=True, timeout=30,
        )
        lines = r.stdout.splitlines()
        raw = [l for l in lines if any(k in l for k in ["Traceback", "ERROR", "Failed", "CRITICAL"])]
        # Agregar crashloops conocidos como UN finding c/u
        crashloop = {svc: sum(1 for l in raw if svc in l) for svc in _LOG_CRASHLOOP_SVCS}
        crashloop = {k: v for k, v in crashloop.items() if v > 0}
        # Errores REALES = ni ruido benigno ni crashloop-agregado
        real = [l for l in raw
                if not any(n in l for n in _LOG_NOISE)
                and not any(svc in l for svc in _LOG_CRASHLOOP_SVCS)]
        metrics["error_lines_24h"] = len(real)            # señal REAL (de-noised) — la que importa
        metrics["error_lines_24h_raw"] = len(raw)         # crudo, para referencia
        metrics["noise_excluded_24h"] = len(raw) - len(real) - sum(crashloop.values())
        metrics["crashloop_aggregated"] = crashloop
        # Cada crashloop = 1 finding agregado (no N)
        for svc, n in crashloop.items():
            findings.append({
                "sev": "WARN",
                "msg": f"crashloop {svc}: {n} fallos/24h (1 causa raíz agregada, no {n} errores distintos)",
            })
        # Threshold ahora sobre errores REALES
        if len(real) > THRESHOLD_ERROR_LINES_24H:
            findings.append({
                "sev": "WARN",
                "msg": f"{len(real)} líneas de error REALES en 24h (de-noised, threshold {THRESHOLD_ERROR_LINES_24H})",
            })
        # Top 5 recurring sobre REALES
        from collections import Counter
        counter = Counter()
        for l in real:
            parts = l.rsplit(":", 1)
            tail = parts[-1].strip()[:80] if parts else l[:80]
            counter[tail] += 1
        metrics["top_errors"] = [{"pattern": p, "count": c} for p, c in counter.most_common(5)]
    except Exception as e:
        findings.append({"sev": "WARN", "msg": f"Log check failed: {e}"})
    return {"findings": findings, "metrics": metrics}


def post_webchat(message: str) -> bool:
    """Postea al webchat. Retorna True si entregó, False si falló — el caller
    DEBE chequearlo: un fallo = ALERTING BLACKOUT (el audit corrió pero su
    resultado no llegó a nadie). Antes solo printeaba a stderr sin señal de éxito."""
    try:
        payload = json.dumps({
            "from": "NEXUS",
            "to": "equipo",
            "type": "conversation",
            "channel": "web_chat",
            "message": message,
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            WEBCHAT_URL, data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception as e:
        print(f"webchat post failed: {e}", file=sys.stderr)
        return False


async def main(post_on_green: bool = False):
    ts = datetime.now(timezone.utc).isoformat()
    report = {"ts": ts, "agent": "NEXUS", "checks": {}}
    all_findings = []

    # DB checks
    conn = await asyncpg.connect(resolve_db_dsn())
    try:
        db_result = await check_db(conn)
        report["checks"]["db"] = db_result
        all_findings.extend(db_result["findings"])
    finally:
        await conn.close()

    # Service checks
    svc_result = check_services()
    report["checks"]["services"] = svc_result
    all_findings.extend(svc_result["findings"])

    # Log checks
    log_result = check_logs()
    report["checks"]["logs"] = log_result
    all_findings.extend(log_result["findings"])

    # Classify overall status
    has_crit = any(f["sev"] == "CRIT" for f in all_findings)
    has_warn = any(f["sev"] == "WARN" for f in all_findings)
    status = "RED" if has_crit else ("YELLOW" if has_warn else "GREEN")
    report["status"] = status
    report["findings_count"] = {
        "CRIT": sum(1 for f in all_findings if f["sev"] == "CRIT"),
        "WARN": sum(1 for f in all_findings if f["sev"] == "WARN"),
    }
    report["findings"] = all_findings

    # Persist
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")

    # Print to stdout (journal)
    print(json.dumps(report, indent=2, ensure_ascii=False))

    # Post to webchat only if YELLOW/RED (or if post_on_green forced)
    if status in ("RED", "YELLOW") or post_on_green:
        summary_lines = [f"🌙 SEAL Nightly Audit — {ts[:10]} — Status: {status}"]
        for f in all_findings[:10]:
            emoji = "🔴" if f["sev"] == "CRIT" else "🟡"
            summary_lines.append(f"{emoji} [{f['sev']}] {f['msg']}")
        if len(all_findings) > 10:
            summary_lines.append(f"  ...{len(all_findings) - 10} más en {LOG_PATH.name}")
        summary_lines.append(f"DB {report['checks']['db']['metrics'].get('db_size_mb','?')}MB | errors 24h: {report['checks']['logs']['metrics'].get('error_lines_24h','?')} | bm25 missing: {report['checks']['db']['metrics'].get('bm25_missing_active','?')}")
        if not post_webchat("\n".join(summary_lines)):
            # El audit corrió pero su resumen NO llegó al webchat: blackout de
            # alertas. Lo dejamos GRITANDO (antes era un fallo mudo) — un fallback
            # al log persistente para que el hallazgo no se pierda.
            print("⚠️ ALERTING BLACKOUT: el resumen del nightly audit NO llegó al webchat (:8765 caído?). "
                  f"Resumen guardado en {LOG_PATH}.", file=sys.stderr)

    return report


if __name__ == "__main__":
    force_post = "--post-on-green" in sys.argv
    result = asyncio.run(main(post_on_green=force_post))
    sys.exit(0 if result["status"] != "RED" else 1)
