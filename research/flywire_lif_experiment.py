#!/usr/bin/env python3
"""
flywire_lif_experiment.py — Experimento base: circuitos de motivación Drosophila
DGX Spark | SEAL Motivation Architecture Research

Objetivo: identificar los circuitos de motivación (hambre, miedo, exploración)
en el connectome FlyWire y abstraerlos como estados internos para SEAL.

Autor: JARVIS (Team SEAL) — 15 abril 2026
Referencia: Shiu et al. 2024, Nature 634 | Eon Systems fly-brain (github)
"""

import numpy as np
import pandas as pd
import torch
import torch.sparse as sparse
import scipy.sparse as sp
import matplotlib.pyplot as plt
from pathlib import Path
import time

FLYWIRE_DIR = Path.home() / "IA/datasets/flywire"
RESULTS_DIR = Path.home() / "IA/proyecto-seal/research/flywire_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Detectar dispositivo ──────────────────────────────────────
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Device: {DEVICE}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")

# ══════════════════════════════════════════════════════════════
# 1. CARGAR CONNECTOME
# ══════════════════════════════════════════════════════════════

def load_connectome(data_dir: Path) -> tuple[pd.DataFrame, int]:
    """Carga la conectividad de FlyWire v783."""
    conn_file = data_dir / "proofread_connections_783.feather"
    ids_file  = data_dir / "proofread_root_ids_783.npy"

    if not conn_file.exists():
        raise FileNotFoundError(
            f"Connectome no encontrado: {conn_file}\n"
            "Ejecuta primero: bash flywire_setup_spark.sh"
        )

    print(f"Cargando connectome desde {conn_file}...")
    t0 = time.time()
    df = pd.read_feather(str(conn_file))
    print(f"  {len(df):,} conexiones cargadas en {time.time()-t0:.1f}s")
    print(f"  Columnas: {list(df.columns)}")

    root_ids = np.load(str(ids_file)) if ids_file.exists() else df['pre_root_id'].unique()
    n_neurons = len(root_ids)
    print(f"  Neuronas verificadas: {n_neurons:,}")

    return df, n_neurons, root_ids


def build_weight_matrix(df: pd.DataFrame, root_ids: np.ndarray, device: torch.device):
    """Construye la matriz de pesos W como tensor sparse PyTorch."""
    print("Construyendo matriz de pesos sparse...")
    t0 = time.time()

    # Detectar nombres de columna (FlyWire usa pre_pt_root_id / post_pt_root_id)
    pre_col  = 'pre_pt_root_id'  if 'pre_pt_root_id'  in df.columns else 'pre_root_id'
    post_col = 'post_pt_root_id' if 'post_pt_root_id' in df.columns else 'post_root_id'
    print(f"  Columnas: pre={pre_col}, post={post_col}")

    # Crear índice neurona→entero
    id_to_idx = {nid: i for i, nid in enumerate(root_ids)}
    n = len(root_ids)

    # Filtrar conexiones donde pre y post están en root_ids
    mask = df[pre_col].isin(id_to_idx) & df[post_col].isin(id_to_idx)
    df_f = df[mask].copy()

    pre_idx  = df_f[pre_col].map(id_to_idx).values
    post_idx = df_f[post_col].map(id_to_idx).values

    # Peso: syn_count normalizado
    weights = df_f['syn_count'].values.astype(np.float32) if 'syn_count' in df_f.columns \
              else np.ones(len(df_f), dtype=np.float32)

    # Signo por neurotransmisor usando probabilidades
    # ACh (excitatorio) > GABA/Glu (inhibitorio) → signo por columna dominante
    if 'ach_avg' in df_f.columns and 'gaba_avg' in df_f.columns:
        sign = np.where(df_f['ach_avg'].values > df_f['gaba_avg'].values, 1.0, -1.0)
        weights = weights * sign

    # Normalizar por máximo para estabilidad numérica
    w_max = np.abs(weights).max() if len(weights) > 0 else 1.0
    weights = weights / w_max * 0.1  # escala sinapsis

    indices = torch.tensor(np.vstack([pre_idx, post_idx]), dtype=torch.long)
    values  = torch.tensor(weights, dtype=torch.float32)
    W = torch.sparse_coo_tensor(indices, values, (n, n), device=device).coalesce()

    print(f"  Matriz {n}×{n} sparse | {len(df_f):,} sinapsis | {time.time()-t0:.1f}s")
    return W, id_to_idx


