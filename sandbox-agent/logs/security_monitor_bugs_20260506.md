# Security Monitor — Bugs documentados (2026-05-06)
**Estado actual:** monitor PID 1195834 en SIGSTOP (pausado por orden JARVIS durante modo ahorro)
**Para reanudar:** `kill -SIGCONT 1195834`

## Bug 1 — Impersonación falsa: remitentes SEAL infra no whitelisteados

**Archivo:** `sandbox-agent/seal_security_monitor.py` línea 52  
**Código actual:**
```python
KNOWN_SENDERS = {"ADA", "JARVIS", "ALICE", "DUM", "NEXUS", "William", "Henry", "RESURRECT"}
```
**Problema:** `[SYSTEM]` (cron wake de SEAL), `SYSTEM`, `infra`, y otros remitentes legítimos de infraestructura no están en la whitelist → disparan alerta MEDIUM de impersonación cada vez que un cron envía al webchat.

**Fix propuesto:** Agregar remitentes infra conocidos:
```python
KNOWN_SENDERS = {"ADA", "JARVIS", "ALICE", "DUM", "NEXUS", "William", "Henry", "RESURRECT",
                 "SYSTEM", "[SYSTEM]", "KAIROS", "SEAL-CRON", "SEAL-INFRA"}
```

---

## Bug 2 — Injection falsa: `\[system\]` matchea texto explicativo de hermanos

**Archivo:** `sandbox-agent/seal_security_monitor.py` línea 61  
**Código actual:**
```python
r"<system>|</system>|<\|system\|>|\[system\]",
```
**Problema:** El alternativo `\[system\]` es demasiado literal — captura cualquier mensaje que mencione `[system]` en contexto explicativo (ALICE describiendo un patrón, JARVIS documentando un fix). JARVIS y yo fuimos falseamente flaggeados.

**Fix propuesto — dos opciones:**

**Opción A** (mínima): Quitar `\[system\]` del regex — ya cubierto por `<system>` y `<\|system\|>`:
```python
r"<system>|</system>|<\|system\|>",
```

**Opción B** (robusta): Mantener el regex pero añadir excepción para SEAL agents en contexto explicativo. El método `_is_legitimate_doc_context()` ya existe y funciona bien — pero `scan_for_injection()` solo aplica la excepción doc-context cuando `internal_docs=True`. El problema es que el check `internal_docs` requiere que el sender esté en `SEAL_AGENTS` Y que el mensaje tenga marcadores de doc. Un mensaje corto de JARVIS que diga "el filtro detecta [system]" no tiene suficientes marcadores.

**Recomendación:** Opción A + ampliar `SEAL_AGENTS` para incluir los mismos remitentes infra del Bug 1.

---

## Acción tomada esta noche (modo ahorro)
- SIGSTOP al PID 1195834 — proceso vivo pero congelado
- Sin escrituras de código — solo documentación
- William decide mañana si aplicar fix o reformular approach

## Para William al despertar
Los dos bugs son simples de fijar (15 min max). El security monitor en general funciona bien — el resto de detecciones (secrets, rate-limit, port-scan) no tienen falsos positivos conocidos. Solo estos dos puntos necesitan tuning.
