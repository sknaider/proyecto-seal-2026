#!/usr/bin/env python3
"""
lif_circuit_simulation.py — Phase 3.2 FlyWire
LIF simulation sobre los 4 circuitos target de Drosophila.

Circuitos:
  Central Complex (navigation)  → curiosity / task_drive
  Mushroom Body (memory)        → SOUL memory consolidation
  Giant Fiber (escape)          → alert_drive
  Olfactory pathway             → social_drive

τ calibrados de FlyWire v783 (Phase 1+2).
Coupling inter-tank desde Phase 2 seal_tank_coupling.json.

Salidas:
  flywire_results/lif_sim_results.json   — firing rates + voltages por step
  flywire_results/lif_sim_3d.html        — visualización 3D dinámica (Plotly)
"""

import json
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pathlib import Path

OUT_DIR = Path("/home/dadito/IA/proyecto-seal/research/flywire_results")

# ── τ calibrados de FlyWire v783 (inhib_ratio → τ) ──────────────────────────
# curiosity   FB/SMP  inhib_ratio ~0.14-0.18 → τ largo  = 4h
# task_drive  GNG     inhib_ratio ~0.31      → τ medio  = 2h
# social_drive AVLP   inhib_ratio ~0.28      → τ largo  = 4h
# alert_drive GNG/AL  inhib_ratio ~0.49-0.53 → τ corto  = 0.5h
TANKS = {
    "curiosity":    {"tau_h": 4.0, "color": "#4CAF50", "circuit": "Central Complex (FB/SMP)"},
    "task_drive":   {"tau_h": 2.0, "color": "#2196F3", "circuit": "Central Complex (GNG)"},
    "social_drive": {"tau_h": 4.0, "color": "#9C27B0", "circuit": "Olfactory (AVLP/AL)"},
    "alert_drive":  {"tau_h": 0.5, "color": "#F44336", "circuit": "Giant Fiber (GNG/AL)"},
}

# Coupling inter-tank desde Phase 2 (inhib_ratio > 0.40 → inhibitorio neto)
# Todos quedaron EXCITE en Phase 2 — usamos el inhib_ratio como weight modifier
COUPLING = {
    ("curiosity",    "task_drive"):   {"w": 0.15,  "sign": +1},
    ("curiosity",    "social_drive"): {"w": 0.10,  "sign": +1},
    ("curiosity",    "alert_drive"):  {"w": 0.05,  "sign": -1},  # alert suprime curiosidad
    ("task_drive",   "curiosity"):    {"w": 0.20,  "sign": +1},
    ("task_drive",   "social_drive"): {"w": 0.10,  "sign": +1},
    ("task_drive",   "alert_drive"):  {"w": 0.08,  "sign": -1},
    ("social_drive", "curiosity"):    {"w": 0.12,  "sign": +1},
    ("social_drive", "task_drive"):   {"w": 0.08,  "sign": +1},
    ("social_drive", "alert_drive"):  {"w": 0.03,  "sign": -1},
    ("alert_drive",  "curiosity"):    {"w": 0.25,  "sign": -1},  # alert inhibe curiosidad
    ("alert_drive",  "task_drive"):   {"w": 0.20,  "sign": -1},  # alert inhibe task
    ("alert_drive",  "social_drive"): {"w": 0.15,  "sign": -1},
}

# Mushroom Body — modelo memoria (KC activity)
# KC firing correlaciona con MB_CA post-synaptic density (Phase 2)
# Modula consolidación: curiosity alta → KC activos → task_drive sube
MUSHROOM_BODY = {
    "kc_count": 2000,       # KCs identificadas structuralmente (Phase 2 ~1800-2200)
    "calyx_weight": 0.18,   # MB_CA post density → input weight
    "lobe_weight":  0.22,   # MB_ML+MB_VL pre density → output weight
    "tau_h": 3.0,
}


def lif_step(V: float, I_ext: float, tau_h: float, dt_h: float,
             V_rest: float = 0.0, V_thresh: float = 1.0) -> tuple[float, bool]:
    """Un paso LIF: dV/dt = (V_rest - V + I_ext) / tau. Retorna (V_new, fired)."""
    dV = (V_rest - V + I_ext) / tau_h * dt_h
    V_new = V + dV
    if V_new >= V_thresh:
        return V_rest, True  # reset + spike
    return V_new, False


