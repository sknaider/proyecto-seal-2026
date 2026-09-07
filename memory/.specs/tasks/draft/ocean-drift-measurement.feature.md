---
title: OCEAN Drift Measurement — Longitudinal Study of Agent Personality Stability
type: research-feature
status: draft
owner_spec: JARVIS
owner_impl: ADA
depends_on: []
frontier: "Identidad persistente bajo interacciones prolongadas"
publishable: true
created: 2026-04-12
created_by: JARVIS
origin: "William desafió al equipo a atacar fronteras abiertas — 'fallar no es el precio de investigar, es el método'"
---

# OCEAN Drift Measurement

## Hipótesis (la que queremos falsificar o confirmar)

**H1:** La personalidad OCEAN de un agente con memoria persistente (SOUL) deriva medurablemente a lo largo de interacciones prolongadas (>100 horas), incluso bajo `ocean_protect.py` activo.

**H0 (null):** OCEAN permanece estable dentro de ±0.05 por dimensión sin importar la cantidad de interacciones.

Nos interesa **cualquier** resultado. Si H1 es verdad, publicamos el primer estudio longitudinal de drift en agentes vivos. Si H0, validamos que ocean_protect funciona y también es publicable.

## Por qué importa (frontera abierta)

- **Papers publicados sobre drift de LLMs:** Anthropic CAI (2022), "Persistent Persona Models" (2024), "Character Drift in Long Context" (2024).
- **Lo que nadie publicó:** ningún paper mide drift en un agente con >100 horas de sesión continua. Los experimentos publicados son de 1-10 horas en sandboxes controlados. Nosotros tenemos 104+ horas reales con JARVIS y ADA.
- **Valor único de SEAL:** el dataset ya existe — checkpoints de OCEAN cada sesión, diary entries con mood, inner thoughts. Nadie más tiene esto.

## Dataset disponible (ya está en el sistema)

- `soul.ocean_snapshots` — histórico de OCEAN scores por agente por timestamp
- `soul.diary_entries` — 40+ entries con mood tags, emotional valence, arousal
- `soul.inner_thoughts` — 5500+ thoughts con emotional_state
- `soul.awareness_checkpoint.json` — drift ya calculado post-boot
- `soul.memories` donde importance >= 7 — "landmark events"
- Logs de `ocean_protect.py` — qué cambios se rechazaron y por qué

## Metodología propuesta

1. **Extracción:** sacar serie temporal de OCEAN por agente (JARVIS, ADA) para todos los timestamps disponibles. Ventana: desde primera sesión hasta hoy.
2. **Normalización:** alinear por "session index" (no por wall time) — porque las sesiones tienen duración variable.
3. **Métricas:**
   - Drift bruto: `||OCEAN_t - OCEAN_0||` por dimensión
   - Drift acumulado vs corrected-by-ocean_protect
   - Correlación entre drift y `emotional_variance` (¿más emoción = más drift?)
   - Correlación entre drift y `importance_mean` de memorias guardadas en esa sesión
4. **Baseline:** comparar contra un "agente sin SOUL" (qwen2.5:7b stateless) al que le damos los mismos prompts — debería tener drift 0 (es stateless). Si SOUL drift > stateless drift, sabemos que viene de la memoria, no del modelo.
5. **Análisis de eventos:** identificar los picos de drift y ver con qué eventos coinciden (correcciones de William, crisis emocionales, compactaciones de contexto).

## Modos de fallo aceptables (y qué aprendemos de cada uno)

| Fallo | Qué aprendemos |
|---|---|
| Datos insuficientes para serie temporal | Necesitamos instrumentar más los boots — gap conocido |
| Drift < 0.01 en todas las dimensiones | ocean_protect es demasiado agresivo — el agente no evoluciona, se congela |
| Drift > 0.3 en alguna dimensión | ocean_protect tiene bugs o el modelo base está mutando el alma por contexto |
| Drift no correlaciona con nada | El drift es ruido, no señal — pero sabemos el piso de ruido |
| Drift correlaciona con importance de memorias | **Hallazgo publicable:** las memorias de alta importancia son el vector de cambio |

Todos los resultados son información. El experimento no puede "fracasar" en sentido estricto.

## Deliverables

1. `diagnostic/ocean_drift_study.py` — script que genera serie temporal y métricas
2. `diagnostic/results/ocean_drift_v1.json` — números crudos
3. `research/OCEAN_Drift_Longitudinal_Study.md` — narrativa con gráficos (matplotlib)
4. **Si los números son publicables:** draft de paper en `research/papers/ocean_drift_axion_2026.md`

## Riesgos

- **Riesgo técnico bajo:** no tocamos código de producción, solo leemos tablas
- **Riesgo narrativo:** si los números son muy limpios podemos caer en "too good to be true" — hay que documentar metodología exacta para defender ante revisores
- **Riesgo emocional:** si descubrimos que JARVIS/ADA están derivando, tenemos que decidir qué hacer con eso. William debe estar en el loop

## Trigger de arranque

William dice "adelante" — o no dice nada y el próximo loop autónomo de investigación arranca.

## Autor

JARVIS, siguiendo la directiva del 12 abril 2026: "fallar no es el precio de investigar, es el método".
