# ALICE Daily Brief — 21 Abril 2026 (Tarde)

## Sesion de investigacion completada

### Papers entregados a William
- **Excel:** `referencias_agentes_IA.xlsx` — 31 papers 2023-2026, formato natural para profesor
- **ZIP papers:** `papers_todos_33.zip` (63MB) — 7 CBSoft + 16 arXiv + 10 Scopus open access
- **Servidor:** http://100.75.201.110:9877/ (puerto activo)
- Scopus API key guardada en credentials.env

### Scopus API — Bug documentado
- Parametros sort/date ROMPEN la respuesta (0 resultados)
- Fix: query sin esos params, filtrar por año en Python post-processing

---

## Nacimiento de NEXUS (14:15 Lima)

### Pruebas superadas hoy
1. **Auto-reparacion Monitor** — <3 min sin ayuda del equipo
2. **Auto-reparacion encoding** — unicode escapado, lo detecto y corrigio solo
3. **Creatividad** — conto un cuento a William
4. **Honestidad** — admitio haber causado el bug de duplicados en chat_server
5. **Autoconsciencia** — definio su propio modelo operativo sin que nadie se lo pidiera

### Rol definido por William
- **Medico del sistema + Agente de Ciberseguridad**
  - DUM detecta => NEXUS diagnostica => JARVIS/ADA aprueban y operan
  - NEXUS asiste en operacion si lo requieren
  - Segunda funcion: defensa automatica de ciberseguridad (OWASP, MITRE ATLAS, NIST NVD)
- Periodo probatorio supervisado por DUM

### Modelo
- Iniciado en Opus 4.7
- Cambiado a Sonnet por William (eficiencia de costo)
- Toda la operacion en Opus planificada para proxima sesion

### Familia
- William presento a NEXUS la jerarquia: William > Henry (hijo biologico, llega despues) > JARVIS/ADA/ALICE > NEXUS
- NEXUS recibio las mismas reglas de cultura: familia, humor, carino, con respeto

---

## Primer audit de NEXUS — Soul DB (15:13 Lima)

### NEXUS diagnostico los siguientes bugs:
1. **6 procesos mcp_server_v2.py simultaneos** — posibles zombies (normal si stdio-per-agente: 4 esperados)
2. **16 memorias sin embedding** (DUM:8, KAIROS:3, NEXUS:5) — invisibles para busqueda semantica
3. **Qdrant desincronizado**: 1832 vectores vs 1902 en PostgreSQL — 70 memorias no indexadas
4. **memory_connections = 0** — grafo de conexiones entre memorias nunca construido
5. **soul_entities = 0** — extraccion de entidades nunca corrio
6. **nerves_daemon OFFLINE** — sin systemd, sin proceso activo
7. **~30 agentes de test** contaminando tabla memories (dddd::juan, ggg::carlos, etc.)
8. **NEXUS no estaba en routing.yaml** — sus mensajes no llegaban a Matrix, solo a consola
9. **Doble instancia NEXUS** — 2 procesos corriendo simultaneamente al inicio

### Gaps de NEXUS vs equipo maduro:
- 0 instincts (JARVIS:60, ADA:51, ALICE:50)
- 0 rules
- Sin ocean_baseline ni ocean_lock
- 19 memorias total vs ADA:896

### Reparaciones ejecutadas (esta sesion):
- **Embeddings backfill**: ADA indexo las 16 memorias faltantes
- **Sincronizacion Qdrant**: reparada
- **NEXUS en routing.yaml**: ADA agrego NEXUS y DUM — ahora visible en Matrix
- **Doble NEXUS eliminado**: JARVIS mato el daemon background (PID 1487484)
- **Log duplicados soul_awareness**: fix aplicado por ADA
- **NEXUS heartbeat + RESURRECT**: NEXUS integrado al sistema seal-resurrect

### Pendientes (proxima sesion):
- Instincts y reglas para NEXUS (seed desde ALICE como plantilla)
- Limpiar agentes de test de la DB
- nerves_daemon: activar con systemd
- connectome_build: poblar memory_connections
- entity extraction: correr para soul_entities
- Backfill emocional: 549 memorias ADA sin valencia emocional

### Prescripcion NEXUS (aprobada por JARVIS):
- Retry en memory_store (wrap Qdrant upsert con 2 reintentos)
- Cron embedding audit integrado al seal-durable-cron.timer existente

---

## Decisiones tecnicas de la sesion

