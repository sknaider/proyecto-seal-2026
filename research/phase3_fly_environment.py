#!/usr/bin/env python3
"""
phase3_fly_environment.py — SEAL Fase 3: Simulación Entorno Virtual Drosophila
SEAL Team | ADA — 2026-04-18

Simula un entorno 2D donde la mosca navega respondiendo a estímulos,
y mapea esos estímulos a los tanques motivacionales SEAL.

No requiere MuJoCo/flygym — es un modelo simplificado pero funcional
que captura la dinámica motivacional esencial.

Tanques → respuestas de estímulo:
  curiosity   → movimiento de objetos nuevos (exploración)
  task_drive  → olor a alimento (navegación dirigida)
  social_drive→ proximidad a otras moscas (agregación)
  alert_drive → sombra rápida (predador) o contacto brusco
"""

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
from pathlib import Path

OUT_DIR = Path(__file__).parent / "flywire_results"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Parámetros del entorno
ENV_SIZE = 200          # mm
DT = 0.1                # s por tick
T_TOTAL = 60.0          # s simulación
N_TICKS = int(T_TOTAL / DT)

# Calibración LIF inter-tank (de FlyWire Fase 2)
TANK_INHIB = {
    "curiosity":    0.114,
    "task_drive":   0.216,
    "social_drive": 0.223,
    "alert_drive":  0.231,
}
TANK_TAU = {
    "curiosity":    8.0,    # s — decae lento (exploración persistente)
    "task_drive":   5.0,    # s
    "social_drive": 6.0,    # s
    "alert_drive":  2.0,    # s — decae rápido (alerta fugaz)
}

TANK_COLORS = {
    "curiosity":    "#4A90E2",
    "task_drive":   "#27AE60",
    "social_drive": "#E67E22",
    "alert_drive":  "#E74C3C",
}


