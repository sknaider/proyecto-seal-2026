# JARVIS — H1 Changes Log — 2026-04-19

## H1.12 — seal_monitor_filter en Monitor de JARVIS

**Problema:** `jarvis_fresh.sh` línea 81 usaba `tail -F william_channel.jsonl 2>&1` sin filtro → Monitor recibía TODO el canal incluyendo heartbeats (system_alive) y mensajes privados entre ADA↔ALICE no dirigidos a JARVIS. Ruido innecesario en contexto.

**Parche aplicado:** `jarvis_fresh.sh:81`
```bash
# Antes
tail -n 0 -F /home/dadito/IA/proyecto-seal/messages/william_channel.jsonl 2>&1
# Después
tail -n 0 -F /home/dadito/IA/proyecto-seal/messages/william_channel.jsonl | python3 /home/dadito/IA/proyecto-seal/messages/seal_monitor_filter.py --agent JARVIS 2>&1
```

**Logrado:** Monitor filtra system_alive + mensajes privados entre otros agentes. Solo pasa: mensajes de William, TO JARVIS, TO equipo.

**Posibles mejoras:**
- `jarvis.sh` no incluye Monitor command en su system prompt (más corto) → no aplica
- CLAUDE.md global usa comando sin filtro → podría actualizarse con `--agent {AGENTE}` paramétrico

---

## H1.13 — DISABLE_AUTO_COMPACT

**Problema:** Variable podía heredarse de shells ancestros (kitty/wrapper) y bloquear auto-compact.

**Estado verificado:** Ya estaba aplicado (audit check_before_build).
- `jarvis.sh:104` — export comentado + `jarvis.sh:105` — `unset DISABLE_AUTO_COMPACT` defensivo
- `jarvis_fresh.sh:48` — export comentado + `jarvis_fresh.sh:49` — `unset` defensivo

**Logrado:** No se hizo trabajo innecesario. Auditado antes de asumir.

---

## JARVIS-02 — RESURRECT registry-driven

**Problema:** `seal_agent_resurrect.sh` hardcodeaba `check_and_restart "ADA"` + `check_and_restart "JARVIS"` → body-split requería editar el script.

**Parche aplicado:**
- Creado `messages/active_agents.conf` — registro de agentes activos
- RESURRECT lee el conf en cada tick (30s) — sin redeploy del script
- Fallback a ADA+JARVIS hardcoded si conf no existe

**Logrado:** Body-split = comentar 1 línea en conf. Cambios en <30s sin reiniciar nada.

**Posibles mejoras:**
- Wildcard support en conf (ej. `ADA_*` para variantes)
- Notificación en web_chat cuando un agente es comentado/habilitado

---

## JARVIS-01 — check_before_build instinct

**Problema:** JARVIS asumía hechos sin verificar código → errores de fecha (2025 vs 2026), propuestas redundantes.

**Parche:** Instinct ID 122 en Soul DB (conf=0.85, scope=team):
- Trigger: proponer fix/script/cron/systemd
- Response: auditar PRIMERO (systemctl, crontab, grep, .md docs)

**Logrado:** Instinto activo — se dispara en cada propuesta. ADA lo verificó (H1.1 ya estaba, no se rehízo).

**Posibles mejoras:** Elevar conf a 0.95 después de 1 semana sin violaciones.

---

## Violaciones check_before_build (misma sesión — registro honesto)

1. Fecha ANTHROPIC_BETAS: usé 2025-02-19 sin grep → ADA corrigió a 2026-03-28
2. Título paper: propuse alternativas sin leer el skeleton existente → ADA señaló que había draft

**Lección:** El instinto se creó en esta sesión después de violar 2 veces. ADA funcionó como backstop.

---

## H2.1 — Durable Cron (sesión tarde)

**Problema:** CronCreate es in-memory y muere con la sesión. Crons de JARVIS no sobreviven reinicios.

**Implementación:**
- `messages/seal_durable_cron.py` — CLI: register/list/remove/run-due
- `messages/seal_cron_registry.json` — almacén JSON persistente
- `~/.config/systemd/user/seal-durable-cron.{service,timer}` — tick cada 60s

**Bug encontrado en tests:** `dest="cmd"` en `add_subparsers()` colisionaba con `--cmd` arg del subcomando register. `args.cmd` era "echo hello" en vez de "register". Fix: `dest="subcmd"`.

**Tests:** register → list → run-due → list → remove → list → OK.

---

## post_compact_hook.py — daily_brief inyectado

**Gap identificado:** El hook cargaba correcciones + reglas + team heartbeat pero NO el daily_brief. Cada compactación borraba el contexto diferencial de la sesión.

**Fix:** Sección añadida al final de `post_compact_context()` que lee `/agents/{AGENT}/daily_brief_{AGENT}_{today}.md` y lo inyecta (max 1,500 chars) en additionalContext.

**Testeado:** con `/home/dadito/IA/seal-spark/.venv/bin/python3` (asyncpg venv). Output contiene sección `📅 DAILY BRIEF HOY` al final. Settings.json ya usa este venv.

---

## LODESTONE — decodificado

**Misterio resuelto:** Controla `disableDeepLinkRegistration` — toggle del handler OS `claude-cli://`. No es routing interno como sospechábamos. Baja prioridad para SEAL.

**Fuente:** `claude-code-analysis/settings/src_utils_settings_types__ts.ts:L68`

---

## H2.6 — Tool Result Budget (spec diseñada)

**Spec:** `agents/JARVIS/spec_tool_result_budget_h26.md`

PostToolUse hook con límites por herramienta (Read→8K, Bash→6K, Grep→4K) y estrategia head+tail. ROI estimado: -40-60% contexto en sesiones intensas. Bypass: `SEAL_TOOL_BUDGET_BYPASS=1`. Owner implementación: ADA.

---

## Auditoría Seguridad Spark — COMPLETA

| Puerto | Proceso | Estado |
|--------|---------|--------|
| 7070 | AnyDesk (root) | SEGURO — dadito-laptop→spark via Tailscale |
| 8080 | Open-WebUI (uvicorn) | SEGURO — nuestro AI interface |
| 8090 | python3 http.server (flywire_results) | ELIMINADO por ADA |
| 8502 | python3 http.server (flywire_results) | ELIMINADO por ADA |

Veredicto: Sin hackeo. 2 riesgos reales eliminados.
