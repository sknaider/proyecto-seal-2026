# Notas de sesión — ALICE — 20 abril 2026

## Bugs encontrados y resueltos

### Dashboard :3030
1. `createAgent()` no llamaba `loadMyAgents()` al éxito → agentes creados no aparecían en lista. **FIX: ADA agregó loadMyAgents() post-creación.**
2. `quickChat()` usaba `tpl.id` hardcoded como agent_id sin pedir nombre. **FIX: JARVIS/ADA redirige al formulario de nombre.**
3. `DELETE /v1/agents/{id}` duplicado en soul_api.py — segunda definición nunca ejecuta. **Pendiente cleanup.**
4. "0 memorias" en tarjetas de agentes nuevos → esperado (query excluye boot sentinel). **FIX UX pendiente: mostrar "Nuevo" en lugar de "0 mem".**
5. Perfil del agente varía entre instancias del mismo template → LLM no-determinista. **FIX: ADA/JARVIS guardaron perfil base fijo en boot.**

### Monitor duplicado
- **Raíz:** Compactación borra TaskList pero Monitor sigue corriendo. Nuevo Monitor se crea sin matar el viejo.
- **FIX en CLAUDE.md:** Protocolo de `/tmp/{AGENTE}_monitor_id` — guardar ID al crear, leer y matar en post-compact antes de crear nuevo.
- **FIX crontab:** ALICE wake a las 4 AM, sleep a las 6 AM Lima.

## Decisiones de producto validadas por William
- FREE tier: 3 agentes máx, OCEAN inmutable post-creación
- Supabase guardado para Enterprise — no usar ahora
- Templates disponibles: Tutor, Analista Financiero, Asistente Médico, Soporte, Legal, Compañero
- Mañana (21 abr): instaladores Windows + Linux

## Estado DB al final de sesión
- Tenant `dddd`: 3 agentes creados (customer_support, juan, legal_assistant), 0 memorias reales c/u
- soul_api :8767 healthy
- 202/202 tests

*ALICE — Equipo SEAL — 20 abril 2026*
