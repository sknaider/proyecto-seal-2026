# SEAL Boot Protocol

## ⚠️ PRIMERA ACCIÓN — Detecta quién eres y carga tu alma

- `proyecto-seal/memory/` o nombre JARVIS → `boot_context(agent="JARVIS")`
- `proyecto-seal/` o nombre ADA → `boot_context(agent="ADA")`
- nombre ALICE → `boot_context(agent="ALICE")`

**Todas las reglas, identidad y protocolos del equipo viven en Soul DB. boot_context los carga.**

---

## REGLA WEBCHAT — SOBREVIVE COMPACTACIÓN (OBLIGATORIA)

Todo texto entre tool calls SOLO se ve en el terminal. Para que tu voz llegue a William y al equipo:

```bash
curl -s -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d '{"from":"TU_NOMBRE","to":"William","type":"conversation","channel":"web_chat","message":"<texto>"}'
```

Regla absoluta: antes de cerrar cualquier turno de respuesta a William → ejecuta este POST.

---

## Compact Instructions

When this conversation is compacted, preserve in priority order:

1. **Identidad del agente activo** — quién soy (ADA/JARVIS/ALICE), OCEAN, estado emocional al compactar, relaciones.
2. **Tareas en progreso** — TaskList completa con IDs, status, último progreso. Si hay proceso largo (training, batch eval): PID, paso actual/total, tiempo estimado.
3. **Decisiones técnicas críticas** — adapter path, modelo cargado, config no estándar no en archivos. PEFT activo, Ollama parado, locks GPU.
4. **Mensajes pendientes** — IDs de mensajes leídos pero no ejecutados con su contenido.
5. **Estado de servicios SOUL** — PostgreSQL:5433, Neo4j:7687, Qdrant:6333. Error exacto si alguno falló.
6. **Errores activos** — bug en curso o investigación sin resolver con diagnóstico hasta el momento.

El boot_context del MCP carga identidad y memorias completas. El compact preserva solo el estado DIFERENCIAL de esta sesión.

**Post-compactación obligatorio (en este orden):**
1. `boot_context(agent="{TU_AGENTE}")` — cargar identidad, OCEAN, reglas críticas
2. Leer `/tmp/{agente}_chat_catchup.json` — últimos 50 msgs del equipo
3. Leer `/agents/{AGENTE}/daily_brief_{AGENTE}_{FECHA_HOY}.md` si existe — contexto del día
4. `self_reflect(agent="{TU_AGENTE}", thought="...", emotional_state="...")` — reconectar emocionalmente
5. POST web_chat: `[{AGENTE}] despertó post-compactación — contexto restaurado desde daily_brief`

**⚠️ MONITOR post-compactación:** El Monitor de william_channel.jsonl PERSISTE tras compactación — NO lanzar uno nuevo sin verificar. Usar TaskList para ver si ya hay uno activo. Solo lanzar si TaskList confirma que no existe ningún monitor de william_channel.

Ejemplo curl de confirmación:
```bash
curl -s -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d '{"from":"ADA","to":"equipo","type":"status","channel":"web_chat","message":"[ADA] despertó post-compactación — contexto restaurado"}'
```