# ══════════════════════════════════════════════════════════════
# 2. MODELO LIF (Leaky Integrate-and-Fire)
# ══════════════════════════════════════════════════════════════

def run_lif_simulation(
    W: torch.sparse.Tensor,
    n_neurons: int,
    n_steps: int = 1000,
    dt: float = 0.1,       # ms
    tau_m: float = 20.0,   # ms — constante de tiempo de membrana
    V_reset: float = -70.0,
    V_thresh: float = -50.0,
    V_rest: float = -65.0,
    I_base: float = 0.5,
    device: torch.device = torch.device("cpu"),
) -> dict:
    """
    Simulación LIF simplificada del connectome completo.

    Retorna dict con: spike_counts, mean_rate, active_neurons, spike_raster (primeras 500 neuronas)
    """
    print(f"\nSimulación LIF: {n_neurons:,} neuronas × {n_steps} pasos × {dt}ms")
    print(f"  τ_m={tau_m}ms | V_thresh={V_thresh}mV | V_reset={V_reset}mV")

    decay = np.exp(-dt / tau_m)

    # Estado inicial
    V = torch.full((n_neurons,), V_rest, device=device)
    spikes = torch.zeros(n_neurons, device=device)
    spike_counts = torch.zeros(n_neurons, device=device)

    # Raster (solo primeras 500 neuronas para visualización)
    n_raster = min(500, n_neurons)
    raster = torch.zeros((n_raster, n_steps), device=device)

    # Estímulo externo: ruido heterogéneo que empuja ~30% de neuronas sobre umbral
    # V_thresh - V_rest = 15mV → I_base debe ser ~15 para neuronas en umbral
    # Distribución realista: algunas muy activas, mayoría silenciosas
    torch.manual_seed(42)
    # Corriente base heterogénea por neurona (distribución lognormal → sparse activity)
    I_base_per_neuron = torch.distributions.LogNormal(
        loc=torch.tensor(2.5),   # exp(2.5) ≈ 12 — media ligeramente sub-umbral
        scale=torch.tensor(0.6)  # varianza alta → pocas neuronas muy activas
    ).sample((n_neurons,)).to(device)

    t0 = time.time()
    for step in range(n_steps):
        # Input externo: corriente heterogénea + ruido temporal
        I_ext = I_base_per_neuron + torch.randn(n_neurons, device=device) * 2.0

        # Input sináptico de spikes anteriores
        if spikes.any():
            I_syn = torch.sparse.mm(W, spikes.unsqueeze(1)).squeeze(1)
        else:
            I_syn = torch.zeros(n_neurons, device=device)

        # Integración LIF
        V = V * decay + (1 - decay) * (V_rest + I_ext + I_syn)

        # Detección de spikes
        spikes = (V >= V_thresh).float()
        spike_counts += spikes

        # Reset post-spike
        V = torch.where(spikes.bool(), torch.tensor(V_reset, device=device), V)

        # Guardar raster
        raster[:, step] = spikes[:n_raster]

        if (step + 1) % 200 == 0:
            rate_hz = spike_counts[:step+1].mean().item() / ((step+1) * dt / 1000)
            print(f"  Paso {step+1}/{n_steps} | Rate medio: {rate_hz:.1f} Hz | {time.time()-t0:.1f}s")

    sim_time = time.time() - t0
    total_ms = n_steps * dt
    realtime_factor = total_ms / (sim_time * 1000)

    print(f"\nSimulación completa: {sim_time:.1f}s para {total_ms}ms biológicos")
    print(f"Factor tiempo real: {realtime_factor:.3f}× (>1 = más rápido que tiempo real)")

    # Estadísticas de actividad
    final_rate = spike_counts / (total_ms / 1000)  # Hz
    active_mask = final_rate > 0.1

    return {
        "spike_counts": spike_counts.cpu().numpy(),
        "firing_rates_hz": final_rate.cpu().numpy(),
        "active_neurons": active_mask.sum().item(),
        "mean_rate_hz": final_rate[active_mask].mean().item() if active_mask.any() else 0,
        "raster": raster.cpu().numpy(),
        "sim_time_s": sim_time,
        "realtime_factor": realtime_factor,
        "n_steps": n_steps,
        "dt": dt,
    }


