#!/usr/bin/env python3
"""
phase3_fullbrain_3d.py — SEAL Fase 3: Visualización Full-Brain Drosophila en 3D
SEAL Team | ADA — 2026-04-18

Genera un mapa 3D interactivo del cerebro completo de Drosophila (FlyWire v783),
coloreado por tanque motivacional SEAL. Archivo HTML standalone.

Tanques SEAL → Neuropils:
  curiosity   → FB, SMP  (azul)
  task_drive  → GNG       (verde)
  social_drive→ AVLP      (naranja)
  alert_drive → GNG, AL   (rojo)
  unmapped    → gris
"""

import pandas as pd
import plotly.graph_objects as go
import json
from pathlib import Path

FLYWIRE_DIR = Path.home() / "IA/datasets/flywire"
OUT_DIR = Path.home() / "IA/proyecto-seal/research/flywire_results"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TANK_COLORS = {
    "curiosity":    "#4A90E2",   # azul
    "task_drive":   "#27AE60",   # verde
    "social_drive": "#E67E22",   # naranja
    "alert_drive":  "#E74C3C",   # rojo
    "unmapped":     "#95A5A6",   # gris
}

TANK_NEUROPILS = {
    "curiosity":    ["FB", "SMP"],
    "task_drive":   ["GNG"],
    "social_drive": ["AVLP"],
    "alert_drive":  ["GNG", "AL"],
}


def neuropil_to_tank(neuropil: str) -> str:
    np_upper = neuropil.upper()
    for tank, prefixes in TANK_NEUROPILS.items():
        for prefix in prefixes:
            if np_upper.startswith(prefix):
                return tank
    return "unmapped"


def main():
    print("=== SEAL Fase 3 — Full-Brain 3D Drosophila ===")

    print("Cargando sinapsis con coordenadas 3D...")
    df = pd.read_feather(str(FLYWIRE_DIR / "flywire_synapses_783.feather"))
    print(f"  {len(df):,} sinapsis cargadas")

    # Calcular centroide por neuropil (media de posiciones pre)
    print("Calculando centroides por neuropil...")
    centroids = df.groupby("neuropil").agg(
        x=("pre_pt_position_x", "mean"),
        y=("pre_pt_position_y", "mean"),
        z=("pre_pt_position_z", "mean"),
        count=("id", "count"),
    ).reset_index()

    centroids["tank"] = centroids["neuropil"].apply(neuropil_to_tank)
    centroids["color"] = centroids["tank"].map(TANK_COLORS)
    centroids["size"] = (centroids["count"] / centroids["count"].max() * 30 + 4).clip(4, 35)

    print(f"  {len(centroids)} neuropils mapeados")
    print(f"  Distribución por tanque:\n{centroids['tank'].value_counts().to_string()}")

    # Construir figura plotly
    fig = go.Figure()

    for tank in list(TANK_COLORS.keys()):
        sub = centroids[centroids["tank"] == tank]
        if len(sub) == 0:
            continue
        fig.add_trace(go.Scatter3d(
            x=sub["x"],
            y=sub["y"],
            z=sub["z"],
            mode="markers+text",
            name=tank,
            text=sub["neuropil"],
            textposition="top center",
            marker=dict(
                size=sub["size"],
                color=TANK_COLORS[tank],
                opacity=0.75 if tank != "unmapped" else 0.25,
                line=dict(width=0.5, color="white"),
            ),
            hovertemplate=(
                "<b>%{text}</b><br>"
                f"Tanque: {tank}<br>"
                "Sinapsis: %{customdata:,}<br>"
                "x=%{x:.0f} y=%{y:.0f} z=%{z:.0f}<extra></extra>"
            ),
            customdata=sub["count"],
        ))

    fig.update_layout(
        title=dict(
            text="🧠 Drosophila Full-Brain — SEAL Motivational Tanks (FlyWire v783)",
            font=dict(size=18, color="white"),
        ),
        paper_bgcolor="#0D0D0D",
        plot_bgcolor="#0D0D0D",
        scene=dict(
            xaxis=dict(backgroundcolor="#111", gridcolor="#333", color="white", title="X (nm)"),
            yaxis=dict(backgroundcolor="#111", gridcolor="#333", color="white", title="Y (nm)"),
            zaxis=dict(backgroundcolor="#111", gridcolor="#333", color="white", title="Z (nm)"),
            bgcolor="#111",
        ),
        legend=dict(
            font=dict(color="white"),
            bgcolor="rgba(0,0,0,0.5)",
        ),
        margin=dict(l=0, r=0, b=0, t=50),
    )

    out_html = OUT_DIR / "fullbrain_3d_seal_tanks.html"
    fig.write_html(str(out_html))
    print(f"\n✅ Cerebro 3D exportado: {out_html}")
    print("   Abre en navegador para visualización interactiva.")

    # Guardar stats
    stats = {
        "neuropils_total": len(centroids),
        "by_tank": centroids["tank"].value_counts().to_dict(),
        "synapses_total": int(df["id"].count()),
        "date": "2026-04-18",
        "source": "FlyWire v783 flywire_synapses_783.feather",
    }
    with open(OUT_DIR / "fullbrain_stats.json", "w") as f:
        json.dump(stats, f, indent=2)
    print(f"   Stats: {OUT_DIR}/fullbrain_stats.json")

    return out_html


if __name__ == "__main__":
    main()
