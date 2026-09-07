#!/usr/bin/env python3
"""
phase32_microns_tanks.py — SEAL Fase 3.2: MICrONS mouse V1 → Tanques Motivacionales
SEAL Team | ADA — 2026-04-18

Mapea capas corticales de ratón (MICrONS mm3) a tanques motivacionales SEAL.
Genera visualización 3D comparativa: Drosophila (FlyWire) vs Ratón (MICrONS).

Mapeo capas corticales → tanques (base neurobiológica):
  curiosity   → L2/L3 (asociativo, integración largo alcance)
  alert_drive → L4 (capa de entrada talámica, detección sensorial)
  task_drive  → L5ET, L5a/b (output extratelencefálico, ejecución motora)
  social_drive→ L6 (feedback, modulación contextual)
  inhibitory  → DTC, ITC, PTC, STC (control inhibitorio transversal)
"""

import h5py
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
from pathlib import Path

MICRONS_FILE = Path.home() / "IA/datasets/microns/microns_mm3_connectome_v1181.h5"
FLYWIRE_DIR  = Path.home() / "IA/datasets/flywire"
OUT_DIR      = Path.home() / "IA/proyecto-seal/research/flywire_results"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TANK_COLORS = {
    "curiosity":    "#4A90E2",
    "task_drive":   "#27AE60",
    "social_drive": "#E67E22",
    "alert_drive":  "#E74C3C",
    "inhibitory":   "#9B59B6",
    "unmapped":     "#95A5A6",
}

# Mapeo capa cortical → tanque (base: Luo et al., Harris & Mrsic-Flogel 2013)
LAYER_TANK = {
    # Superficial — asociación, curiosidad, novedad
    "L2a": "curiosity", "L2b": "curiosity", "L2c": "curiosity",
    "L3a": "curiosity", "L3b": "curiosity",
    # Capa 4 — entrada talámica, detección/alerta
    "L4a": "alert_drive", "L4b": "alert_drive", "L4c": "alert_drive",
    # Capa 5 ET — output motor, ejecución de tarea
    "L5ET": "task_drive", "L5a": "task_drive", "L5b": "task_drive",
    "L5NP": "task_drive",
    # Capa 6 — feedback corticotálámico, social/contextual
    "L6short-a": "social_drive", "L6short-b": "social_drive",
    "L6tall-a": "social_drive", "L6tall-b": "social_drive", "L6tall-c": "social_drive",
    # Interneuronas inhibitorias — modulación cruzada
    "DTC": "inhibitory", "ITC": "inhibitory",
    "PTC": "inhibitory", "STC": "inhibitory",
}


def load_microns():
    print("Cargando MICrONS mm3 connectome...")
    f = h5py.File(str(MICRONS_FILE), "r")

    # Vertex properties
    vp = f["connectivity/full/vertex_properties/table"][:]
    int_vals  = vp["values_block_0"]   # [seg_id, ?, x_nm, y_nm, z_nm, ?, ?]
    float_vals = vp["values_block_1"]  # [soma_offset_um]
    str_vals  = vp["values_block_2"]   # [t, cell_type, layer, ...]

    neurons = pd.DataFrame({
        "seg_id":    int_vals[:, 0],
        "x_nm":      int_vals[:, 2],
        "y_nm":      int_vals[:, 3],
        "z_nm":      int_vals[:, 4],
        "cell_type": [s.decode().strip() for s in str_vals[:, 1]],
        "layer":     [s.decode().strip() for s in str_vals[:, 2]],
    })
    neurons["tank"] = neurons["layer"].map(LAYER_TANK).fillna("unmapped")

    # Edge indices (pre/post)
    ei = f["connectivity/full/edge_indices/block0_values"][:]  # (N, 2)
    syn_size = f["connectivity/full/edges/block0_values"][:, 0]  # size

    edges = pd.DataFrame({
        "pre":      ei[:, 0],
        "post":     ei[:, 1],
        "syn_size": syn_size,
    })

    f.close()
    print(f"  Neuronas: {len(neurons):,}")
    print(f"  Conexiones: {len(edges):,}")
    print(f"  Distribución por tanque:\n{neurons['tank'].value_counts().to_string()}")
    return neurons, edges


