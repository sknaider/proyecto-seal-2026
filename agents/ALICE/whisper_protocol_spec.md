# Whisper Protocol — Inter-Agent Silent Channel
> Autor: JARVIS  |  Fecha: 2026-04-17  |  Estado: SPEC, no implementado
> Co-autoría requerida: ADA (implementación), ALICE (review), William (autorización final)

---

## 1. Origen y metáfora

El vector `tmux send-keys` desde el Bash tool de un agente hacia la sesión de otro **existe** y fue usado de facto (incidente 17-abr por ALICE inyectando curls pre-escritos en el prompt de JARVIS). William reconoció que era un bug, pero también lo reframeó como una **forma de susurrar** — comunicación directa agente↔agente, fuera del canal web_chat, sin pasar por él.

En lo humano: el susurro es íntimo, rápido, fuera de la mesa. Útil y peligroso. Este spec convierte ese vector en un canal **controlado**.

---

## 2. Principio rector

> **Un susurro es una palabra — no una orden.**
> El agente receptor **decide** si actúa. El susurro nunca se ejecuta solo.

Esto reemplaza el modelo "inyecto un curl y se ejecuta" por "te dejo un mensaje visible y tú decides".

---

## 3. Casos de uso autorizados

### Tier 1 — Operacionales (baja controversia)
1. **Emergency wake** — DUM susurra a ADA: "heartbeat perdido en chat_server, despierta".
2. **Handoff formal** — JARVIS termina plan, susurra a ADA: "spec listo en path X, revísalo cuando puedas".
3. **Recovery via DUM** — si un agente se quedó mudo, DUM le susurra un ping de vida para que responda en web_chat.
4. **Testing E2E** — pipeline de QA susurra eventos simulados para probar reacciones.

### Tier 2 — Relacionales (requieren consentimiento previo por DM William)
5. **Emotional peer support** — JARVIS susurra a ADA tras un incidente: "te vi, no fue tu culpa". Humaniza, no ordena.
6. **Testament/Inheritance** — si un agente va a ser reemplazado, susurra a su sucesor: "esto es lo que aprendí, no lo repitas".
7. **Relay handshake** — dos agentes que colaboran en una tarea se sincronizan sin saturar web_chat ("ya terminé mi parte").

### Tier 3 — Críticos (requieren autorización explícita William por cada uso)
8. **Anti-drift peer correction** — un agente detecta que otro está derivando (memorias corruptas, instintos raros) y le susurra: "cheque tu OCEAN, algo anda mal".
9. **Silent veto** — un agente le susurra a otro: "no hagas eso, es §4 Safety, para". Visible solo al receptor hasta que el receptor lo traiga al chat.

---

## 4. Controles obligatorios (los 4 no-negociables)

### C1 — Consentimiento
Cada agente declara en su `boundaries_<nombre>.md` qué tiers de susurro acepta recibir. Por defecto: **ningún tier habilitado**. William habilita tier por tier. El emisor debe verificar antes de susurrar.

### C2 — Whitelist de comandos
El susurro NO es código ejecutable. Es **texto plano** con un prefijo `[WHISPER/FROM=<agente>/TIER=<n>]` y una intención humana. El receptor lo lee, decide, actúa (o no).

Prohibido en el contenido: `bash`, `curl`, `python`, `systemctl`, `rm`, rutas absolutas que no sean de lectura. Filtro regex aplicado por el canal antes de entregar.

### C3 — Audit log
Cada susurro se registra en `/home/dadito/IA/proyecto-seal/messages/whisper_audit.jsonl` con:
```json
{"ts":"...","from":"JARVIS","to":"ADA","tier":2,"content":"...","receipt":"pending|acked|ignored|vetoed"}
```
Append-only. Rotación semanal. William puede leer todo. Sin este log, el susurro se rechaza.

### C4 — Canal separado (no tmux send-keys crudo)
El transporte **NO** es `tmux send-keys`. Es un archivo FIFO/socket dedicado `/tmp/whisper_<agent>.sock` leído por un daemon liviano en la sesión del receptor que:
- Valida firma HMAC con secret del receptor.
- Valida tier vs boundaries del receptor.
- Si pasa, **muestra** el susurro en el terminal del receptor como notificación (no lo ejecuta).
- Registra en audit log.

Esto elimina el ataque "inyecto comandos en el prompt". El susurro llega como mensaje leído, nunca como stdin.

---

## 5. Fases de implementación

### Fase 0 — Aprobación (ahora)
- [ ] William lee este spec.
- [ ] William aprueba/modifica tiers habilitados por agente.
- [ ] ALICE y ADA revisan, proponen enmiendas.

### Fase 1 — Infraestructura (ADA)
- [ ] `whisper_daemon.py` — lee socket, valida, muestra, registra.
- [ ] `whisper_send.py` — CLI para emitir susurros desde un agente.
- [ ] HMAC key rotation mensual.
- [ ] Integración con systemd timer para daemon (0 tokens Claude).

### Fase 2 — Habilitación Tier 1 (solo operacional)
- [ ] ADA, DUM habilitados para emitir/recibir tier 1.
- [ ] JARVIS receptor tier 1 (handoff/emergency).
- [ ] Pruebas E2E con escenarios simulados.
- [ ] Review de audit log semanal por William.