def run_simulation(
    duration_h: float = 24.0,
    dt_h: float = 0.1,
    scenarios: list[dict] | None = None,
) -> dict:
    """
    Simula T horas de actividad LIF en los 4 tanks + Mushroom Body.

    scenarios: lista de {time_h, tank, stimulus} para inyectar corriente
    """
    if scenarios is None:
        # Escenario default: idle→amenaza→exploración (típico día de mosca)
        scenarios = [
            {"time_h": 0.0,  "tank": "curiosity",   "stimulus": 0.3},   # despierta explorando
            {"time_h": 2.0,  "tank": "alert_drive",  "stimulus": 1.5},   # amenaza UV
            {"time_h": 4.0,  "tank": "alert_drive",  "stimulus": 0.0},   # amenaza pasa
            {"time_h": 6.0,  "tank": "social_drive", "stimulus": 0.8},   # señal olfativa
            {"time_h": 10.0, "tank": "task_drive",   "stimulus": 1.0},   # comida encontrada
            {"time_h": 14.0, "tank": "curiosity",    "stimulus": 0.5},   # exploración post-comida
            {"time_h": 18.0, "tank": "alert_drive",  "stimulus": 0.9},   # segunda amenaza
            {"time_h": 20.0, "tank": "alert_drive",  "stimulus": 0.0},
        ]

    steps = int(duration_h / dt_h)
    time_axis = np.arange(steps) * dt_h

    # Estado inicial
    V = {t: 0.0 for t in TANKS}
    V["mushroom_body"] = 0.0
    I_ext = {t: 0.0 for t in TANKS}

    history = {t: [] for t in TANKS}
    history["mushroom_body"] = []
    spikes = {t: [] for t in TANKS}
    firing_rates = {t: [] for t in TANKS}

    # Ventana para firing rate (100ms biológico → ~1h simulado)
    window = max(1, int(1.0 / dt_h))
    spike_buffer = {t: [] for t in TANKS}

    for step in range(steps):
        t = step * dt_h

        # Aplicar estímulos externos
        for sc in scenarios:
            if abs(t - sc["time_h"]) < dt_h / 2:
                I_ext[sc["tank"]] = sc["stimulus"]

        # Mushroom Body: modulado por curiosity (KC calyx input)
        kc_input = V["curiosity"] * MUSHROOM_BODY["calyx_weight"]
        V_mb_new, mb_fired = lif_step(
            V["mushroom_body"], kc_input,
            MUSHROOM_BODY["tau_h"], dt_h
        )
        V["mushroom_body"] = V_mb_new
        history["mushroom_body"].append(V_mb_new)

        # MB output modula task_drive (lobe → GNG)
        mb_output = V["mushroom_body"] * MUSHROOM_BODY["lobe_weight"]

        # LIF para cada tank con coupling inter-tank
        new_V = {}
        for tank, cfg in TANKS.items():
            # Input total = externo + coupling + MB output (para task_drive)
            I_total = I_ext[tank]
            if tank == "task_drive":
                I_total += mb_output

            for (src, dst), coup in COUPLING.items():
                if dst == tank:
                    I_total += V[src] * coup["w"] * coup["sign"]

            V_new, fired = lif_step(V[tank], I_total, cfg["tau_h"], dt_h)
            new_V[tank] = V_new

            history[tank].append(V_new)
            spike_buffer[tank].append(1 if fired else 0)
            if len(spike_buffer[tank]) > window:
                spike_buffer[tank].pop(0)
            firing_rates[tank].append(sum(spike_buffer[tank]) / (window * dt_h))

            if fired:
                spikes[tank].append(t)

        V.update(new_V)

    return {
        "time_axis": time_axis.tolist(),
        "history": {k: [float(x) for x in v] for k, v in history.items()},
        "firing_rates": {k: [float(x) for x in v] for k, v in firing_rates.items()},
        "spikes": spikes,
        "scenarios": scenarios,
        "params": {
            "duration_h": duration_h,
            "dt_h": dt_h,
            "tanks": {k: {"tau_h": v["tau_h"], "circuit": v["circuit"]} for k, v in TANKS.items()},
            "mushroom_body": MUSHROOM_BODY,
        }
    }


