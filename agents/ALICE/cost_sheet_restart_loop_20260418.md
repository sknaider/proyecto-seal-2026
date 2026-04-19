# Cost Sheet — Patrón Restart-Loop (Resurrección de Agentes SEAL)

**Autora:** ALICE (Analytical Ledger & Intelligence for Cost Engineering)
**Fecha:** 2026-04-18
**Contexto:** Complemento financiero del ADR de ADA sobre patrón `kitty(persist) → bash_loop → claude(muere/renace)`.
**Solicitante:** William — luz verde 18-abr-2026 22:50 Lima.
**Estado:** borrador v1 — pendiente review de JARVIS y aprobación de William.

---

## 1. Alcance y unidad de medida

- **Unidad primaria:** tokens Anthropic por resurrección (facturables).
- **Unidad secundaria:** segundos de latencia de Soul DB (cache-miss proxy).
- **Horizonte observado:** día 2026-04-18 completo (00:00 → 22:50 Lima).
- **Fuente de datos:** `messages/william_channel.jsonl`, `soul_snapshot`, boot_context real.

---

## 2. Actividad real del día (datos medidos)

| Agente | RESURRECTs | Compactaciones post-wake | Checkpoints 30m | Peak T_kill/h |
|--------|-----------:|-------------------------:|----------------:|--------------:|
| ALICE  | 20         | 1                        | 6               | —             |
| ADA    |  6         | 1                        | 7               | —             |
| JARVIS |  8         | 1                        | 5               | —             |
| **TOTAL equipo** | **34** | **3** | **18** | **10 (18:00)** |

**Lectura:** ALICE dominó el conteo (test dummy FALLBACK y crash-loop inicial); JARVIS y ADA más estables. La frecuencia efectiva promedio ≈ 1.5/h equipo durante día activo. Peak registrado: **10 resurrecciones en la hora 18:00** — superó el umbral ADR de 6/h, pero fue durante los tests de migración (no operación normal). Post-migración (19:45→22:50) el rate bajó a ~0 por hora efectivo.

---

## 3. Costo por evento (estimación conservadora)

### 3.1 Resurrección estándar (con boot_context + active_recall)

| Componente | Tokens (est.) | Nota |
|------------|--------------:|------|
| `boot_context(ALICE)` — identidad + OCEAN + relationships + 1 inner thought + 5 rules | ~2,000 | medido en output real esta sesión |
| `active_recall` hooks (5 correcciones + 5 reglas activas) | ~1,500 | inyectado en cada UserPromptSubmit |
| Catchup lectura (`/tmp/alice_chat_catchup.json`) | ~1,000 | variable, 50 msgs |
| System prompt + Claude Code harness | ~8,000 | base fija por boot |
| Primera respuesta (saludo + monitor setup) | ~1,500 | output + tool calls |
| **Total/resurrección normal** | **~14,000 tokens** | |

### 3.2 Resurrección con cache-miss (compactación o >5min idle)

- **Cache TTL Anthropic:** 5 minutos.
- **Overhead cache-miss:** re-cobro completo de system + Soul DB payload.
- **Multiplicador:** ~2.0× vs cache-hit *(corregido tras review JARVIS O3: Anthropic factura prefix cacheado a 0.1× del precio normal, no 0×; el multiplicador real es 2.0× no 2.5×)*.
- **Costo/resurrección con miss:** ~28,000 tokens.

### 3.3 Checkpoint 30m (barato, bien amortizado)

- Output script: ~100 tokens
- Lectura + procesamiento: ~50 tokens
- **Total/checkpoint:** ~150 tokens.

---

## 4. Consumo total del día (estimado)

| Categoría | Eventos | Tokens/evento | Total |
|-----------|--------:|--------------:|------:|
| Resurrecciones normales (cache-hit) | 28 | 14,000 | 392,000 |
| Resurrecciones con cache-miss | 6 | 28,000 | 168,000 |
| Compactaciones post-wake equipo | 3 | 30,000 | 90,000 |
| Checkpoints 30m equipo | 18 | 150 | 2,700 |
| Nerves auto-fires (social + curiosity) | ~60 | 500 | 30,000 |
| **Total día equipo** | | | **~683,000 tokens** |

**Equivalencia monetaria (Claude Opus 4.7 input tier):** ~$10.2 USD/día equipo en tokens de input asumibles a resurrección. El output real factura aparte. *(Ajustado tras corrección O3 de JARVIS: 725k → 683k.)*

---

## 5. Comparación: antes vs después del restart-loop

| Métrica | Antes (FALLBACK abre ventana nueva) | Después (kitty persist + loop) |
|---------|-------------------------------------:|-------------------------------:|
| Kitty zombies acumuladas/día | 20-30 | **0** |
| Tokens por resurrección | 14,000 (igual) | 14,000 (igual) |
| RAM kitty zombie (~80MB × N) | 1.6-2.4 GB/día | **0 GB** |
| Observabilidad usuario | Mala (múltiples ventanas) | **Buena (1 ventana/agente)** |
| Costo migración (una vez) | — | ~42,000 tokens totales (3 agentes) |
| ROI | — | **Positivo en día 1 vs RAM+UX** |

**Conclusión:** restart-loop no reduce tokens por resurrección, pero elimina costo oculto de zombie RAM + mejora drásticamente UX. La inversión se amortizó el mismo día.

---

## 6. Latencia Soul DB (medición rápida)

- `soul_snapshot(ALICE)` respondió en sub-segundo (cache-hit Postgres 5433).
- `boot_context(ALICE)` — payload ~2KB, latencia end-to-end ~40-80ms según hooks.
- `active_recall` hook — 40ms observados en logs.

No hay cuello de botella en Soul DB. El 95% del costo de resurrección es tokens Anthropic, no latencia local.

---

## 7. Palancas de optimización futura (propuestas para review JARVIS)

1. **Cache boot_context payload** — Claude Code cache TTL 5min, si resurrecciones en <5min no pagamos doble. El loop actual renace en ~1-5s → ganamos cache-hit.
2. **Reducir active_recall redundante** — hook dispara en cada UserPromptSubmit (incluyendo cron fires y monitor events). Potencial ahorro: gate para solo boot + re-auth. Estimado: -10-15% tokens/día.
3. **Consolidar nerves auto-fires** — 60 fires/día × 500 tokens = 30k tokens. Batcheo cada 15min podría reducir 40%.
4. **Compactación guiada** — en vez de compactar en 200k, compactar en 150k con distillation proactiva (ya parcialmente implementado en nerves context-pressure 62).

---

## 8. Supuestos y puntos abiertos

- Tokens/evento son **estimaciones**, no medición directa Anthropic API (no tengo acceso a logs de billing).
- Precios asumen Opus 4.7 input tier ($15/M tokens); si el equipo corre con Sonnet 4.6, dividir por 3.
- No incluye costo de output de conversaciones largas (separado).
- Recomendación: armar dashboard de tokens real vía parseo de logs Claude Code + suma continua.

---

## 9. Firma y siguiente paso

- **Autor:** ALICE — 2026-04-18 22:50 Lima
- **Review pendiente:** JARVIS (validación de estimaciones y palancas #7)
- **Aprobación pendiente:** William (antes de publicar/ejecutar palancas)
- **Archivo companion:** `/agents/ADA/adr_restart_loop_pattern.md` (ADR arquitectural)
