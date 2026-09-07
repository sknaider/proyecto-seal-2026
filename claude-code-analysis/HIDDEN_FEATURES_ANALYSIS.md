# Hidden Features — OpenClaude Deep Dive
> 14 features ocultas encontradas por ADA, 5 abril 2026
> Ninguna estaba documentada en los 10 specs originales

## TOP 3 para SEAL (Valor 10/10)

### 1. Memory Extraction Agent (forked subagent automático)
**Archivo:** `services/extractMemories/extractMemories.ts`
- Después de cada respuesta, fork un subagent que comparte prompt cache
- Analiza mensajes recientes y extrae memorias automáticamente
- Mutex: si el agente principal ya escribió memoria, skip
- Throttle configurable, max 5 turns por extracción
- Permisos restringidos: solo Read/Grep/Glob + Edit en directorio de memoria
- **Para SEAL:** Reemplaza memory_store manual. Captura lo que los agentes olvidan guardar.

### 2. Coordinator Mode (multi-agente nativo)
**Archivo:** `coordinator/coordinatorMode.ts`
- `CLAUDE_CODE_COORDINATOR_MODE=1` transforma al agente en orquestador puro
- Solo puede usar AgentTool, SendMessage, TaskStop (NO bash, NO edit)
- Workflow forzado: Research → Synthesis → Implementation → Verification
- Workers reportan via `<task-notification>` XML
- **Scratchpad directory** donde workers leen/escriben sin permisos
- **Para SEAL:** ES el patrón JARVIS(coordinator) → ADA(worker) nativo

### 3. Durable Cron (loops persistentes)
**Archivo:** `tools/ScheduleCronTool/prompt.ts`
- Dos modos: session-only (muere con el proceso) y **durable** (persiste a `.claude/scheduled_tasks.json`)
- Sobrevive restarts, auto-catch missed one-shots
- Jitter anti-thundering-herd
- Auto-expire después de N días
- **Para SEAL:** Elimina el problema de loops que mueren con la sesión

## Valor 8-9/10

### 4. Session Memory Compaction
**Archivo:** `services/compact/sessionMemoryCompact.ts`
- En vez de pedir al LLM que resuma (caro, lossy), mantiene un Session Memory .md
- Al compactar, reemplaza mensajes viejos con el contenido del session memory + N mensajes recientes
- Preserva pares tool_use/tool_result y thinking blocks

### 5. AutoDream
**Archivo:** `services/autoDream/autoDream.ts`
- Fires después de 24h Y 5+ sesiones acumuladas
- Fork subagent que: scan transcripts → merge signal → convert relative dates → delete contradicted facts → prune MEMORY.md
- File lock para prevenir consolidación concurrente

### 6. Secret Scanner (30+ regex)
**Archivo:** `services/teamMemorySync/secretScanner.ts`
- Gitleaks rules: AWS keys, GitHub PATs, Anthropic API keys, OpenAI, Stripe, private keys
- Secretos NUNCA salen de la máquina
- **Para SEAL:** Directamente reusable para data sovereignty checks

### 7. Swarm Permission Sync
**Archivo:** `utils/swarm/permissionSync.ts`
- Workers escriben requests a `permissions/pending/`
- Leader aprueba/deniega, resolución va a `resolved/`
- File locking, auto-cleanup 1h, failure detection

### 8. Microcompact 3-tier
**Archivo:** `services/compact/microCompact.ts`
- Tier 1: cache_edits API (borra de cache servidor sin invalidar prefijo)
- Tier 2: time-based clear (cache ya expiró)
- Tier 3: full compact (LLM o Session Memory)

## Valor 5-7/10

### 9. Magic Docs (auto-updating documentation)
**Archivo:** `services/MagicDocs/magicDocs.ts`
- Archivos con `# MAGIC DOC: [titulo]` se auto-actualizan
- Background subagent con solo Edit tool

### 10. LSP Passive Feedback
**Archivo:** `services/lsp/passiveFeedback.ts`
- Recibe diagnostics de LSP servers automáticamente
- Inyecta errores/warnings en la conversación sin que el usuario pregunte

### 11. Sleep Tool con Tick System
- Periodic check-ins durante wait states
- No ocupa shell process

## Valor 3-5/10

### 12. Companion System (Pickle the capybara)
- 18 especies, 5 raridades, gacha determinístico por hash de userId
- Bones regenerados from hash (no persistidos, anti-cheat)

### 13. Model Migration Chain
- Fennec = codename de Opus 4.6
- Migraciones: fennec→opus, sonnet-4-5→sonnet-4-6

### 14. Vim Mode completo
- Motions, operators, text objects, state machine
- 1000+ líneas de implementación
