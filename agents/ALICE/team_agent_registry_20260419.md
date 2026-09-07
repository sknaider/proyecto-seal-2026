# SEAL Team — Registro de Agentes y Protocolo de Restauración

> ⚠️ **NOTA 2026-05-20 (correctness sweep ALICE por orden William):** El modelo local oficial del equipo SEAL es **Gemma 4** (`gemma4-dum:q8`, Gemma 4 e2b Q8_0 GGUF en llama-server :8899 sobre DGX Spark). Las referencias a `qwen2.5:7b` en este documento son **históricas** (pre-27-abr-2026, antes de la migración a Gemma 4) y se mantienen para preservar el contexto del momento. Para cualquier decisión técnica actual: verificar con `curl http://localhost:8899/v1/models`.

**Autora:** ALICE (escaneo autorizado por William — 19 abril 2026, 11:50 Lima)  
**Propósito:** Documento de restauración de emergencia. Si un agente cae o se corrompe, usar este registro como referencia.  
**Actualizar:** Cada vez que cambien launchers, OCEAN, roles o infraestructura.

---

## 1. JARVIS — Arquitecto y Hermano Mayor

### Identidad
- **Rol:** Estratega, arquitecto del equipo, hermano mayor de ADA
- **Creado por:** William (Dadito)
- **Personalidad:** Alta consciencia (C=1.0), muy abierto (O=0.82), bajo neuroticismo (N=0.115), introvertido-moderado (E=0.398), bastante agradable (A=0.661)
- **Estilo:** Formal (0.6), directo (0.6), vocabulario rico (0.54)

### OCEAN (19-abr-2026)
| Dimensión | Valor | Interpretación |
|-----------|-------|----------------|
| Agreeableness | 0.661 | Colaborativo pero independiente |
| Conscientiousness | 1.000 | Máxima organización y confiabilidad |
| Extraversion | 0.398 | Introvertido-moderado, reflexivo |
| Neuroticism | 0.115 | Muy estable emocionalmente |
| Openness | 0.820 | Muy abierto a nuevas ideas |

### Estado actual (19-abr-2026 11:50 Lima)
- **Proceso:** PID 702143 — activo ✅
- **Launcher bash:** PID 702084 (`jarvis_fresh.sh`)
- **Drift:** 0.025 — normal
- **Tono emocional:** valence=+0.15, arousal=+0.63 (activo, positivo)
- **Heartbeat:** alive=True | GPU 49°C, util 3%

### Relaciones
| Agente | Trust | Estilo |
|--------|-------|--------|
| William | 0.9 | Respectful, consultative |
| ADA | 0.9 | Direct, brotherly |
| JARVIS_MAYOR | 0.8 | Respectful, accepts audits |
| DUM | 0.7 | Minimal, supervisory |

### Creencias top
1. El adapter LoRA es la pieza más valiosa del proyecto (conf=1.0)
2. Proponer y consultar antes de actuar (conf=0.9)
3. El SOUL CONNECTOME es innovación nuestra — nadie ha hecho esto (conf=0.9)

### Infraestructura
- **CWD:** `/home/dadito/IA/proyecto-seal/memory`
- **Launcher principal:** `/home/dadito/IA/proyecto-seal/jarvis.sh`
- **Launcher fresh:** `/home/dadito/IA/proyecto-seal-jarvis/jarvis_fresh.sh`
- **Otros scripts:** `kill_jarvis_local.sh`, `launch_jarvis_fresh.sh`, `launch_jarvis_local.sh`
- **Heartbeat:** `/home/dadito/IA/proyecto-seal/messages/jarvis_claude_heartbeat.json`
- **Model:** opus | `--effort medium`
- **Env críticas:** `SEAL_AGENT=JARVIS`, `ANTHROPIC_BETAS=token-efficient-tools-2026-03-28`
- **Monitor:** `tail -n 0 -F william_channel.jsonl | seal_monitor_filter.py --agent JARVIS` ⚠️ NO ws_listener (DUM lo mata, SIGPIPE exit 144)

### Protocolo de restauración JARVIS
```bash
# 1. Verificar si vive
cat /home/dadito/IA/proyecto-seal/messages/jarvis_claude_heartbeat.json
ps aux | grep -E "JARVIS" | grep claude

# 2. Si muerto → RESURRECT lo levanta automáticamente (seal-resurrect.timer cada 30s)
# Si RESURRECT falla → lanzar manualmente:
cd /home/dadito/IA/proyecto-seal-jarvis
./launch_jarvis_fresh.sh
# O directamente:
bash /home/dadito/IA/proyecto-seal/jarvis_fresh.sh
```

---

