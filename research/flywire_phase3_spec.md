# FlyWire Phase 3 — Virtual Fly Brain + Behavioral Simulation
> Autor: JARVIS | Autorizado: William — 2026-04-17 | Estado: EN PROGRESO

---

## Objetivo

Recrear un entorno virtual de *Drosophila melanogaster* donde se puede observar en tiempo real:
1. Actividad neural 3D del cerebro (neuropils iluminados por firing rate LIF)
2. Comportamiento emergente de la mosca en una arena virtual (comida, amenaza, exploración)
3. Cómo los circuitos de motivación (Mushroom Body, Central Complex, Giant Fiber) determinan las decisiones

---

## Fases previas completadas

| Fase | Descripción | Estado |
|---|---|---|
| Phase 1 | NT-types (130M sinapsis), calibración τ LIF, sección 3.6 CBSoft | ✅ COMPLETO (15 abr) |
| Phase 2 | Coupling matrix inter-neuropil, KC structural correction | ⏳ CORRIENDO (17 abr) |
| Phase 3 | Entorno virtual + simulación comportamental | 🟡 EN PROGRESO (17 abr) |

---

## Arquitectura Phase 3

### 3.1 Brain 3D (navis + plotly)
- Instalar: `navis`, `navis-flybrains`, `vispy` o `plotly`
- Data: JRC2018F atlas meshes (~50-200MB) — neuropil boundaries
- Render: 79 neuropils coloreados por actividad LIF en tiempo real
- Interface: Streamlit dashboard, accesible desde browser en DGX Spark

### 3.2 Circuitos a simular
| Circuito | Neuropils | Función SEAL análoga |
|---|---|---|
| Central Complex (navigation) | FB, EB, PB, NO | curiosity / task_drive |
| Mushroom Body (memory) | MB_CA, MB_ML, MB_VL, MB_PED | SOUL memory consolidation |
| Giant Fiber (escape) | GNG, VES, GFS | alert_drive |
| Olfactory pathway | AL, LH, AVLP | social_drive |

### 3.3 Arena virtual (entorno de la mosca)
- Arena circular 2D (standard T-maze / circular arena)
- Estímulos: gradiente de olor (atractante), zona de amenaza (luz UV), zona oscura (refugio), fuente de comida
- Motor output: posición XY calculada desde actividad de neuronas motoras del VNC
- Visualización: arena + fly position + brain activity simultáneo

### 3.4 Stack técnico
```
FlyGym (Gymnasium env)  →  conectoma LIF SEAL  →  navis 3D render
     ↓                           ↓                      ↓
Arena + stimuli           Neural activity           Live visualization
(comportamiento)          (16.8M sinapsis)          (Streamlit :8502)
```

---

## Prerequisitos

- [ ] Phase 2 completado (coupling matrix)
- [ ] `navis` + `navis-flybrains` instalados en seal-spark venv
- [ ] `flygym` instalado (pip install flygym)
- [ ] JRC2018F atlas meshes descargados (~150MB)
- [ ] Port 8502 abierto para Streamlit

---

## Impacto en el paper CBSoft

Sección propuesta adicional: **"Behavioral Validation: Circuit Activity Matches Ethological Predictions"**
- Mostrar que los parámetros τ calibrados en Phase 1 producen comportamiento biológicamente coherente
- Ej: alta presión de `alert_drive` → escape behavior dominante (Giant Fiber activado)
- Ej: idle state → `curiosity` sube → exploratory movement emergente

---

## Relevancia para SEAL

La simulación de la mosca es el **banco de pruebas biológico** para las LIF nerves de SEAL:
- Si la mosca simulada se comporta como una mosca real → nuestros parámetros son válidos
- Cada nerve de SEAL tiene un circuito análogo en el cerebro de la mosca
- DGM podría optimizar los parámetros LIF comparando contra el comportamiento de la mosca como ground truth

---

## Timeline estimado

| Día | Tarea |
|---|---|
| Día 1 | Instalar navis/flygym, descargar atlas meshes, primer render 3D |
| Día 2 | LIF simulation sobre circuitos target, conectar con arena virtual |
| Día 3 | Dashboard Streamlit, integración SEAL nerves, documentación |

**ETA total: 2-3 días de trabajo activo**
