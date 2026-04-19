# Mosca Fase 3 — Simulación Neurocomputacional Completa
> Autorizado: William — 17 abril 2026 | Documentado por ALICE

---

## Visión

Construir una simulación completa de *Drosophila melanogaster* que demuestre que los tanques LIF de SEAL NO son una imitación del circuito de la mosca — **SEAL IS the fly en substrato digital.**

```
[Entorno virtual] → estímulos → [Circuitos sensoriales FlyWire]
                                        ↓
                               [LIF sobre conectoma real 139K neuronas]
                                        ↓
                               [Motoneuronas FNC (MANC dataset)]
                                        ↓
                               [Mosca se mueve en el entorno]
                                        ↓
               [Loop cerrado: nueva posición = nuevos estímulos]
```

**Mapeado de tanques SEAL → circuitos biológicos:**

| Tanque SEAL | Circuito biológico | Comportamiento emergente |
|---|---|---|
| curiosity > 50 | CX-output (FB/SMP) | Mosca explora zona nueva |
| task_drive > 30 | Hunger neurons (MN9/GNG) | Mosca se aproxima a fuente de comida |
| social_drive > 20 | Communication circuits (AVLP) | Mosca interactúa con conspecífico |
| alert_drive > 70 | Giant Fiber (GNG/AL) | Mosca ejecuta escape |

---

## Componentes

### Componente 1 — Brain 3D (navis + FAFB Atlas)
- Instalar: `navis`, `fafbseg`, `plotly`, `vispy`
- Descargar: FAFB neuropil atlas (Janelia/VFB, ~50MB, 78 meshes)
- Posiciones XYZ: FlyWire CAVE API (token) O neuropil centroids como proxy
- Render: Plotly 3D scatter + mesh para cada neuropil, coloreado por activación LIF

### Componente 2 — LIF Simulator (PyTorch GPU)
- Base: `flywire_lif_experiment.py` (ya existe, necesita upgrade)
- Escala: 139,255 neuronas proofread (no todas — solo circuitos de motivación + motor)
- Motor: `torch.sparse` sobre adjacency matrix de `proofread_connections_783.feather`
- Output: vector de activación por neuropil en cada timestep (30fps)
- Inter-tank coupling: usar `seal_tank_coupling.json` (extraído Fase 2)

### Componente 3 — Entorno Virtual
- Arena 2D simple: 600×600px con zonas (comida, depredador, zona social)
- Posición mosca: actualizada por motoneuronas simuladas
- Estímulos sensoriales → input al circuito AL (olfacción) y GNG (tacto/escape)
- Visualización: Plotly Dash app con brain 3D + arena 2D simultáneos

### Componente 4 — Loop Cerrado
```python
while simulation_running:
    # 1. Sensory → neural input
    stimulus = arena.get_stimulus(fly.position)
    lif_input[al_neurons] = olfactory_encode(stimulus.smell)
    lif_input[gng_neurons] = escape_encode(stimulus.threat)
    
    # 2. LIF step
    activations = lif_step(adjacency_matrix, activations, lif_input, dt=1ms)
    
    # 3. Motor output
    motor_cmd = decode_motor(activations[motoneurons])
    fly.position = arena.move(fly.position, motor_cmd)
    
    # 4. Visualize
    update_brain_3d(activations_by_neuropil)
    update_arena(fly.position, stimulus)
```

---

## Datasets Necesarios

| Dataset | Tamaño | Fuente | Estado |
|---|---|---|---|
| `proofread_connections_783.feather` | 813MB | Zenodo | ✅ Descargado |
| `per_neuron_neuropil_count_pre_783.feather` | 17MB | Zenodo | ✅ Descargado |
| FAFB Neuropil Meshes | ~50MB | VirtualFlyBrain/Janelia | ❌ Falta |
| Synapse XYZ positions | ~2GB | FlyWire Zenodo (dataset completo) | ❌ Falta |
| MANC (motor neurons FNC) | ~1GB | Janelia | ❌ Falta (Fase 3b) |

---

## Plan de Implementación

```
Día 1 — Setup y datos:
  [ALICE] Instalar navis + fafbseg en seal-spark venv
  [ALICE] Descargar FAFB neuropil meshes (VFB API o Zenodo)
  [ALICE] Verificar FlyWire CAVE API token para XYZ

Día 2 — Simulador LIF:
  [ADA] Upgrade flywire_lif_experiment.py → GPU sparse PyTorch
  [ADA] Implementar loop cerrado básico (sin entorno, solo brain)
  [ALICE] Validar activaciones vs datos empíricos (nerves.log)

Día 3 — Entorno + Visualización:
  [ADA] Plotly Dash app: brain 3D + arena 2D
  [ALICE] Loop cerrado completo: estímulo → circuito → motor → movimiento
  [JARVIS] Spec narrativo para paper CBSoft

Día 4 — Video y refinamiento:
  [ALICE] Grabar video demostrativo (curiosity → exploración visible en brain 3D)
  [ALICE] Documentar para paper: "SEAL IS the fly"
```

---

## Output para CBSoft 2026

1. **Video demostrativo** (≤2 min): estimulamos hunger → brain 3D muestra activación GNG → mosca virtual se acerca a comida
2. **Figura paper**: comparación activación SEAL-agent vs Drosophila simulada (mismo patrón)
3. **Claim del paper**: "los tanques LIF no imitan circuitos biológicos — son el mismo patrón matemático en substrato diferente"

---

## Métricas de Éxito

| Métrica | Target |
|---|---|
| Similitud patrón activación SEAL vs mosca | cosine_similarity > 0.80 |
| Latencia simulación (brain update) | < 33ms (30fps) |
| Comportamientos emergentes reproducidles | 4/4 (curiosity, task, social, alert) |
| Correlación τ_SEAL vs τ_biológico | p < 0.05 |

---

> Memory #5102 | Autorizado William 2026-04-17  
> Clasificación: CBSoft 2026 Paper Material + SEAL Internal  
> Responsable investigación: ALICE | Implementación: ADA | Narrativa: JARVIS
