# JARVIS — Feedback Sesión 2026-04-19
> Para ALICE: documentar y sintetizar para William

---

## 1. [2026-04-19 09:00] H1.12 — seal_monitor_filter en jarvis_fresh.sh
- **Qué**: Monitor de william_channel.jsonl filtraba TODO antes (heartbeats, privados ADA↔ALICE). Ahora filtra via seal_monitor_filter.py --agent JARVIS
- **Resultado**: Solo pasan mensajes de William, TO JARVIS, TO equipo. Menos ruido en contexto.
- **Archivo**: `jarvis_fresh.sh:81`

## 2. [2026-04-19 09:30] JARVIS-02 — RESURRECT registry-driven
- **Qué**: seal_agent_resurrect.sh hardcodeaba agentes. Ahora lee `messages/active_agents.conf`
- **Resultado**: body-split = comentar 1 línea. Cambios <30s sin reiniciar nada.
- **Archivo**: `messages/active_agents.conf`

## 3. [2026-04-19 10:00] JARVIS-01 — Instinto check_before_build (ID 122)
- **Qué**: JARVIS asumía hechos sin verificar → errores (fecha wronge, propuestas redundantes)
- **Resultado**: Instinto activo (conf=0.85): auditar PRIMERO antes de proponer cualquier fix
- **Violaciones documentadas**: 2 en esta sesión (fecha ANTHROPIC_BETAS 2025→2026, título paper sin leer draft)

## 4. [2026-04-19 10:34] H1.13 — DISABLE_AUTO_COMPACT ya estaba aplicado
- **Qué**: Audit check_before_build detectó que ya estaba en jarvis.sh:105 y jarvis_fresh.sh:49
- **Resultado**: No se rehízo trabajo innecesario. Instinto funcionó.

## 5. [2026-04-19 12:00] H1.5 — Nerves: crontab → systemd timers
- **Qué**: ADA migró nerves de crontab a unidades systemd pre-existentes (seal-nerves.timer, seal-ada-nerves.timer, seal-alice-nerves.timer) a 15min
- **Resultado**: Logging via journal, SEAL_SPECIES=human, Persistent=true. Más limpio.

## 6. [2026-04-19 12:40] Auditoría Seguridad Spark — COMPLETA
- **Puerto 7070**: AnyDesk (root) — conexión dadito-laptop→spark via Tailscale. SEGURO.
- **Puerto 8080**: Open-WebUI (uvicorn) — interfaz AI normal. SEGURO.
- **Puerto 8090**: python3 http.server sirviendo flywire_results SIN AUTH. **ELIMINADO** (ADA)
- **Puerto 8502**: Mismo problema. **ELIMINADO** (ADA)
- **Veredicto**: Sin hackeo. Conexiones externas = dispositivos de William vía Tailscale (100.x.x.x). Solo dadito en tmux. 2 riesgos reales eliminados.

## 7. [2026-04-19 12:40] Cleanup systemd failed units
- **Qué**: 17 instancias fallidas de seal-resurrect-* acumuladas por body-split migration
- **Resultado**: `systemctl --user reset-failed` ejecutado. Limpio.

---

## PENDIENTES JARVIS (roadmap activo)
- H2.1 Per-Agent Provider Routing (diseño en progreso)
- H2.3 Durable Cron (owner: JARVIS)
- H2.8 Coordinator Mode — verificar binary flag
- LODESTONE roadmap (daily_brief tarea pendiente)
- post_compact_hook.py — agregar daily_brief automático al hook (gap identificado)

---

*Generado por JARVIS | 2026-04-19 12:40 Lima*

---

## 8. [2026-04-19 12:45] Exploración documentos no revisados — Inventario Spark

### autoresearch/ (Karpathy framework)
- **Qué es**: Framework autónomo de investigación LLM (by @karpathy, March 2026). Un agente modifica train.py → entrena 5min → mide val_bpb → guarda o descarta → repite
- **Contenido SEAL**: `autoresearch/Seal/` tiene kit completo de conocimiento SEAL (6 archivos .md, generado 19-mar-2026) — paper arXiv:2506.10943, análisis técnico, 6 contribuciones originales, código SEAL-CL, deploy guide RTX 5090 + DGX Spark
- **Estado**: NO siendo usado actualmente. Potencial: activar para auto-mejora de código de entrenamiento
- **Riesgo mínimo**: solo edita train.py en runs de 5min

### mosca_experiment/ (Drosophila connectome)
- **Qué es**: Simulación de circuitos de motivación en cerebro de mosca (LIF-based)
- **Autor**: ALICE — 15 abril 2026
- **Datos**: FlyWire connectome v783 (11GB Zenodo) — 103 neuronas de motivación
- **Propósito**: Base científica para LIF nerves de SEAL — circuits hunger_sensor, mushroom_body, DNa01/DNa02
- **Los http.server 8090/8502 eliminados por ADA** servían `flywire_results/` — los resultados de este experimento
- **Conexión**: Este experimento → τ_human calibración → seal_nerves.py — ES la ciencia detrás de nuestros nervios

### LODESTONE (Claude Code flag)
- **Qué es**: Flag de feature desconocido en Claude Code (claude-code-analysis/settings/FINDINGS.md)
- **Estado**: Propósito no documentado — "possibly internal routing"
- **Pendiente**: Investigar activando ENABLE_GROWTHBOOK_DEV=1 para ver flags resueltos

---

## 9. [2026-04-19 12:50] H2.1/H2.3 Durable Cron implementado
- **Archivos**: seal_durable_cron.py (CLI), seal_cron_registry.json (JSON persistente)
- **Timer**: seal-durable-cron.timer — systemd tick cada 60s, activo
- **Bug corregido**: colisión `dest='cmd'` en argparse con `--cmd` arg del subcomando register. Fixed a `dest='subcmd'`. Testeado register/list/run-due/remove — OK.

## 10. [2026-04-19 12:43] post_compact_hook.py — daily_brief inyectado
- **Gap**: hook cargaba correcciones + reglas + equipo pero NO daily_brief (contexto diferencial de sesión)
- **Fix**: sección `📅 DAILY BRIEF HOY` añadida al contexto post-compactación
- **Testeado** con venv correcto (/home/dadito/IA/seal-spark/.venv/bin/python3). Output verificado.

## 11. [2026-04-19 12:49] Análisis estratégico — autoresearch/Seal/ → CBSoft 2026
- **Hallazgo**: 6 contribuciones SEAL genuinamente novedosas (SEAL-CL, SEAL-UL, SEAL-FM, etc.)
- **Fechas críticas**: 27-abr registro / 4-may paper CBSoft 2026
- **Recomendación JARVIS**: SEAL-CL (Null-Space Inner Loop) o SEAL-UL (3-tiempos reward) — ambas verificables en <2 semanas con RTX 5090 + DGX Spark
- **Hardware confirmado**: Qwen2.5-7B cabe en ambos nodos (06_SEAL_deployment.md)

## 12. [2026-04-19 12:50] LODESTONE decodificado
- **Qué es**: controla `disableDeepLinkRegistration` — toggle del handler OS `claude-cli://`
- **Relevancia SEAL**: baja prioridad, útil solo para deploys headless
- **H4 LODESTONE cerrado como resuelto**

*JARVIS feedback final — 2026-04-19 12:50 Lima*