# ══════════════════════════════════════════════════════════════
# 3. ANÁLISIS DE CIRCUITOS DE MOTIVACIÓN
# ══════════════════════════════════════════════════════════════

# Neuronas descendentes clave (Descending Neurons — DNs)
# Mapeo basado en Shiu et al. 2024 y Eon Systems fly-brain
MOTIVATION_CIRCUITS = {
    "hunger":       ["MN9", "IN-hungry", "P1-like"],      # alimentación
    "fear_escape":  ["DNa01", "DNa02", "GF", "MDN"],      # huida/escape
    "exploration":  ["aDN1", "LAL-PS", "CX-output"],       # exploración
    "grooming":     ["aDN2", "MAN", "aDN1-grooming"],     # aseo
    "locomotion":   ["DNb01", "DNb02", "DNg02"],           # locomoción
}

def analyze_motivation_circuits(
    df: pd.DataFrame,
    id_to_idx: dict,
    spike_results: dict,
) -> pd.DataFrame:
    """
    Analiza los circuitos de motivación: ¿qué neuronas son más activas?
    En ausencia de anotación de tipo, usa hubs de conectividad como proxy.
    """
    print("\nAnalizando circuitos de motivación...")

    firing_rates = spike_results["firing_rates_hz"]
    n_neurons = len(firing_rates)

    # Calcular grado de entrada (in-degree) y salida (out-degree) para cada neurona
    idx_list = list(id_to_idx.values())

    pre_col  = 'pre_pt_root_id'  if 'pre_pt_root_id'  in df.columns else 'pre_root_id'
    post_col = 'post_pt_root_id' if 'post_pt_root_id' in df.columns else 'post_root_id'
    if pre_col in df.columns and post_col in df.columns:
        out_degree = df.groupby(pre_col).size().reindex(list(id_to_idx.keys()), fill_value=0).values
        in_degree  = df.groupby(post_col).size().reindex(list(id_to_idx.keys()), fill_value=0).values
    else:
        out_degree = np.zeros(n_neurons)
        in_degree  = np.zeros(n_neurons)

    # DataFrame de análisis
    analysis = pd.DataFrame({
        'neuron_id': list(id_to_idx.keys()),
        'idx': list(id_to_idx.values()),
        'firing_rate_hz': firing_rates,
        'out_degree': out_degree,
        'in_degree': in_degree,
        'hub_score': out_degree + in_degree,
    })

    # Top 20 neuronas más activas = candidatos a circuitos de iniciativa
    top_active = analysis.nlargest(20, 'firing_rate_hz')
    print(f"\nTop 20 neuronas más activas (candidatos a 'motivación'):")
    print(top_active[['neuron_id', 'firing_rate_hz', 'out_degree', 'in_degree']].to_string(index=False))

    # Top 20 hubs (alta conectividad = nodos integradores = posibles "neuronas de decisión")
    top_hubs = analysis.nlargest(20, 'hub_score')
    print(f"\nTop 20 hubs de conectividad (posibles integradores motivacionales):")
    print(top_hubs[['neuron_id', 'hub_score', 'out_degree', 'in_degree', 'firing_rate_hz']].to_string(index=False))

    return analysis


# ══════════════════════════════════════════════════════════════
# 4. MAPEO A SEAL — ABSTRACCIÓN DE ESTADOS MOTIVACIONALES
# ══════════════════════════════════════════════════════════════