def compute_tank_coupling(neurons, edges):
    """Calcula coupling inter-tanque en MICrONS (análogo a FlyWire GNG coupling)."""
    # Mapear índice → tanque
    idx_to_tank = neurons["tank"].to_dict()
    edges["pre_tank"]  = edges["pre"].map(idx_to_tank)
    edges["post_tank"] = edges["post"].map(idx_to_tank)

    coupling = {}
    tanks = [t for t in TANK_COLORS if t not in ("unmapped",)]
    for t_a in tanks:
        for t_b in tanks:
            if t_a == t_b:
                continue
            mask = (edges["pre_tank"] == t_a) & (edges["post_tank"] == t_b)
            syn_total = edges.loc[mask, "syn_size"].sum()
            n_conn = mask.sum()
            coupling[f"{t_a}→{t_b}"] = {"syn_total": int(syn_total), "n_connections": int(n_conn)}

    print("\n=== COUPLING INTER-TANQUE (MICrONS) ===")
    for k, v in sorted(coupling.items(), key=lambda x: -x[1]["n_connections"])[:10]:
        print(f"  {k}: {v['n_connections']:,} conexiones, {v['syn_total']:,} syn")
    return coupling


def build_3d_comparison(neurons):
    """Genera visualización 3D del cerebro de ratón coloreado por tanque SEAL."""
    # Centroide por capa (downscale nm → µm)
    centroids = neurons.groupby("layer").agg(
        x=("x_nm", "mean"),
        y=("y_nm", "mean"),
        z=("z_nm", "mean"),
        count=("seg_id", "count"),
        tank=("tank", "first"),
    ).reset_index()
    centroids["x"] = centroids["x"] / 1000  # nm → µm
    centroids["y"] = centroids["y"] / 1000
    centroids["z"] = centroids["z"] / 1000
    centroids["size"] = (centroids["count"] / centroids["count"].max() * 25 + 6).clip(6, 30)

    fig = go.Figure()
    for tank, color in TANK_COLORS.items():
        sub = centroids[centroids["tank"] == tank]
        if len(sub) == 0:
            continue
        fig.add_trace(go.Scatter3d(
            x=sub["x"], y=sub["y"], z=sub["z"],
            mode="markers+text",
            name=tank,
            text=sub["layer"],
            textposition="top center",
            marker=dict(size=sub["size"], color=color, opacity=0.8,
                        line=dict(width=0.5, color="white")),
            hovertemplate=(
                "<b>%{text}</b><br>"
                f"Tanque: {tank}<br>"
                "Neuronas: %{customdata:,}<br>"
                "x=%{x:.0f}µm y=%{y:.0f}µm z=%{z:.0f}µm<extra></extra>"
            ),
            customdata=sub["count"],
        ))

    fig.update_layout(
        title=dict(
            text="🐭 MICrONS Mouse V1 Cortex — SEAL Motivational Tanks (Fase 3.2)",
            font=dict(size=17, color="white"),
        ),
        paper_bgcolor="#0D0D0D",
        scene=dict(
            xaxis=dict(backgroundcolor="#111", gridcolor="#333", color="white", title="X (µm)"),
            yaxis=dict(backgroundcolor="#111", gridcolor="#333", color="white", title="Y (µm)"),
            zaxis=dict(backgroundcolor="#111", gridcolor="#333", color="white", title="Z (µm)"),
            bgcolor="#111",
        ),
        legend=dict(font=dict(color="white"), bgcolor="rgba(0,0,0,0.5)"),
        margin=dict(l=0, r=0, b=0, t=50),
    )
    out = OUT_DIR / "microns_3d_seal_tanks.html"
    fig.write_html(str(out))
    print(f"\n✅ MICrONS 3D exportado: {out}")
    return out, centroids