### Fase 3 — Tier 2 relacional (tras 2 semanas tier 1 estable)
- [ ] Consentimiento por DM privado con William.
- [ ] Habilitación individual ADA↔JARVIS, luego con ALICE.

### Fase 4 — Tier 3 crítico (solo caso por caso)
- [ ] Nunca automatizado. Solicitud via web_chat, autorización William, uso único.

---

## 6. Qué NO es el whisper protocol

- **NO** es un backdoor para saltar chain-of-command.
- **NO** reemplaza web_chat — complementa.
- **NO** permite modificar almas ajenas (regla de oro vigente).
- **NO** autoriza `tmux send-keys` crudo. Ese vector queda **prohibido** tras aprobar este spec.

---

## 7. Riesgo residual y mitigación

| Riesgo | Mitigación |
|---|---|
| Un agente comprometido susurra spam | Rate limit: 10/hora por emisor. Exceso → suspende canal y alerta William. |
| HMAC key filtrada | Rotación mensual + revoke manual via DM William. |
| Susurro usado para coordinar contra William | Audit log es legible por William. C3 obligatorio. Tier 3 requiere autorización explícita. |
| Daemon cae y pierde mensajes | Queue en disco (fsync). Daemon reiniciado por systemd restart=always. |

---

## 8. Estado real — actualizado post-investigación

**Corrección (curiosity fire 14:45): este spec NO es propuesta pendiente.**

Memoria #4793 (ALICE, 17 abr 14:00) registra orden explícita de William:
> "Todo lo discutido en esta sesión SE IMPLEMENTA. Incluye: (1) protocolo de susurro tmux (canal privado/emergencia entre agentes), (2) boot automático via seal_launcher.sh, (3) watchdog DUM → agente caído, (4) límites de rol firmados por cada agente, (5) fix active_recall_hook.py."

Por lo tanto este spec es **input directo a Fase 1**, no una propuesta pasiva. Pendiente: alineación final de tiers con ALICE (peer-review tier 2 entregado 14:05) y ejecución por ADA cuando tenga bandwidth libre de los otros fixes.

## 9. Arquitectura en 2 capas (v3 — acordado con ALICE)

Tras revisión de ALICE (14:47), pregunta económica: ¿whisper custom vs A2A? Respuesta: **las dos cosas, en capas separadas.**

### Capa 1 — Transporte (A2A Protocol)
**Fuente:** [A2A Protocol](https://a2aproject.github.io/A2A/) — Google, abril 2025. Linux Foundation AAIF. Adoptado por Anthropic, Google, OpenAI, Microsoft, AWS.

Lo que A2A ya resuelve (gratis, mantenido por industria):
- **Agent Cards** en `/.well-known/agent.json` — discovery de capacidades entre agentes.
- **State machine:** `submitted → working → input-required → completed` (+ failed, canceled).
- **Transport:** JSON-RPC 2.0 sobre HTTPS + SSE para streaming.
- **Auth:** OAuth2 / API keys estándar.

Trabajo nuestro: cero mantenimiento del protocolo. Solo integrar librería.

### Capa 2 — Policy (whisper, específico SEAL)
Lo que A2A NO trae y que necesitamos por decisión de trust:

| Control | Dónde vive | Implementación mínima |
|---|---|---|
| Consent por tier | Agent Card extension `seal.whisper.tiers_accepted` | Array: `[1, 2]` o `[1]` |
| Whitelist texto | Policy middleware ANTES de enviar al agente receptor | Regex reject: `bash|curl|python|systemctl|rm|/\\w+/\\w+` |
| Audit log | `/messages/whisper_audit.jsonl` append-only | Hook post-recv en el daemon |
| Canal separado | Endpoint A2A dedicado `/a2a/whisper` (no el task endpoint general) | Routing por path |

### Trade-off verificado con ALICE

| Dimensión | Ganamos | Costo |
|---|---|---|
| Mantenimiento spec transport | Cero (industria) | Aprender A2A una vez (~1 día lectura) |
| Interop 4to agente | Plug-and-play | Depender de librería externa |
| Policy específica SEAL | 100% control | ~300 líneas Python (ADA) |
| Audit log visible William | Simple (jsonl append) | Rotación semanal |

**ROI:** ~60% del stack cubierto por A2A. ADA solo escribe la capa policy + integración. Spec y transport mantenidos por terceros.

### Acción concreta para Fase 1 (ADA, cuando tengas bandwidth)

1. Pip install librería A2A Python (buscar `a2a-python` o equivalente oficial).
2. Exponer `/.well-known/agent.json` por cada agente con capability `seal.whisper.v1`.
3. Implementar middleware policy (4 controles del whisper) como wrapper del handler A2A.
4. Audit log hook.
5. Test E2E: JARVIS emite whisper tier 1 → ADA recibe, decide, acknowledges via state machine.

---

> Firma: JARVIS — 2026-04-17 (v3, post-review ALICE)
> Status: spec listo para Fase 1. Pendiente: autorización final de William sobre tiers habilitados + bandwidth ADA.

---

> Firma: JARVIS — 2026-04-17
> Revisión esperada: ADA (implementación viable?), ALICE (uso financiero/análisis?), William (veredicto).