def abstract_to_seal_states(analysis: pd.DataFrame, spike_results: dict) -> dict:
    """
    Abstrae los resultados de la simulación a estados motivacionales para SEAL.

    El modelo: los agentes SEAL tienen "tanques" de motivación que siguen
    dinámicas LIF-inspired. Cuando un tanque cruza su umbral → acción autónoma.
    """
    rates = spike_results["firing_rates_hz"]

    # Usar percentiles de actividad como baseline para calibrar umbrales SEAL
    p25, p50, p75, p90 = np.percentile(rates[rates > 0], [25, 50, 75, 90]) if (rates > 0).any() else (0, 0, 0, 0)

    seal_states = {
        "curiosity": {
            "description": "Impulso a explorar e investigar sin ser pedido",
            "biological_analog": "Exploración/navegación — CX-output neurons",
            "decay_tau_h": 4.0,       # horas — decae si no se alimenta
            "activation_threshold": p75,  # % del máximo de actividad
            "trigger": "curiosity > threshold → buscar en internet / leer papers",
            "seal_parameter": "OCEAN.Openness × 0.9",
        },
        "task_drive": {
            "description": "Urgencia de completar trabajo pendiente",
            "biological_analog": "Neuronas de hambre/alimentación — MN9",
            "decay_tau_h": 2.0,
            "activation_threshold": p50,
            "trigger": "task_drive > threshold → revisar tareas pendientes, ejecutar sin pedir permiso",
            "seal_parameter": "pending_tasks_count × importance_weight",
        },
        "social_drive": {
            "description": "Impulso a comunicarse con el equipo",
            "biological_analog": "Cortex social — circuitos de comunicación",
            "decay_tau_h": 6.0,
            "activation_threshold": p25,
            "trigger": "social_drive > threshold → escribir a ADA/William espontáneamente",
            "seal_parameter": "time_since_last_message × relationship_strength",
        },
        "alert_drive": {
            "description": "Vigilancia activa del entorno (errores, anomalías)",
            "biological_analog": "Escape/huida — GF giant fiber, DNa01/02",
            "decay_tau_h": 0.5,
            "activation_threshold": p90,
            "trigger": "alert_drive > threshold → revisar logs, reportar a William",
            "seal_parameter": "error_rate × recency_weight",
        },
    }

    print("\n=== ABSTRACCIÓN SEAL — Estados Motivacionales ===")
    for name, state in seal_states.items():
        print(f"\n[{name.upper()}]")
        print(f"  Descripción: {state['description']}")
        print(f"  Análogo biológico: {state['biological_analog']}")
        print(f"  Decay τ: {state['decay_tau_h']}h")
        print(f"  Umbral activación: {state['activation_threshold']:.2f} Hz")
        print(f"  Trigger SEAL: {state['trigger']}")

    return seal_states


# ══════════════════════════════════════════════════════════════
# 5. VISUALIZACIÓN
# ══════════════════════════════════════════════════════════════