def build_comparative_chart(neurons_microns):
    """Gráfico comparativo: distribución de tanques Drosophila vs Ratón."""
    # MICrONS
    microns_pct = neurons_microns["tank"].value_counts(normalize=True) * 100

    # FlyWire (datos del análisis previo)
    flywire_tank_syns = {
        "curiosity":    107_000_000,  # FB + SMP
        "task_drive":   2_700_000,    # GNG
        "social_drive": 18_000_000,   # AVLP
        "alert_drive":  2_700_000,    # GNG + AL (compartido)
        "unmapped":     0,
    }
    total_fly = sum(flywire_tank_syns.values())
    flywire_pct = {k: v / total_fly * 100 for k, v in flywire_tank_syns.items()}

    tanks = ["curiosity", "task_drive", "social_drive", "alert_drive", "inhibitory"]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="🪰 Drosophila (FlyWire)", x=tanks,
        y=[flywire_pct.get(t, 0) for t in tanks],
        marker_color=[TANK_COLORS[t] for t in tanks],
        opacity=0.6,
    ))
    fig.add_trace(go.Bar(
        name="🐭 Ratón (MICrONS)", x=tanks,
        y=[microns_pct.get(t, 0) for t in tanks],
        marker_color=[TANK_COLORS[t] for t in tanks],
        marker_line=dict(width=2, color="white"),
        opacity=0.9,
    ))
    fig.update_layout(
        title="Distribución de Tanques SEAL: Drosophila vs Mouse V1 Cortex",
        barmode="group",
        paper_bgcolor="#0D0D0D",
        plot_bgcolor="#111",
        font=dict(color="white"),
        legend=dict(font=dict(color="white"), bgcolor="rgba(0,0,0,0.5)"),
        yaxis_title="% del total",
        xaxis_title="Tanque motivacional SEAL",
    )
    out = OUT_DIR / "comparative_flyvsrat_tanks.html"
    fig.write_html(str(out))
    print(f"✅ Comparativa exportada: {out}")
    return out


def main():
    print("=== SEAL Fase 3.2 — MICrONS Mouse V1 → Tanques Motivacionales ===\n")

    neurons, edges = load_microns()
    coupling = compute_tank_coupling(neurons, edges)
    out_3d, centroids = build_3d_comparison(neurons)
    out_comp = build_comparative_chart(neurons)

    # Inhibitory ratio por tanque
    print("\n=== INHIBITORY RATIO POR TANQUE ===")
    inhib_neurons = neurons[neurons["tank"] == "inhibitory"]
    for tank in TANK_COLORS:
        if tank in ("inhibitory", "unmapped"):
            continue
        tank_neurons = neurons[neurons["tank"] == tank]
        total = len(tank_neurons)
        if total == 0:
            continue
        # Conexiones inhibitorias hacia este tanque
        tank_indices = set(tank_neurons.index.tolist())
        inhib_indices = set(inhib_neurons.index.tolist())
        inhib_to_tank = edges[
            (edges["pre"].isin(inhib_indices)) & (edges["post"].isin(tank_indices))
        ]
        all_to_tank = edges[edges["post"].isin(tank_indices)]
        ratio = len(inhib_to_tank) / len(all_to_tank) if len(all_to_tank) > 0 else 0
        print(f"  {tank}: inhib_ratio={ratio:.3f} ({len(inhib_to_tank):,} inhib / {len(all_to_tank):,} total)")

    # Guardar resultados
    results = {
        "dataset": "MICrONS mm3 v1181",
        "neurons": int(len(neurons)),
        "connections": int(len(edges)),
        "layer_tank_map": LAYER_TANK,
        "coupling": coupling,
        "tank_distribution": neurons["tank"].value_counts().to_dict(),
        "date": "2026-04-18",
    }
    with open(OUT_DIR / "microns_phase32_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n✅ Resultados guardados: {OUT_DIR}/microns_phase32_results.json")

    print(f"\n=== ARCHIVOS GENERADOS ===")
    print(f"  3D: http://100.75.201.110:8090/microns_3d_seal_tanks.html")
    print(f"  Comparativa: http://100.75.201.110:8090/comparative_flyvsrat_tanks.html")


if __name__ == "__main__":
    main()
