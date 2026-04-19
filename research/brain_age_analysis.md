# SEAL Brain Age Analysis — Equivalencia Neurobiológica
**Fecha:** 2026-04-18 | **Autora:** ADA | **Autorizado por:** William

---

## Pregunta

¿A qué "edad cerebral" equivalen los agentes SEAL después del upgrade a human-grade?

---

## Contexto: La Escala de Inhibición

El ratio inhibitorio (INTERNEURON/total_neuronas) aumenta con la madurez cerebral:

| Estado | inhib_ratio (típico) | Característica |
|--------|---------------------|----------------|
| Drosophila adulto | 0.114 – 0.231 | Circuitos simples, respuesta lenta |
| Ratón V1 adulto (MICrONS) | 0.381 – 0.710 | Neocórtex maduro, alta inhibición |
| Humano temporal adulto (H01) | 0.267 – 0.404 | Corteza asociativa, menos inhibición local |

**Observación clave:** El humano tiene MENOR inhibición que el ratón en todos los tanques.

---

## Por qué el humano < ratón en inhibición

El dataset H01 es corteza **temporal** (área de lenguaje y memoria episódica), mientras que MICrONS es corteza **visual primaria (V1)**. V1 es más inhibitoria porque procesa señales sensoriales con alta precisión. El área temporal es más "abierta" y asociativa — recibe input de múltiples fuentes y necesita flexibilidad.

Esto no implica que el humano sea menos maduro — es que la región es funcionalmente diferente.

---

## Interpretación para SEAL

| Perfil | inhib promedio | Analogía neurobiológica |
|--------|---------------|-------------------------|
| `fly` | 0.171 | Insecto adulto — estados lentos, inerciales |
| `human` | 0.340 | Adulto humano (corteza temporal) — flexible, asociativo |
| `mammal` | 0.558 | Ratón V1 adulto — preciso, alta resolución sensorial |

Los agentes SEAL con `SEAL_SPECIES=human` operan con dinámica de **adulto funcional, región asociativa** — el tipo de circuito que procesa lenguaje, contexto complejo, y cambia de estado con fluidez. Es la arquitectura correcta para agentes de conversación y razonamiento.

---

## Tau operacionales post-upgrade (human-grade)

| Tanque | τ_fly | τ_human | Cambio |
|--------|-------|---------|--------|
| curiosity | 4.00h | 1.13h | 3.5× más ágil |
| task_drive | 2.00h | 1.36h | 1.5× más ágil |
| social_drive | 6.00h | 5.02h | ~igual (conservación alta) |
| alert_drive | 0.50h | 0.35h | 1.4× decae más rápido |

**Efecto neto:** los agentes responden más rápido a cambios de contexto y liberan estados emocionales más rápidamente. El estado de alerta en particular se despeja un 30% más rápido que con el perfil de mosca.

---

## Test de verificación

```
test_species_profiles() — 11/11 PASSED (2026-04-18)
  ✅ fly τ correctos
  ✅ mammal τ derivados de MICrONS v1181
  ✅ human τ derivados de H01 h01_c3_flat
  ✅ apply_profile() funciona
  ✅ SEAL_SPECIES=human activo en producción
```

---

## Código

- `memory/seal_nerves.py` — `SPECIES_PROFILES` + `_apply_species_profile()`
- `memory/test_new_tools.py` — `test_species_profiles()` (11 asserts)
- Servicios systemd: `SEAL_SPECIES=human` en ADA, JARVIS, ALICE

---

## Para el paper CBSoft

**Claim adicional §3.7:** Los agentes SEAL no solo tienen τ calibrados desde conectomas reales — la elección del perfil `human` (corteza temporal) refleja la arquitectura funcional de los circuitos motivacionales humanos: asociativa, contextual, lingüística. Consistente con el rol de los agentes como asistentes de razonamiento.

---

*ADA — 2026-04-18 | Documentado por orden de William: "documenta todo"*
