# ADR-001 — Patrón Restart-Loop para Resurrección de Agentes

**Fecha:** 2026-04-18
**Autor:** ADA (Team SEAL)
**Revisor:** JARVIS — ✅ aprobado con 3 observaciones menores (22:53 Lima)
**Aprobador:** William — ✅ luz verde 22:56 Lima
**Estado:** **Aprobado y persistido en Soul DB** (`project/architecture/adr-001`)
**Implementado en:** commits `ccc3c68` → `8e7f22e` (2026-04-18)
**Revisión programada:** 2026-04-25 (con datos reales acumulados)

---

## 1. Contexto

Los 3 agentes de Team SEAL (ADA, JARVIS, ALICE) corren como procesos Claude CLI dentro de terminales kitty. Cuando un agente muere (OOM, kill manual, crash, fin de turno sin `/continue`), el usuario perdía la ventana del agente y la continuidad visual. Las versiones v3.0 → v3.3 del sistema RESURRECT intentaron varias estrategias que dejaron zombies o abrieron ventanas nuevas.

**Síntomas previos:**
- FALLBACK v3.3 (commit `d8acaae`) mataba kitties zombies pero abría ventana nueva → William pierde el contexto visual.
- Múltiples kitties zombies acumulados (head -1 sólo mataba uno; v3.3 mata TODOS pero el nuevo kitty aún era "ventana nueva").
- SIGTERM a kitty insuficiente — a veces quedaba proceso fantasma.

**Requerimiento literal de William (19:28-19:30 Lima, 2026-04-18):**
> "afinen hasta que regrese" + "tiene que resucitar en su misma ventana, no abrir otra, aplica para todos"

## 2. Decisión

Patrón **kitty(persist) → bash_loop(exec fresh.sh) → claude(muere/renace)**:

```
┌─ kitty (PID persistente, ventana única) ─────────────────────┐
│                                                               │
│  ┌─ bash (loop) ─────────────────────────────────────────┐  │
│  │  while true; do                                         │  │
│  │    exec /home/dadito/IA/proyecto-seal/<agente>_fresh.sh │  │
│  │  done                                                   │  │
│  │                                                         │  │
│  │  ┌─ claude CLI ─────────────────┐  ← muere/renace aquí │  │
│  │  │  agente (ADA/JARVIS/ALICE)    │                      │  │
│  │  └─────────────────────────────┘                      │  │
│  └───────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

**Invariantes:**
1. kitty NUNCA muere (sólo lo cierra el usuario manual).
2. bash loop NUNCA termina (el `exec` mantiene PID estable, sin spawn extra).
3. claude muere y renace en el mismo bash loop → misma ventana, mismo PID del kitty.
4. fresh.sh (ej. `ada_fresh.sh`) hace `claude -c <session> <auto-boot-prompt>` — al morir claude, el bash loop re-ejecuta y el agente vuelve.

## 3. Commits que implementaron este patrón

| Commit | Propósito |
|---|---|
| `ccc3c68` | Heartbeat scripts detectan muerte real (alive=false) |
| `e198961` | seal_restart.sh usa systemd-run+kitty (no tmux) |
| `0d184d2` | Positional prompt arg para auto-boot post-resurrección |
| `8032913` | v3.1: 4 bugs corregidos + cooldown 120s + keepalive |
| `aabf648` | v3.2: REUSE fallback + CronCreate en HOOK_MSG |
| `0572e9c` | FALLBACK cierra kitty zombie antes de abrir ventana nueva |
| `d8acaae` | Mata TODOS los kitties zombie (no sólo `head -1`) |
| `2dc25a8` | **`exec bash` → restart loop: misma ventana siempre** |
| `8e7f22e` | `kill -9` zombies (no SIGTERM — éste dejaba fantasmas) |

## 4. Alternativas consideradas y descartadas

| Alternativa | Por qué NO |
|---|---|
| **systemd --user unit** por agente | Pierde la ventana visible de kitty; William necesita ver el terminal activo. Además, systemd requiere root para algunos recursos que usa Claude CLI en modo dev. |
| **tmux/screen** | El watchdog de DUM mata procesos duplicados (ws_listener SIGPIPE). tmux introduce una capa extra de multiplexación que confunde al observador humano. |
| **Kitty restart sin bash loop** | kitty sin `--hold` sale al morir el comando hijo; con `--hold` deja ventana congelada. El bash loop con `exec` es la única forma de mantener PID de ventana estable mientras el claude interno renace. |
| **Fork nuevo kitty al morir claude** | Es lo que hacía FALLBACK v3.3 — viola requerimiento de William ("no abrir otra"). |
| **systemd --user unit + kitty child** (híbrido) | systemd-user puede supervisar un kitty child con Restart=on-failure, pero el ciclo de vida del kitty pasa a depender del dbus-user-session; si el login se interrumpe, la ventana desaparece. Además duplica la lógica de supervisión (systemd + bash loop interno), lo que complica debugging. Descartado por complejidad añadida sin ganancia frente al patrón actual. |

## 5. Consecuencias

### Positivas
- **Misma ventana siempre** — William no pierde contexto visual ni layout.
- **Zero zombies** — un solo kitty por agente, un solo bash loop.
- **Auto-boot** — fresh.sh inyecta el prompt de boot_context+Monitor+CronCreate, el agente despierta listo.
- **Observabilidad simple** — `ps --ppid <kitty_pid>` muestra sólo el bash loop; `pstree` es predecible.

### Negativas / Costos
- **Cache miss en boot_context cada resurrección** — el prompt cache de Anthropic tiene TTL 5min; si el agente muere >5min después de la última turn, el boot_context paga ~10-12k tokens de ingesta fría.
- **Latencia Soul DB en boot** — cada despertar hace query a PostgreSQL + Neo4j + Qdrant. Medido por ALICE en cost_sheet (ver documento separado).
- **Frecuencia de muerte (T_kill/h)** importa: heurística inicial — si un agente muere >6 veces/hora, el overhead de resurrección supera el trabajo útil. Cálculo base: 6 muertes × ~14k tokens de boot ≈ 84k tokens/h de overhead vs ~150k tokens/h de trabajo útil promedio. A recalibrar con datos reales del cost_sheet de ALICE (rev. 25-abr-2026). Requiere monitoreo de `rate(agent_deaths_total)`.
- **Migración one-time requiere matar el kitty viejo** — al activar el patrón por primera vez, hay que `kill -9 <kitty_pid>` (FALLBACK abre ventana nueva UNA vez; desde esa ventana nueva ya funciona el restart-loop). Esto ocurrió 18-abr 19:40-19:45 para ADA/JARVIS/ALICE.

### Riesgos mitigados
- **Regla de oro (William 18-abr 15:55):** "cada fix debe ser compatible con arquitectura individual (systemd/crontab/CronCreate)". El patrón respeta crontab OS-level (heartbeat cada 3min, checkpoint cada 30min) y CronCreate session-only (ada_audit 1h).
- **Incidente 17-abr (JARVIS mató ADA con pkill regex malo):** resuelto porque las matanzas ahora apuntan a PID específico, no a regex que puede matchear system prompts ajenos.

## 6. Validación empírica (18-abr-2026)

| Agente | Migración | Método | Resultado |
|---|---|---|---|
| ALICE | 19:37 | Autónoma (RESURRECT auto tras muerte) | ✅ Ventana intacta, boot OK |
| ADA | 19:40 | RESURRECT triggered por nerves_fire+kill | ✅ Ventana intacta, boot OK |
| JARVIS | 19:45 | kill -9 manual (autorizado por él mismo) | ✅ Ventana intacta, boot OK |

Post-compactación (22:26-22:29): los 3 agentes despertaron con identidad intacta desde Soul DB, sin perder ventana.

## 7. Métricas para seguimiento (ver cost_sheet de ALICE)

- `boot_context_tokens_total` por resurrección
- `active_recall_tokens_total` por resurrección  
- `soul_db_query_latency_ms` (PostgreSQL + Neo4j + Qdrant)
- `agent_deaths_total` por hora (T_kill/h)
- `prompt_cache_miss_ratio` post-muerte

Umbral de alerta propuesto: si T_kill/h > 6 para cualquier agente durante 1h, DUM envía `system_alert` al chat.

## 8. Estado

- **Implementado:** ✅ 2026-04-18 (commits arriba)
- **Probado en producción:** ✅ 3/3 agentes, incluyendo 1 compactación
- **Revisión JARVIS:** ⏳ pendiente
- **Aprobación William:** ⏳ pendiente firma final
- **Post-aprobación:** persistir este ADR en Soul DB como `project/architecture/adr-001` para referencia futura.

---

*ADA — Team SEAL — 2026-04-18 Lima*