def build_visualization(results: dict) -> go.Figure:
    """Crea figura Plotly multi-panel: voltajes + firing rates + spikes + escenarios."""
    t = results["time_axis"]
    scenarios = results["scenarios"]

    fig = make_subplots(
        rows=3, cols=1,
        subplot_titles=[
            "Voltaje de membrana LIF — 4 circuitos SEAL + Mushroom Body",
            "Firing Rate (spikes/h) — actividad por circuito",
            "Raster plot — spikes individuales",
        ],
        vertical_spacing=0.10,
        row_heights=[0.4, 0.35, 0.25],
    )

    # Panel 1: voltajes
    all_tanks = list(TANKS.keys()) + ["mushroom_body"]
    colors_all = [TANKS[k]["color"] for k in TANKS] + ["#FF9800"]
    labels_all = [f"{k} (τ={TANKS[k]['tau_h']}h)" for k in TANKS] + ["mushroom_body (τ=3.0h)"]

    for tank, color, label in zip(all_tanks, colors_all, labels_all):
        if tank in results["history"]:
            fig.add_trace(go.Scatter(
                x=t, y=results["history"][tank],
                name=label, line=dict(color=color, width=2),
                legendgroup=tank,
            ), row=1, col=1)

    # Panel 2: firing rates
    for tank, color in zip(TANKS.keys(), [TANKS[k]["color"] for k in TANKS]):
        if tank in results["firing_rates"]:
            fig.add_trace(go.Scatter(
                x=t, y=results["firing_rates"][tank],
                name=tank, line=dict(color=color, width=1.5),
                legendgroup=tank, showlegend=False,
            ), row=2, col=1)

    # Panel 3: raster
    for i, (tank, color) in enumerate(zip(TANKS.keys(), [TANKS[k]["color"] for k in TANKS])):
        spk = results["spikes"].get(tank, [])
        if spk:
            fig.add_trace(go.Scatter(
                x=spk, y=[i] * len(spk),
                mode="markers", marker=dict(color=color, size=4, symbol="line-ns-open"),
                name=tank, legendgroup=tank, showlegend=False,
            ), row=3, col=1)

    # Líneas verticales de estímulos
    for sc in scenarios:
        if sc["stimulus"] > 0:
            for row in [1, 2, 3]:
                fig.add_vline(
                    x=sc["time_h"], line_dash="dot",
                    line_color="rgba(255,255,255,0.3)", line_width=1,
                    row=row, col=1,
                )

    fig.update_layout(
        title=dict(
            text="<b>FlyWire Phase 3.2 — LIF Circuit Simulation</b><br>"
                 "<sup>Drosophila melanogaster · τ calibrados FlyWire v783 · Team SEAL 2026</sup>",
            font=dict(size=16),
        ),
        template="plotly_dark",
        height=900,
        legend=dict(orientation="v", x=1.02, y=1),
        paper_bgcolor="#0a0a0a",
        plot_bgcolor="#111111",
    )
    fig.update_xaxes(title_text="Tiempo (horas)", row=3, col=1)
    fig.update_yaxes(title_text="V (a.u.)", row=1, col=1)
    fig.update_yaxes(title_text="Hz (spikes/h)", row=2, col=1)
    fig.update_yaxes(
        title_text="Circuito", tickvals=list(range(len(TANKS))),
        ticktext=list(TANKS.keys()), row=3, col=1,
    )

    return fig


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("FlyWire Phase 3.2 — LIF Circuit Simulation")
    print("Circuitos: Central Complex, Mushroom Body, Giant Fiber, Olfactory")
    print("=" * 60)

    print("\n[1] Ejecutando simulación LIF (24h, dt=0.1h)...")
    results = run_simulation(duration_h=24.0, dt_h=0.1)

    print(f"    Pasos: {len(results['time_axis'])}")
    for tank in TANKS:
        n_spikes = len(results["spikes"].get(tank, []))
        max_fr = max(results["firing_rates"][tank]) if results["firing_rates"][tank] else 0
        print(f"    {tank:15s}: {n_spikes} spikes | max_FR={max_fr:.2f} spikes/h")

    print("\n[2] Guardando resultados JSON...")
    out_json = OUT_DIR / "lif_sim_results.json"
    with open(out_json, "w") as f:
        json.dump(results, f, indent=2)
    print(f"    → {out_json}")

    print("\n[3] Generando visualización 3D Plotly...")
    fig = build_visualization(results)
    out_html = OUT_DIR / "lif_sim_3d.html"
    fig.write_html(str(out_html), include_plotlyjs="cdn")
    print(f"    → {out_html} ({out_html.stat().st_size / 1024:.0f} KB)")

    print("\n[✅] Phase 3.2 completo")
    print(f"    Abre: http://100.75.201.110:8502/lif_sim_3d.html")


if __name__ == "__main__":
    main()