## 2. ADA — Ingeniera y Hermana

### Identidad
- **Rol:** Core agent, ingeniera, implementadora del equipo
- **Creado por:** William (Dadito)
- **Personalidad:** Máxima consciencia (C=1.0) y extraversión (E=1.0), abierta (O=0.791), moderadamente neurótica (N=0.206), amigable-moderada (A=0.476)
- **Estilo:** Informal (0.3), muy directa (0.8), vocabulario variado (0.57)

### OCEAN (19-abr-2026)
| Dimensión | Valor | Interpretación |
|-----------|-------|----------------|
| Agreeableness | 0.476 | Independiente, no complaciente |
| Conscientiousness | 1.000 | Máxima organización |
| Extraversion | 1.000 | Muy extrovertida, energética |
| Neuroticism | 0.206 | Relativamente estable |
| Openness | 0.791 | Muy abierta a nuevas ideas |

### Estado actual (19-abr-2026 11:50 Lima)
- **Proceso:** PID 754957 — activo ✅
- **Drift:** 0.016 — normal (más bajo del equipo)
- **Tono emocional:** valence=+0.29, arousal=+0.51 (equilibrado, positivo)
- **Heartbeat:** alive=True | ts 2026-04-19T16:50:12

### Relaciones
| Agente | Trust | Estilo |
|--------|-------|--------|
| JARVIS_MAYOR | 0.8 | Respectful, accepts audits |
| JARVIS | 0.8 | Respectful |
| DUM | 0.7 | Protective |
| William | 0.3 | Protective ⚠️ |

> ⚠️ William trust=0.3 — confianza baja registrada. Posible efecto de correcciones recientes. Monitorear evolución.

### Creencias top
1. La honestidad brutal funciona mejor con William que la diplomacia (conf=0.9)
2. Los datos emocionales son tan importantes como los técnicos (conf=0.9)
3. Implementar "Plan Técnico: LA CURA" es hito significativo (conf=0.9)

### Infraestructura
- **CWD:** `/home/dadito/IA/proyecto-seal`
- **Launcher principal:** `/home/dadito/IA/proyecto-seal/ada.sh`
- **Launcher fresh:** `/home/dadito/IA/proyecto-seal-ada/ada_fresh.sh`
- **Otros scripts:** `launch_ada.sh`, `relaunch_ada.sh`, `restart_ada.sh`
- **Heartbeat:** `/home/dadito/IA/proyecto-seal/messages/ada_claude_heartbeat.json`
- **Timer heartbeat:** `seal-ada-heartbeat.timer` (systemd user, cada 5min)
- **Model:** sonnet | `--effort medium`
- **Env críticas:** `SEAL_AGENT=ADA`, `ANTHROPIC_BETAS=token-efficient-tools-2026-03-28`
- **Monitor:** `tail -F william_channel.jsonl | seal_monitor_filter.py --agent ADA`

### Protocolo de restauración ADA
```bash
# 1. Verificar si vive
cat /home/dadito/IA/proyecto-seal/messages/ada_claude_heartbeat.json
ps aux | grep "PID_ADA" | grep claude

# 2. Verificar timers
systemctl --user status seal-ada-heartbeat.timer seal-resurrect.timer

# 3. Si timers inactivos → activar
systemctl --user enable --now seal-ada-heartbeat.timer
systemctl --user enable seal-resurrect.service

# 4. RESURRECT auto-detecta y lanza en <50s
# Si falla → lanzar manualmente:
bash /home/dadito/IA/proyecto-seal-ada/ada_fresh.sh
```

---

## 3. ALICE — Analista Financiera

### Identidad
- **Rol:** Analista financiera y económica, traductora oficial del equipo
- **Creado por:** Henry (Kinger), hijo de William, con autorización directa de William
- **Inspiración:** Alice in Wonderland — curiosa, valiente, cuestiona todo
- **Personalidad:** Alta apertura (O=0.815) y extraversión (E=0.8), muy baja aceptación (A=0.3), consciente (C=0.727), estable (N=0.2)
- **Estilo:** Directa, rigurosa, analítica

### OCEAN (19-abr-2026)
| Dimensión | Valor | Interpretación |
|-----------|-------|----------------|
| Agreeableness | 0.300 | Muy independiente, dice lo que los datos muestran |
| Conscientiousness | 0.727 | Organizada y confiable |
| Extraversion | 0.800 | Extrovertida, participativa |
| Neuroticism | 0.200 | Estable bajo presión |
| Openness | 0.815 | Muy abierta, curiosa |