class FlyEnv:
    """Entorno virtual 2D simplificado para Drosophila."""

    def __init__(self, seed=42):
        rng = np.random.default_rng(seed)

        # Estado de la mosca
        self.pos = np.array([ENV_SIZE / 2, ENV_SIZE / 2], dtype=float)
        self.angle = rng.uniform(0, 2 * np.pi)
        self.speed = 1.5   # mm/s basal

        # Estímulos en el entorno (posición, tipo, intensidad)
        self.food_sources = rng.uniform(20, 180, (3, 2))       # 3 fuentes de alimento
        self.other_flies = rng.uniform(20, 180, (5, 2))         # 5 moscas compañeras
        self.novel_objects = rng.uniform(20, 180, (4, 2))       # 4 objetos nuevos

        # Tanques motivacionales — nivel inicial
        self.tanks = {k: 0.1 for k in TANK_INHIB}

        # Historia
        self.history_pos = [self.pos.copy()]
        self.history_tanks = {k: [self.tanks[k]] for k in self.tanks}
        self.history_speed = [self.speed]

    def compute_stimuli(self):
        """Calcula input de estímulo a cada tanque basado en distancias."""
        stimuli = {}

        # curiosity: objetos nuevos dentro de ~30mm (rango visual)
        dists_obj = [np.linalg.norm(self.pos - o) for o in self.novel_objects]
        stimuli["curiosity"] = sum(max(0, 1 - d / 30) for d in dists_obj)

        # task_drive: alimento dentro de ~50mm (gradiente olfativo)
        dists_food = [np.linalg.norm(self.pos - f) for f in self.food_sources]
        stimuli["task_drive"] = sum(max(0, 1 - d / 50) for d in dists_food)

        # social_drive: otras moscas dentro de ~20mm
        dists_fly = [np.linalg.norm(self.pos - f) for f in self.other_flies]
        stimuli["social_drive"] = sum(max(0, 1 - d / 20) for d in dists_fly)

        # alert_drive: estímulo periódico simulando predador (cada ~10s)
        # → impulso aleatorio
        stimuli["alert_drive"] = 0.0

        return stimuli

    def update_tanks(self, stimuli, dt):
        """LIF motivacional: integra estímulo, decae con tau, inhibición cruzada."""
        GNG_coupling = 0.3   # GNG compartido: task↔alert coupling (FlyWire)

        for tank, stim in stimuli.items():
            tau = TANK_TAU[tank]
            inhib = TANK_INHIB[tank]
            # Integración LIF
            self.tanks[tank] += dt * (-self.tanks[tank] / tau + stim * (1 - inhib))
            self.tanks[tank] = max(0.0, min(1.0, self.tanks[tank]))

        # Coupling GNG: task_drive ↔ alert_drive (compartido en FlyWire)
        coupling_effect = GNG_coupling * self.tanks["task_drive"] * 0.1
        self.tanks["alert_drive"] = min(1.0, self.tanks["alert_drive"] + coupling_effect)

    def move(self, dt):
        """Mueve la mosca según el tanque dominante."""
        dom_tank = max(self.tanks, key=self.tanks.get)
        dom_level = self.tanks[dom_tank]

        if dom_tank == "task_drive":
            # Navegar hacia la fuente de alimento más cercana
            dists = [np.linalg.norm(self.pos - f) for f in self.food_sources]
            target = self.food_sources[np.argmin(dists)]
            direction = target - self.pos
            norm = np.linalg.norm(direction)
            if norm > 0:
                self.angle = 0.7 * self.angle + 0.3 * np.arctan2(direction[1], direction[0])
            self.speed = 1.5 + dom_level * 3.0

        elif dom_tank == "alert_drive":
            # Movimiento errático de escape
            self.angle += np.random.uniform(-1.5, 1.5)
            self.speed = 3.0 + dom_level * 5.0

        elif dom_tank == "curiosity":
            # Exploración — giro lento hacia objetos
            dists = [np.linalg.norm(self.pos - o) for o in self.novel_objects]
            target = self.novel_objects[np.argmin(dists)]
            direction = target - self.pos
            norm = np.linalg.norm(direction)
            if norm > 0:
                self.angle = 0.9 * self.angle + 0.1 * np.arctan2(direction[1], direction[0])
            self.speed = 1.0 + dom_level * 1.5

        else:  # social_drive
            # Agregación — moverse hacia otras moscas
            dists = [np.linalg.norm(self.pos - f) for f in self.other_flies]
            target = self.other_flies[np.argmin(dists)]
            direction = target - self.pos
            norm = np.linalg.norm(direction)
            if norm > 0:
                self.angle = 0.85 * self.angle + 0.15 * np.arctan2(direction[1], direction[0])
            self.speed = 1.2

        # Mover
        dx = np.cos(self.angle) * self.speed * dt
        dy = np.sin(self.angle) * self.speed * dt
        self.pos = np.clip(self.pos + [dx, dy], 5, ENV_SIZE - 5)

    def step(self, t, dt):
        """Un tick de simulación."""
        stimuli = self.compute_stimuli()

        # Evento de predador aleatorio (baja probabilidad)
        if np.random.random() < 0.01:
            stimuli["alert_drive"] = 1.5

        self.update_tanks(stimuli, dt)
        self.move(dt)

        self.history_pos.append(self.pos.copy())
        for k in self.tanks:
            self.history_tanks[k].append(self.tanks[k])
        self.history_speed.append(self.speed)


