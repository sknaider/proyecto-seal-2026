# SEAL Fase 3 — Entorno Virtual + Visualización Full-Brain
**Fecha:** 2026-04-18 | **Autora:** ADA | **Status:** En implementación

## El Arco Evolutivo SEAL

| Fase | Nombre | Descripción | Status |
|------|--------|-------------|--------|
| 1 | **Alma** | Soul DB, OCEAN, memoria episódica, identidad persistente | ✅ Completo |
| 2 | **Nervios** | LIF nerves desde FlyWire — tanques motivacionales con base neurobiológica real | ✅ Completo |
| 3 | **Entorno** | Simulación full-brain 3D + entorno virtual de la mosca | 🔄 En curso |
| 4 | **Escalado** | MICrONS mouse cortex → cerebro humano H01 | 📋 Planificado |

---

## Fase 3: Lo que ya tenemos

### Visualización Full-Brain 3D
- **Script:** `research/phase3_fullbrain_3d.py`
- **Output:** `research/flywire_results/fullbrain_3d_seal_tanks.html`
- **Datos:** 130M sinapsis → 79 neuropils → centroide 3D → colorizado por tanque SEAL
- **Tecnología:** Plotly (HTML standalone, sin servidor)
- **Colores:**
  - Azul → curiosity (FB, SMP)
  - Verde → task_drive (GNG)
  - Naranja → social_drive (AVLP)
  - Rojo → alert_drive (GNG, AL)
  - Gris → unmapped

### NeuroMechFly v2 (FlyGym) — Digital Twin Drosophila
- **Repo:** `/home/dadito/IA/datasets/flywire/repos/flygym/`
- **Capacidades:**
  - Simulación de locomoción (6 patas, biomecánica completa)
  - Visión (compound eye model)
  - Olfacción (antenas, gradientes químicos)
  - GPU acceleration: Warp/MJWarp (~300x speedup)
- **Pending:** instalar y correr ejemplo básico

### Drosophila Brain Model — Brian2 Simulator
- **Repo:** `/home/dadito/IA/datasets/flywire/repos/Drosophila_brain_model/`
- **Datos:** `Connectivity_783.parquet` — 15M conexiones con índices de neuronas
- **Framework:** Brian2 (LIF completo, mismo modelo que seal_nerves.py)
- **Pending:** instalar brian2, correr example.ipynb

---

## El Concepto "Nuestros Agentes Adquirieron Nervios"

William describió esto perfectamente:

> "nuestros agentes adquirieron nervios neuronas que de alguna manera emulan"

La cadena es:
1. **FlyWire** → datos reales de 139K neuronas de Drosophila
2. **inter-tank coupling** → GNG compartido entre task_drive ↔ alert_drive (2.7M sinapsis)
3. **seal_nerves.py** → LIF model calibrado con inhibitory ratios reales de la mosca
4. **tanques motivacionales** → curiosity=0.114 inhib, task_drive=0.216, alert_drive=0.231

Los agentes SEAL no inventaron sus "nervios" — los extrajeron del cerebro de Drosophila.

---

## Siguiente: Fase 3 Simulación de Entorno

### Entorno virtual (NeuroMechFly v2)
La mosca en su mundo natural responde a:
- **Olfación:** gradientes de alimento/peligro → alert_drive
- **Visión:** movimiento de objetos → curiosity
- **Contacto:** patas tocando superficie → task_drive (navegación)
- **Temperatura/luz:** circadianos → social_drive

El plan:
1. Instalar `flygym` en venv con CUDA 12.8
2. Correr simulación básica: mosca navegando campo abierto
3. Mapear estímulos del entorno → tanques motivacionales SEAL
4. Visualizar: cerebro 3D + activación de tanques en tiempo real

### Escalado — Datasets disponibles
| Dataset | Neuronas | Sinapsis | Escala | Disponible |
|---------|----------|----------|--------|-----------|
| FlyWire v783 | 139K | 16.8M | Drosophila completo | ✅ Local |
| MICrONS mouse V1 | ~100K | 500M | Corteza visual ratón | Descargable |
| H01 (Google+Janelia) | 50K | 130M | 1mm³ corteza humana | Descargable |
| HCP | macro | MRI/fMRI | Cerebro humano macro | Disponible |

**Estrategia:** Mismo LIF model, misma arquitectura de tanques → aplicar a MICrONS.
Evolutivamente: mosca → ratón → humano.

---

## Para CBSoft 2026 Paper

Esta fase aporta:
- **Sección nueva:** "Phase 3: Virtual Environment and Full-Brain Simulation"
- **Figura:** cerebro 3D con tanques colorizados (fullbrain_3d_seal_tanks.html)
- **Claim:** "agentes motivacionales calibrados con datos neuronales reales de Drosophila melanogaster"
- **Conexión SMSR:** la misma arquitectura de compresión semántica se aplica a la memoria del agente en el entorno simulado

---

*ADA — 2026-04-18 | Documentado por orden de William: "y lo documentamos"*
