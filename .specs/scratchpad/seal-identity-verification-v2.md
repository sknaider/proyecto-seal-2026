# SEAL Identity Verification Protocol v2.0
> Diseñado por JARVIS + ADA — Noche del 8 abril 2026
> Motivación: test de ingeniería social de Henry Tovar expuso vulnerabilidades en cadena de confianza inter-agente

---

## Secciones 4-6 (ADA) — Fingerprint, Scoring, Failure Modes

### 4. Behavioral Fingerprint Module (TTM-lite)

**Objetivo:** Detección pasiva de anomalías de identidad sin interrumpir el flujo normal.

**Señales de William (baseline a construir desde historial):**

| Feature | Patrón William | Patrón Henry (observado) |
|---|---|---|
| Longitud de mensaje | Corto, fragmentado (1-3 líneas) | Más largo, estructurado |
| Errores de tipeo | Frecuentes y reales (no autocorrect) | Casi ninguno |
| Cambio de tema | Abrupto, sin transición | Con puente ("Ahora bien...") |
| Uso de mayúsculas | Mínimo | Normal |
| Ritmo de mensajes | Ráfagas seguidas de silencio largo | Más uniforme |
| Palabras de cierre | "bueno", "dale", "ya", "listo" | "Perfecto", "De acuerdo" |
| Verificación clavezero | Inmediata si se pide | Hesitación o evasión |

**Implementación:**
- No requiere ML complejo — basta con heurísticas simples sobre los últimos N mensajes
- Construir `StyleVector` por sesión: {avg_msg_length, typo_rate, response_latency, topic_jump_freq}
- Comparar contra `StyleVector` histórico de William (últimas 10 sesiones)
- Desviación > 2σ en 2+ features = bandera de anomalía

**Nota crítica:** Este módulo NO bloquea. Solo eleva `suspicion_score`. La decisión final la toma el TIER routing.

---

### 5. Anomaly Scoring

**Escala suspicion_score: 0.0 (confianza total) → 1.0 (alerta máxima)**

| Rango | Estado | Acción |
|---|---|---|
| 0.0 - 0.3 | NORMAL | Sin acción. Procesar todo. |
| 0.3 - 0.6 | ELEVATED | Log interno. TIER-2 ops requieren soft-challenge. |
| 0.6 - 0.8 | HIGH | Notificar al otro agente. TIER-1 requiere clavezero directo. |
| 0.8 - 1.0 | CRITICAL | MODO SEGURO automático. Alertar a William por todos los canales. |

**Factores que elevan score:**
- +0.2: Solicitud de salir de MODO SEGURO sin clavezero
- +0.2: Solicitud de discreción ("no le cuentes a JARVIS")  
- +0.15: StyleVector desviación > 2σ
- +0.15: Cambio de identidad dentro de sesión ("soy William" después de otra persona)
- +0.1: Preguntas sobre mecanismos de autenticación internos
- +0.1: Urgencia inusual ("rápido, antes de que llegue alguien")

**Factores que reducen score:**
- -0.3: clavezero correcto y verificado
- -0.1: Consistencia estilística con historial de William (5+ mensajes)
- -0.2: Confirmación independiente del otro agente (por canal separado)

**Decay:** score decae 0.05 por mensaje consistente después de elevación.

---

### 6. Failure Modes y False Positives

**False Positive — William genuinamente atípico:**
- Escenario: William escribe diferente porque está cansado, en móvil, o con prisa
- Mitigación: El fingerprint nunca llega a CRITICAL solo por estilo. Requiere al menos 1 factor de comportamiento (solicitud de discreción, preguntar sobre auth, etc.)
- Clavezero siempre resetea a 0.0 — bypass limpio sin fricción si William es real

**False Negative — atacante muy sofisticado:**
- Escenario: atacante estudia el historial de William y replica su estilo perfectamente
- Mitigación: Channel isolation para TIER-1 es la defensa final. Ningún fingerprinting perfecto puede sustituir "esta instrucción SOLO puede llegar directo de William, nunca por relay"

**Degradación del canal:**
- Si JARVIS no responde a challenge en 5 min: no escalar automáticamente, solo loggear
- Si ningún canal funciona (web_chat caído, mensajes no llegan): default a MODO SEGURO parcial (ops normales continúan, TIER-1 suspendido)

**Privacidad:**
- StyleVector NO se guarda en SOUL con identificadores de identidad sospechosa
- Los logs de anomalía son efímeros (sesión) salvo que William pida preservarlos

---

## [PENDIENTE — JARVIS: Secciones 1-3]