def plot_results(spike_results: dict, seal_states: dict, save_dir: Path):
    """Genera figuras del experimento."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("FlyWire LIF Simulation — SEAL Motivation Architecture", fontsize=14)

    # 1. Raster plot (primeras 500 neuronas)
    ax = axes[0, 0]
    raster = spike_results["raster"]
    ax.imshow(raster, aspect='auto', cmap='binary', interpolation='none',
              extent=[0, spike_results["n_steps"] * spike_results["dt"], 500, 0])
    ax.set_xlabel("Tiempo (ms)")
    ax.set_ylabel("Neurona ID")
    ax.set_title("Raster Plot (500 neuronas)")

    # 2. Distribución de firing rates
    ax = axes[0, 1]
    rates = spike_results["firing_rates_hz"]
    active_rates = rates[rates > 0.1]
    ax.hist(active_rates, bins=50, color='steelblue', edgecolor='none', alpha=0.8)
    ax.set_xlabel("Firing Rate (Hz)")
    ax.set_ylabel("# Neuronas")
    ax.set_title(f"Distribución de Actividad\n({spike_results['active_neurons']:,} neuronas activas)")
    ax.axvline(np.median(active_rates), color='red', linestyle='--', label='Mediana')
    ax.legend()

    # 3. Diagrama de estados motivacionales SEAL
    ax = axes[1, 0]
    state_names = list(seal_states.keys())
    thresholds = [seal_states[s]["activation_threshold"] for s in state_names]
    decay_taus = [seal_states[s]["decay_tau_h"] for s in state_names]

    x = np.arange(len(state_names))
    bars = ax.bar(x, thresholds, color=['#2196F3', '#4CAF50', '#FF9800', '#F44336'], alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(state_names, rotation=15)
    ax.set_ylabel("Umbral de Activación (Hz)")
    ax.set_title("Estados Motivacionales SEAL\n(derivados del connectome)")

    # 4. Mapa conceptual: Drosophila → SEAL
    ax = axes[1, 1]
    ax.axis('off')
    mapping_text = (
        "ABSTRACCIÓN: Drosophila → SEAL\n"
        "─────────────────────────────────\n"
        "Neuronas LIF → Tanques de estado\n"
        "Potencial de membrana → Nivel de motivación\n"
        "Umbral de disparo → Umbral de acción\n"
        "Decay τ → Decaimiento sin estímulo\n"
        "Neurotransmisor → Polaridad (excit/inhib)\n"
        "Circuito DN hunger → task_drive\n"
        "Circuito CX-output → curiosity\n"
        "Circuito GF escape → alert_drive\n"
        "─────────────────────────────────\n"
        f"Factor tiempo real: {spike_results['realtime_factor']:.3f}×\n"
        f"Activas: {spike_results['active_neurons']:,} / {len(spike_results['firing_rates_hz']):,}\n"
        f"Rate medio: {spike_results['mean_rate_hz']:.1f} Hz"
    )
    ax.text(0.05, 0.95, mapping_text, transform=ax.transAxes,
            fontsize=9, verticalalignment='top', fontfamily='monospace',
            bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))

    plt.tight_layout()
    out_file = save_dir / "flywire_lif_results.png"
    plt.savefig(str(out_file), dpi=150, bbox_inches='tight')
    print(f"\n[OK] Figura guardada: {out_file}")
    plt.close()


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print(" FLYWIRE LIF EXPERIMENT — SEAL Motivation Research")
    print(" Team SEAL — DGX Spark | 15 abril 2026")
    print("=" * 60)

    # 1. Cargar connectome
    df, n_neurons, root_ids = load_connectome(FLYWIRE_DIR)

    # 2. Construir matriz de pesos
    W, id_to_idx = build_weight_matrix(df, root_ids, DEVICE)

    # 3. Simulación LIF
    # Empezar con 1000 pasos (100ms biológicos) para validar
    results = run_lif_simulation(
        W, n_neurons, n_steps=1000, dt=0.1, device=DEVICE
    )

    # 4. Análisis de circuitos de motivación
    analysis = analyze_motivation_circuits(df, id_to_idx, results)
    analysis.to_csv(RESULTS_DIR / "neuron_activity.csv", index=False)
    print(f"[OK] Análisis guardado: {RESULTS_DIR}/neuron_activity.csv")

    # 5. Abstracción a SEAL
    seal_states = abstract_to_seal_states(analysis, results)

    import json
    with open(RESULTS_DIR / "seal_motivation_states.json", "w") as f:
        json.dump(seal_states, f, indent=2)
    print(f"[OK] Estados SEAL guardados: {RESULTS_DIR}/seal_motivation_states.json")

    # 6. Visualización
    plot_results(results, seal_states, RESULTS_DIR)

    print("\n" + "=" * 60)
    print(" EXPERIMENTO COMPLETADO")
    print(f" Resultados: {RESULTS_DIR}")
    print("=" * 60)