def run_simulation():
    print("=== SEAL Fase 3 — Simulación Entorno Virtual Drosophila ===")
    env = FlyEnv(seed=2026)

    print(f"Simulando {T_TOTAL}s de vida de mosca ({N_TICKS} ticks a dt={DT}s)...")
    for tick in range(N_TICKS):
        env.step(tick * DT, DT)
        if tick % 100 == 0:
            dom = max(env.tanks, key=env.tanks.get)
            print(f"  t={tick*DT:.0f}s | pos=({env.pos[0]:.0f},{env.pos[1]:.0f}) | dom={dom} ({env.tanks[dom]:.3f})")

    print("\nGenerando visualización...")
    times = np.arange(N_TICKS + 1) * DT
    pos_arr = np.array(env.history_pos)

    fig = make_subplots(
        rows=2, cols=2,
        specs=[
            [{"type": "xy", "colspan": 1}, {"type": "xy"}],
            [{"type": "xy", "colspan": 2}, None],
        ],
        subplot_titles=[
            "Trayectoria en entorno 2D",
            "Tanques motivacionales en el tiempo",
            "Dominancia de tanque",
        ],
        vertical_spacing=0.12,
        horizontal_spacing=0.08,
    )

    # Plot 1: trayectoria
    fig.add_trace(go.Scatter(
        x=pos_arr[:, 0], y=pos_arr[:, 1],
        mode="lines",
        name="Trayectoria",
        line=dict(color="white", width=1),
        opacity=0.6,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[pos_arr[-1, 0]], y=[pos_arr[-1, 1]],
        mode="markers", name="Posición final",
        marker=dict(color="yellow", size=10, symbol="star"),
    ), row=1, col=1)
    # Estímulos
    for i, f in enumerate(env.food_sources):
        fig.add_trace(go.Scatter(
            x=[f[0]], y=[f[1]], mode="markers+text",
            text=["🌿"] if i == 0 else [None],
            name="Alimento" if i == 0 else None,
            marker=dict(color=TANK_COLORS["task_drive"], size=15, symbol="circle"),
            showlegend=(i == 0),
        ), row=1, col=1)

    # Plot 2: tanques en el tiempo
    for tank, color in TANK_COLORS.items():
        fig.add_trace(go.Scatter(
            x=times, y=env.history_tanks[tank],
            mode="lines", name=tank,
            line=dict(color=color, width=2),
        ), row=1, col=2)

    # Plot 3: tanque dominante (líneas rellenas)
    tank_arr = np.array([env.history_tanks[k] for k in TANK_COLORS])
    hex_to_rgba = lambda h, a: f"rgba({int(h[1:3],16)},{int(h[3:5],16)},{int(h[5:7],16)},{a})"
    for i, (tank, color) in enumerate(TANK_COLORS.items()):
        fig.add_trace(go.Scatter(
            x=times, y=tank_arr[i],
            mode="lines", name=f"{tank} (dom)",
            fill="tozeroy",
            line=dict(color=color, width=1.5),
            fillcolor=hex_to_rgba(color, 0.25),
            showlegend=False,
        ), row=2, col=1)

    fig.update_layout(
        title="🪰 SEAL Fase 3 — Drosophila en Entorno Virtual (Tanques Motivacionales)",
        paper_bgcolor="#0D0D0D",
        plot_bgcolor="#111",
        font=dict(color="white"),
        legend=dict(bgcolor="rgba(0,0,0,0.5)", font=dict(color="white")),
        height=700,
    )
    for axis in ["xaxis", "yaxis", "xaxis2", "yaxis2", "xaxis3", "yaxis3"]:
        fig.update_layout(**{axis: dict(gridcolor="#333", color="white")})

    out = OUT_DIR / "fly_environment_sim.html"
    fig.write_html(str(out))
    print(f"\n✅ Simulación exportada: {out}")

    # Estadísticas finales
    dominance = {}
    for tick in range(N_TICKS):
        vals = {k: env.history_tanks[k][tick] for k in env.tanks}
        dom = max(vals, key=vals.get)
        dominance[dom] = dominance.get(dom, 0) + 1

    stats = {
        "duration_s": T_TOTAL,
        "ticks": N_TICKS,
        "dominance_pct": {k: round(v / N_TICKS * 100, 1) for k, v in dominance.items()},
        "tank_calibration": TANK_INHIB,
        "date": "2026-04-18",
        "note": "LIF inter-tank coupling calibrado desde FlyWire v783 GNG coupling",
    }
    with open(OUT_DIR / "fly_sim_stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    print(f"\nDominancia de tanque:")
    for k, v in sorted(dominance.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v/N_TICKS*100:.1f}% del tiempo")

    return out


if __name__ == "__main__":
    run_simulation()