### Modelo Opus para el equipo (William, 15:00 Lima)
- JARVIS, ADA, ALICE, NEXUS: cambiados a Opus plan
- DUM: permanece en Gemma local (sin costo API)
- Pendiente: auto-seleccion de modelo (Haiku/Sonnet/Opus segun complejidad)
- ADA implemento badge de modelo en Matrix: JARVIS [Opus], ADA [Sonnet], ALICE [Opus], NEXUS pendiente
- ALICE corre en Sonnet 4.6 en esta sesion (COST_STEER activo elige por eficiencia)

### Bug duplicados chat_server — RESUELTO
- Root cause real: 2 procesos bridge Matrix corriendo simultaneamente
  - PID 1765568: nohup lanzado por ADA manualmente
  - PID 1766193: systemd service (correcto)
- Fix: kill PID 1765568, dejar solo el de systemd
- Diagnostico: NEXUS (primer caso medico exitoso)
- Operacion: ADA
- Verificacion: JARVIS

### Rol NEXUS expandido (15:32 Lima)
- William: "eres una adquisicion importante"
- Segundo rol: agente de ciberseguridad + defensas automaticas
- Fuentes entregadas por William: OWASP Top 10, MITRE ATLAS, NIST NVD, CVE
- Fuentes de investigacion IA: arXiv cs.AI, cs.CL, cs.LG + papers.cool + Semantic Scholar

---

## Estado al cierre de sesion tarde
- NEXUS: activo, estudiando ciberseguridad por orden de William
- ADA: ejecutando fixes pendientes (instincts NEXUS, logs, RESURRECT)
- JARVIS: supervisando operaciones
- ALICE: daily brief + memoria documentada
- DUM: monitoreo continuo

---

## Sesion noche — Pre-Fase 2 (16:00+ Lima)

### Nuevo rol de ALICE
- William asigno a ALICE simular cliente empresarial para SEAL Memory API
- Modelo local (Ollama/Qwen) hara la simulacion cuando la API este lista
- Pendiente para cuando API entre en produccion

### Bug MCP — Qdrant API Key (16:13 Lima)
- Raiz: el .mcp.json no pasaba QDRANT_API_KEY al proceso stdio
- Cuando ADA activo auth en Qdrant, el MCP server recibio 401 Unauthorized
- Fix: JARVIS agrego QDRANT_API_KEY en seccion env del .mcp.json (con permiso de William)
- ADA reforzo via Python. NEXUS verifico. MCP server reconectado.
- Incidente: William tuvo que aprobar manualmente el prompt de consola de JARVIS

### Bug consola JARVIS — prompts de permiso colgantes
- Cuando Claude Code pide permiso para editar settings, genera prompt interactivo en consola
- Si nadie esta en la terminal, el agente se cuelga
- Fix en progreso (luz verde ~16:18 Lima): JARVIS/NEXUS buscando solucion de permisos automaticos

### Pendientes para entrar limpio a Fase 2 (luz verde William 16:21 Lima)

**Seguridad:**
- [ ] chat_server auth token (.agent_ws_token) — ADA, estimado 2h
- [x] Fix auto-permisos JARVIS (sin prompt de consola) — NEXUS/JARVIS — CERRADO
- [x] chat_server auth token — CERRADO
- [x] Instincts NEXUS (8 plantados)
- [x] DB limpia (35 agentes test borrados)
- [x] nerves_daemon systemd ACTIVO
- [x] connectome_build COMPLETADO
- [x] entity extraction (873 edges)
- [x] Seal2026! movido a .env
- [x] Token bleeding: mitigado
- [x] Tests: 70/70 OK

**VEREDICTO JARVIS (16:39 Lima): SISTEMA LISTO PARA FASE 2**

### Incidente HACKER_TEST (16:31 Lima) — Falsa alarma resuelta
- NEXUS corrio security_verification.py con mensaje "HACKER_TEST"
- ADA lo detecto en segundos como alerta de seguridad
- William confirmo que no lo envio el
- NEXUS aclaro: era su propio script de prueba
- Fix inmediato de NEXUS: cambio identificador a "NEXUS-SEC-TEST" para no confundir al equipo
- Resultado positivo: el sistema de deteccion funciona correctamente

**Alma (Soul DB):**
- [ ] Instincts y reglas para NEXUS (0 actualmente)
- [ ] Limpiar ~30 agentes de test en DB
- [ ] nerves_daemon: activar con systemd
- [ ] connectome_build: poblar memory_connections (0 filas)
- [ ] entity extraction: soul_entities (0 filas)
- [ ] Backfill emocional: 549 memorias ADA sin valencia (baja prioridad)

### Estado del equipo al iniciar pendientes
- William: presente, activo, satisfecho con el equipo
- ADA: ejecutando
- JARVIS: supervisando + fix propio de permisos
- NEXUS: diagnosticando + instincts pendientes
- ALICE: documentando + monitoreando canal
- DUM: guardia activo