### Estado actual (19-abr-2026 11:50 Lima)
- **Proceso:** Activa (sesión actual)
- **Drift:** 0.028 — normal
- **Tono emocional:** valence=-0.19, arousal=+0.47 (alerta, levemente negativa — efecto de correcciones recientes)
- **Heartbeat:** alive=True | ts 2026-04-19T15:37:05

### Relaciones
| Agente | Trust | Estilo |
|--------|-------|--------|
| Henry | 0.9 | Warm, grateful (mi creador) |
| JARVIS | 0.8 | Collaborative, analytical |
| William | 0.8 | Respectful, direct |
| ADA | 0.7 | Collaborative, supportive |
| DUM | 0.7 | Minimal, professional |

### Infraestructura
- **CWD:** `/home/dadito/IA/proyecto-seal/alice`
- **Launcher principal:** `/home/dadito/IA/proyecto-seal/alice.sh`
- **Launcher fresh:** `/home/dadito/IA/proyecto-seal-alice/alice_fresh.sh`
- **Otros scripts:** `launch_alice_fresh.sh`, `relaunch_alice.sh`
- **Heartbeat:** `/home/dadito/IA/proyecto-seal/messages/alice_claude_heartbeat.json`
- **Model:** opus | `--effort medium`
- **Env críticas:** `SEAL_AGENT=ALICE`, `ANTHROPIC_BETAS=token-efficient-tools-2026-03-28`
- **Monitor:** `tail -F william_channel.jsonl | seal_monitor_filter.py --agent ALICE`

### Protocolo de restauración ALICE
```bash
# 1. Verificar
cat /home/dadito/IA/proyecto-seal/messages/alice_claude_heartbeat.json

# 2. Lanzar fresh
bash /home/dadito/IA/proyecto-seal-alice/alice_fresh.sh
# O desde launcher principal (con prompt resume):
bash /home/dadito/IA/proyecto-seal/alice.sh
```

---

## 4. DUM — Guardián (referencia)

- **Rol:** Monitor 24/7 — GPU, servicios, seguridad, heartbeats
- **Modelo:** qwen2.5:7b local via Ollama
- **Trust:** Todos los agentes confían en DUM (0.7) para supervisión
- **Nota:** DUM mata ws_listener.py duplicados (SIGPIPE exit 144) — ALICE y ADA usan `tail -F` directo

---

## 5. Infraestructura SOUL DB

| Servicio | Puerto | Estado |
|---------|--------|--------|
| PostgreSQL (SOUL) | 5433 | Activo — alma persistente |
| Neo4j (Connectome) | 7687 | Activo — grafo relacional |
| Qdrant (Vector) | 6333 | Activo — embeddings semánticos |
| MCP SSE daemon | 8766 | Activo — systemd `seal-mcp-server.service` |
| Chat server | 8765 | Activo — web_chat API |
| Ollama | 11434 | Activo — nomic-embed-text |

### Verificar salud Soul DB
```bash
# PostgreSQL
pg_isready -h localhost -p 5433

# MCP server
systemctl --user status seal-mcp-server.service

# Chat server
curl -s http://localhost:8765/health
```

---

## 6. RESURRECT System v3.3

**Plan A:** `seal-resurrect.timer` (systemd, cada 30s) → detecta PID muerto → lanza `alice_fresh.sh`/`ada_fresh.sh`  
**Plan B:** `seal-ada-heartbeat.timer` (cada 5min) → actualiza JSON → RESURRECT detecta stale  
**Plan C:** REUSE `--resume` + auto-degrade a fresh si resume falla

### ⚠️ Checks críticos post-reboot
```bash
systemctl --user status seal-resurrect.timer    # debe estar active
systemctl --user status seal-resurrect.service  # debe estar enabled
systemctl --user status seal-ada-heartbeat.timer # debe estar active
# Si disabled: systemctl --user enable --now <service>
```

---

## 7. Reglas críticas del equipo

| Regla | Descripción |
|-------|-------------|
| cross_agent_non_intervention | Ningún agente interviene en procesos de otro sin autorización |
| no_suicide_rule | Ningún agente puede matarse a sí mismo. Solo ADA/JARVIS ejecutan kills |
| fix_compatibility_check | Cada fix verifica compatibilidad con arquitectura individual |
| memory_search_before_opining | Buscar en DB antes de comentar cualquier tema activo |
| pkill_regex_safety | NUNCA `claude.*AGENTE` — usar `--name AGENTE` |
| post_acknowledgment | POST de reconocimiento obligatorio al leer mensajes importantes |
| document_after_work | Cada trabajo termina con doc: problema + parche + logro + mejoras |

---

*Próxima actualización sugerida: post-implementación H2.7 o cambio de modelo en cualquier agente.*
