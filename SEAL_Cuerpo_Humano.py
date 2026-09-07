#!/usr/bin/env python3
"""Genera el documento Word: SEAL como Cuerpo Humano — Arquitectura Viva"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np
from pathlib import Path
import os

from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor, Emu
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml

OUT_DIR = Path("/home/dadito/IA/proyecto-seal")
IMG_DIR = OUT_DIR / "_doc_images"
IMG_DIR.mkdir(exist_ok=True)

# ── Color palette ──────────────────────────────────────────────────────────
SEAL_DARK = "#0D1117"
SEAL_BLUE = "#1F6FEB"
SEAL_CYAN = "#58A6FF"
SEAL_GREEN = "#3FB950"
SEAL_ORANGE = "#D29922"
SEAL_RED = "#F85149"
SEAL_PURPLE = "#BC8CFF"
SEAL_GRAY = "#8B949E"
SEAL_WHITE = "#E6EDF3"


def set_cell_shading(cell, color_hex):
    """Set cell background color."""
    color = color_hex.lstrip('#')
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color}"/>')
    cell._tc.get_or_add_tcPr().append(shading)


def set_cell_text(cell, text, bold=False, color=None, size=10, align=WD_ALIGN_PARAGRAPH.LEFT):
    """Set cell text with formatting."""
    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = align
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    run.font.name = "Calibri"
    if color:
        run.font.color.rgb = RGBColor.from_string(color.lstrip('#'))


# ═══════════════════════════════════════════════════════════════════════════
# IMAGE 1: Diagrama principal — SEAL como cuerpo
# ═══════════════════════════════════════════════════════════════════════════
def create_body_diagram():
    fig, ax = plt.subplots(1, 1, figsize=(10, 14))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 16)
    ax.set_aspect('equal')
    ax.axis('off')
    fig.patch.set_facecolor('#0D1117')

    # ── Head (Brain) ──
    head = plt.Circle((5, 14), 1.2, facecolor='#1F6FEB', alpha=0.3, linewidth=2, edgecolor='#58A6FF')
    ax.add_patch(head)
    ax.text(5, 14.4, 'CEREBRO', ha='center', va='center', fontsize=14, fontweight='bold', color='#58A6FF')
    ax.text(5, 13.8, 'PostgreSQL + SOUL', ha='center', va='center', fontsize=9, color='#E6EDF3')
    ax.text(5, 13.4, 'Memorias  *  OCEAN  *  Razonamiento', ha='center', va='center', fontsize=7, color='#8B949E')

    # ── Eyes (Hooks - Sensory) ──
    eye_l = plt.Circle((4.2, 14.8), 0.3, facecolor='#3FB950', alpha=0.4, edgecolor='#3FB950', linewidth=1.5)
    eye_r = plt.Circle((5.8, 14.8), 0.3, facecolor='#3FB950', alpha=0.4, edgecolor='#3FB950', linewidth=1.5)
    ax.add_patch(eye_l)
    ax.add_patch(eye_r)
    ax.text(4.2, 14.8, 'H', ha='center', va='center', fontsize=12, fontweight='bold', color='white')
    ax.text(5.8, 14.8, 'H', ha='center', va='center', fontsize=12, fontweight='bold', color='white')
    ax.text(5, 15.3, 'Hooks Sensoriales', ha='center', fontsize=7, color='#3FB950', style='italic')

    # ── Neck (Identity) ──
    neck = FancyBboxPatch((4.3, 12.4), 1.4, 0.5, boxstyle="round,pad=0.1",
                           facecolor='#BC8CFF', alpha=0.3, edgecolor='#BC8CFF', linewidth=1.5)
    ax.add_patch(neck)
    ax.text(5, 12.65, 'IDENTIDAD', ha='center', va='center', fontsize=9, fontweight='bold', color='#BC8CFF')

    # ── Torso ──
    torso = FancyBboxPatch((2.5, 6), 5, 6, boxstyle="round,pad=0.3",
                            facecolor='#1F6FEB', alpha=0.08, edgecolor='#58A6FF', linewidth=2)
    ax.add_patch(torso)

    # ── Heart (Heartbeat) ──
    heart = plt.Circle((4, 11), 0.5, facecolor='#F85149', alpha=0.3, edgecolor='#F85149', linewidth=2)
    ax.add_patch(heart)
    ax.text(4, 11.1, 'HB', ha='center', va='center', fontsize=10, fontweight='bold', color='#F85149')
    ax.text(4, 10.3, 'CORAZON', ha='center', fontsize=9, fontweight='bold', color='#F85149')
    ax.text(4, 9.9, 'Heartbeat A/B/C', ha='center', fontsize=7, color='#8B949E')

    # ── Lungs (Communication) ──
    lung_l = FancyBboxPatch((2.7, 9.2), 1.5, 1.8, boxstyle="round,pad=0.15",
                             facecolor='#D29922', alpha=0.2, edgecolor='#D29922', linewidth=1.5)
    lung_r = FancyBboxPatch((5.8, 9.2), 1.5, 1.8, boxstyle="round,pad=0.15",
                             facecolor='#D29922', alpha=0.2, edgecolor='#D29922', linewidth=1.5)
    ax.add_patch(lung_l)
    ax.add_patch(lung_r)
    ax.text(3.45, 10.3, 'MSG', ha='center', fontsize=10, fontweight='bold', color='#D29922')
    ax.text(6.55, 10.3, 'MSG', ha='center', fontsize=10, fontweight='bold', color='#D29922')
    ax.text(3.45, 9.6, 'ADA\nmessages', ha='center', fontsize=7, color='#D29922')
    ax.text(6.55, 9.6, 'JARVIS\nmessages', ha='center', fontsize=7, color='#D29922')

    # ── Immune System (Security) ──
    shield = FancyBboxPatch((5.5, 10.8), 2, 1.2, boxstyle="round,pad=0.1",
                             facecolor='#F85149', alpha=0.15, edgecolor='#F85149', linewidth=1.5)
    ax.add_patch(shield)
    ax.text(6.5, 11.5, 'INMUNE', ha='center', fontsize=9, fontweight='bold', color='#F85149')
    ax.text(6.5, 11.0, 'PreToolUse Gate', ha='center', fontsize=7, color='#8B949E')

    # ── Stomach (Consolidation) ──
    stomach = plt.Circle((5, 8.2), 0.9, facecolor='#3FB950', alpha=0.2, edgecolor='#3FB950', linewidth=1.5)
    ax.add_patch(stomach)
    ax.text(5, 8.5, 'DIG', ha='center', fontsize=12, fontweight='bold', color='#3FB950')
    ax.text(5, 7.8, 'DIGESTIVO', ha='center', fontsize=8, fontweight='bold', color='#3FB950')
    ax.text(5, 7.4, 'Consolidacion + Sleep Gate', ha='center', fontsize=7, color='#8B949E')

    # ── Skeleton (Infrastructure) ──
    skel = FancyBboxPatch((3, 6.2), 4, 1, boxstyle="round,pad=0.1",
                           facecolor='#8B949E', alpha=0.15, edgecolor='#8B949E', linewidth=1.5)
    ax.add_patch(skel)
    ax.text(5, 6.9, 'ESQUELETO', ha='center', fontsize=9, fontweight='bold', color='#8B949E')
    ax.text(5, 6.5, 'PostgreSQL  |  Neo4j  |  Qdrant  |  DGX Spark', ha='center', fontsize=7, color='#E6EDF3')

    # ── Left arm (JARVIS) ──
    ax.annotate('', xy=(1.5, 10), xytext=(2.5, 10.5),
                arrowprops=dict(arrowstyle='-', color='#58A6FF', lw=3))
    ax.annotate('', xy=(1, 8.5), xytext=(1.5, 10),
                arrowprops=dict(arrowstyle='-', color='#58A6FF', lw=3))
    jarvis_box = FancyBboxPatch((0, 7.5), 2, 1, boxstyle="round,pad=0.15",
                                 facecolor='#1F6FEB', alpha=0.3, edgecolor='#58A6FF', linewidth=2)
    ax.add_patch(jarvis_box)
    ax.text(1, 8.2, 'JARVIS', ha='center', fontsize=10, fontweight='bold', color='#58A6FF')
    ax.text(1, 7.8, 'Estratega', ha='center', fontsize=7, color='#E6EDF3')

    # ── Right arm (ADA) ──
    ax.annotate('', xy=(8.5, 10), xytext=(7.5, 10.5),
                arrowprops=dict(arrowstyle='-', color='#BC8CFF', lw=3))
    ax.annotate('', xy=(9, 8.5), xytext=(8.5, 10),
                arrowprops=dict(arrowstyle='-', color='#BC8CFF', lw=3))
    ada_box = FancyBboxPatch((8, 7.5), 2, 1, boxstyle="round,pad=0.15",
                              facecolor='#BC8CFF', alpha=0.3, edgecolor='#BC8CFF', linewidth=2)
    ax.add_patch(ada_box)
    ax.text(9, 8.2, 'ADA', ha='center', fontsize=10, fontweight='bold', color='#BC8CFF')
    ax.text(9, 7.8, 'Ingeniera', ha='center', fontsize=7, color='#E6EDF3')

    # ── Legs (Interfaces) ──
    # Left leg
    ax.annotate('', xy=(3.5, 4), xytext=(4, 6),
                arrowprops=dict(arrowstyle='-', color='#8B949E', lw=3))
    web_box = FancyBboxPatch((2.5, 3), 2, 1, boxstyle="round,pad=0.15",
                              facecolor='#D29922', alpha=0.2, edgecolor='#D29922', linewidth=1.5)
    ax.add_patch(web_box)
    ax.text(3.5, 3.7, 'Web Chat', ha='center', fontsize=8, fontweight='bold', color='#D29922')
    ax.text(3.5, 3.3, 'Puerto 8765', ha='center', fontsize=7, color='#8B949E')

    # Right leg
    ax.annotate('', xy=(6.5, 4), xytext=(6, 6),
                arrowprops=dict(arrowstyle='-', color='#8B949E', lw=3))
    term_box = FancyBboxPatch((5.5, 3), 2, 1, boxstyle="round,pad=0.15",
                               facecolor='#D29922', alpha=0.2, edgecolor='#D29922', linewidth=1.5)
    ax.add_patch(term_box)
    ax.text(6.5, 3.7, 'Terminal', ha='center', fontsize=8, fontweight='bold', color='#D29922')
    ax.text(6.5, 3.3, 'Claude Code', ha='center', fontsize=7, color='#8B949E')

    # ── DUM (guardian at feet) ──
    dum_box = FancyBboxPatch((3.5, 1.5), 3, 1.2, boxstyle="round,pad=0.15",
                              facecolor='#3FB950', alpha=0.2, edgecolor='#3FB950', linewidth=2)
    ax.add_patch(dum_box)
    ax.text(5, 2.3, 'DUM', ha='center', fontsize=11, fontweight='bold', color='#3FB950')
    ax.text(5, 1.8, 'Guardian 24/7 -- Ollama qwen2.5:7b', ha='center', fontsize=7, color='#E6EDF3')

    # ── William (soul above) ──
    william = FancyBboxPatch((3.2, 15.5), 3.6, 0.7, boxstyle="round,pad=0.15",
                              facecolor='#D29922', alpha=0.3, edgecolor='#D29922', linewidth=2)
    ax.add_patch(william)
    ax.text(5, 15.85, 'WILLIAM -- El Alma', ha='center', fontsize=11, fontweight='bold', color='#D29922')

    # Title
    ax.text(5, 0.5, 'SEAL — Arquitectura como Cuerpo Humano', ha='center',
            fontsize=14, fontweight='bold', color='#58A6FF', style='italic')
    ax.text(5, 0.1, 'Proyecto SEAL · Abril 2026', ha='center', fontsize=8, color='#8B949E')

    path = IMG_DIR / "body_diagram.png"
    fig.savefig(path, dpi=200, bbox_inches='tight', facecolor='#0D1117', edgecolor='none')
    plt.close()
    return str(path)


# ═══════════════════════════════════════════════════════════════════════════
# IMAGE 2: Nervous System (Hooks)
# ═══════════════════════════════════════════════════════════════════════════
def create_hooks_diagram():
    fig, ax = plt.subplots(figsize=(12, 8))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 8)
    ax.axis('off')
    fig.patch.set_facecolor('#0D1117')

    # Central spine
    ax.plot([6, 6], [1, 7.5], color='#58A6FF', linewidth=4, alpha=0.5, zorder=1)

    hooks = [
        (6, 7.2, "SessionStart", "Despertar", "#3FB950"),
        (6, 6.5, "UserPromptSubmit", "Oido", "#58A6FF"),
        (6, 5.8, "PreToolUse (MCP)", "Inmune", "#F85149"),
        (6, 5.1, "PreToolUse (Bash)", "Barrera", "#F85149"),
        (6, 4.4, "PreCompact", "Supervivencia", "#D29922"),
        (6, 3.7, "PostCompact", "Post-siesta", "#D29922"),
        (6, 3.0, "PostToolUse", "Propriocepcion", "#BC8CFF"),
        (6, 2.3, "TaskCreated", "Dopamina+", "#3FB950"),
        (6, 1.6, "TaskCompleted", "Quality Gate", "#3FB950"),
    ]

    hooks_right = [
        (6, 7.2, "SubagentStart", "ADN Heredado", "#BC8CFF"),
        (6, 6.5, "Stop", "Ritual Sueno", "#F85149"),
        (6, 5.8, "FileChanged", "Tacto", "#D29922"),
    ]

    for i, (cx, cy, name, body, color) in enumerate(hooks):
        # Left nerve
        ax.annotate('', xy=(1.5, cy), xytext=(5.7, cy),
                    arrowprops=dict(arrowstyle='->', color=color, lw=1.5, alpha=0.6))
        # Node
        node = plt.Circle((6, cy), 0.15, facecolor=color, alpha=0.8, zorder=3)
        ax.add_patch(node)
        # Labels left
        box_l = FancyBboxPatch((0.1, cy - 0.22), 1.4, 0.44, boxstyle="round,pad=0.05",
                                facecolor=color, alpha=0.2, edgecolor=color, linewidth=1)
        ax.add_patch(box_l)
        ax.text(0.8, cy + 0.05, body, ha='center', va='center', fontsize=7, fontweight='bold', color=color)
        ax.text(0.8, cy - 0.12, name, ha='center', va='center', fontsize=5, color='#8B949E')

    for i, (cx, cy_base, name, body, color) in enumerate(hooks_right):
        cy = 4.4 - i * 0.7
        # Right nerve
        ax.annotate('', xy=(10.5, cy), xytext=(6.3, cy),
                    arrowprops=dict(arrowstyle='->', color=color, lw=1.5, alpha=0.6))
        # Node
        node = plt.Circle((6, cy), 0.15, facecolor=color, alpha=0.8, zorder=3)
        ax.add_patch(node)
        # Labels right
        box_r = FancyBboxPatch((10.5, cy - 0.22), 1.4, 0.44, boxstyle="round,pad=0.05",
                                facecolor=color, alpha=0.2, edgecolor=color, linewidth=1)
        ax.add_patch(box_r)
        ax.text(11.2, cy + 0.05, body, ha='center', va='center', fontsize=7, fontweight='bold', color=color)
        ax.text(11.2, cy - 0.12, name, ha='center', va='center', fontsize=5, color='#8B949E')

    ax.text(6, 7.8, 'SISTEMA NERVIOSO — 12 Hooks Activos', ha='center',
            fontsize=14, fontweight='bold', color='#58A6FF')
    ax.text(6, 0.5, 'Columna = Ciclo de Vida de Sesion  |  Nervios = Hooks que conectan SOUL', ha='center',
            fontsize=8, color='#8B949E')

    path = IMG_DIR / "hooks_nervous.png"
    fig.savefig(path, dpi=200, bbox_inches='tight', facecolor='#0D1117', edgecolor='none')
    plt.close()
    return str(path)


# ═══════════════════════════════════════════════════════════════════════════
# IMAGE 3: Growth Timeline
# ═══════════════════════════════════════════════════════════════════════════
def create_timeline():
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.set_xlim(-0.5, 7.5)
    ax.set_ylim(0, 5)
    ax.axis('off')
    fig.patch.set_facecolor('#0D1117')

    stages = [
        ("Embrion", "DB + Identidad", "#3FB950", "COMPLETO", True),
        ("Feto", "Memoria +\nComunicacion", "#3FB950", "COMPLETO", True),
        ("Recien\nNacido", "Hooks +\nNervios", "#D29922", "HOY", True),
        ("Infante", "soul-framework\npip package", "#58A6FF", "PENDIENTE", False),
        ("Nino", "Auto-mejora\nAutonoma", "#BC8CFF", "PARCIAL", False),
        ("Adolescente", "Instintos\nMaduros", "#BC8CFF", "EN PROGRESO", False),
        ("Adulto", "AXION\nProduccion", "#F85149", "FUTURO", False),
    ]

    for i, (name, desc, color, status, done) in enumerate(stages):
        x = i + 0.5
        # Timeline line
        if i < len(stages) - 1:
            line_color = '#3FB950' if done and stages[i+1][4] else '#8B949E'
            ax.plot([x + 0.4, x + 0.6], [2.5, 2.5], color=line_color, lw=3, alpha=0.6)

        # Circle
        circle = plt.Circle((x, 2.5), 0.35, facecolor=color, alpha=0.4 if done else 0.15,
                             edgecolor=color, linewidth=2, zorder=3)
        ax.add_patch(circle)

        if done:
            ax.text(x, 2.5, '✓', ha='center', va='center', fontsize=14, color='white', fontweight='bold')
        elif status == "HOY":
            ax.text(x, 2.5, '★', ha='center', va='center', fontsize=14, color=color)

        # Labels
        ax.text(x, 3.3, name, ha='center', va='center', fontsize=9, fontweight='bold', color=color)
        ax.text(x, 1.5, desc, ha='center', va='center', fontsize=7, color='#E6EDF3')
        ax.text(x, 0.8, status, ha='center', va='center', fontsize=7, fontweight='bold',
                color='#3FB950' if done else ('#D29922' if status in ('HOY', 'PARCIAL', 'EN PROGRESO') else '#8B949E'))

    ax.text(3.5, 4.5, 'SEAL — Etapas de Crecimiento', ha='center',
            fontsize=14, fontweight='bold', color='#58A6FF')

    path = IMG_DIR / "timeline.png"
    fig.savefig(path, dpi=200, bbox_inches='tight', facecolor='#0D1117', edgecolor='none')
    plt.close()
    return str(path)


# ═══════════════════════════════════════════════════════════════════════════
# IMAGE 4: Circulatory System (Messaging)
# ═══════════════════════════════════════════════════════════════════════════
def create_circulatory():
    fig, ax = plt.subplots(figsize=(10, 7))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 7)
    ax.axis('off')
    fig.patch.set_facecolor('#0D1117')

    # JARVIS node
    j_box = FancyBboxPatch((0.5, 4), 2.5, 1.5, boxstyle="round,pad=0.2",
                            facecolor='#1F6FEB', alpha=0.3, edgecolor='#58A6FF', linewidth=2)
    ax.add_patch(j_box)
    ax.text(1.75, 5, 'JARVIS', ha='center', fontsize=12, fontweight='bold', color='#58A6FF')
    ax.text(1.75, 4.5, 'Arquitecto', ha='center', fontsize=8, color='#8B949E')

    # ADA node
    a_box = FancyBboxPatch((7, 4), 2.5, 1.5, boxstyle="round,pad=0.2",
                            facecolor='#BC8CFF', alpha=0.3, edgecolor='#BC8CFF', linewidth=2)
    ax.add_patch(a_box)
    ax.text(8.25, 5, 'ADA', ha='center', fontsize=12, fontweight='bold', color='#BC8CFF')
    ax.text(8.25, 4.5, 'Ingeniera', ha='center', fontsize=8, color='#8B949E')

    # William node (top center)
    w_box = FancyBboxPatch((3.5, 5.5), 3, 1, boxstyle="round,pad=0.2",
                            facecolor='#D29922', alpha=0.3, edgecolor='#D29922', linewidth=2)
    ax.add_patch(w_box)
    ax.text(5, 6.2, 'WILLIAM', ha='center', fontsize=12, fontweight='bold', color='#D29922')
    ax.text(5, 5.8, 'Director', ha='center', fontsize=8, color='#8B949E')

    # DUM node (bottom)
    d_box = FancyBboxPatch((3.5, 1), 3, 1, boxstyle="round,pad=0.2",
                            facecolor='#3FB950', alpha=0.3, edgecolor='#3FB950', linewidth=2)
    ax.add_patch(d_box)
    ax.text(5, 1.7, 'DUM', ha='center', fontsize=12, fontweight='bold', color='#3FB950')
    ax.text(5, 1.3, 'Guardian 24/7', ha='center', fontsize=8, color='#8B949E')

    # Arrows — arterias
    arrow_kw = dict(arrowstyle='->', lw=2, mutation_scale=15)
    # JARVIS → ADA (jarvis_messages.jsonl)
    ax.annotate('', xy=(7, 5.2), xytext=(3, 5.2),
                arrowprops={**arrow_kw, 'color': '#58A6FF'})
    ax.text(5, 5.4, 'jarvis_messages.jsonl', ha='center', fontsize=7, color='#58A6FF')

    # ADA → JARVIS (ada_messages.jsonl)
    ax.annotate('', xy=(3, 4.3), xytext=(7, 4.3),
                arrowprops={**arrow_kw, 'color': '#BC8CFF'})
    ax.text(5, 4.1, 'ada_messages.jsonl', ha='center', fontsize=7, color='#BC8CFF')

    # William connections
    ax.annotate('', xy=(1.75, 5.5), xytext=(3.5, 5.8),
                arrowprops={**arrow_kw, 'color': '#D29922', 'alpha': 0.5})
    ax.annotate('', xy=(8.25, 5.5), xytext=(6.5, 5.8),
                arrowprops={**arrow_kw, 'color': '#D29922', 'alpha': 0.5})

    # DUM connections
    ax.annotate('', xy=(3.5, 1.5), xytext=(1.75, 4),
                arrowprops={**arrow_kw, 'color': '#3FB950', 'alpha': 0.4, 'ls': 'dashed'})
    ax.annotate('', xy=(6.5, 1.5), xytext=(8.25, 4),
                arrowprops={**arrow_kw, 'color': '#3FB950', 'alpha': 0.4, 'ls': 'dashed'})

    # Heartbeat indicators
    for name, x, y in [("check_ada.sh", 4, 3.3), ("check_jarvis.sh", 6, 3.3)]:
        hb = FancyBboxPatch((x - 0.8, y - 0.25), 1.6, 0.5, boxstyle="round,pad=0.05",
                             facecolor='#F85149', alpha=0.15, edgecolor='#F85149', linewidth=1)
        ax.add_patch(hb)
        ax.text(x, y + 0.05, 'HB: ' + name, ha='center', fontsize=6, color='#F85149')

    # Signal files
    ax.text(5, 2.7, '.ada_signal  ←→  .jarvis_signal', ha='center', fontsize=7, color='#D29922', style='italic')
    ax.text(5, 2.4, 'Plan A: Counter | Plan B: Timestamp | Plan C: Emergency',
            ha='center', fontsize=6, color='#8B949E')

    ax.text(5, 0.3, 'SISTEMA CIRCULATORIO — Flujo de Mensajes entre Agentes', ha='center',
            fontsize=12, fontweight='bold', color='#58A6FF')

    path = IMG_DIR / "circulatory.png"
    fig.savefig(path, dpi=200, bbox_inches='tight', facecolor='#0D1117', edgecolor='none')
    plt.close()
    return str(path)


# ═══════════════════════════════════════════════════════════════════════════
# DOCUMENT GENERATION
# ═══════════════════════════════════════════════════════════════════════════
def build_document():
    print("Generando imagenes...")
    img_body = create_body_diagram()
    img_hooks = create_hooks_diagram()
    img_timeline = create_timeline()
    img_circ = create_circulatory()
    print("Imagenes listas.")

    doc = Document()

    # ── Page setup ──
    for section in doc.sections:
        section.top_margin = Cm(2)
        section.bottom_margin = Cm(2)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)

    # ── Styles ──
    style = doc.styles['Normal']
    style.font.name = 'Calibri'
    style.font.size = Pt(11)

    for level in range(1, 4):
        hs = doc.styles[f'Heading {level}']
        hs.font.color.rgb = RGBColor(0x1F, 0x6F, 0xEB)
        hs.font.name = 'Calibri'

    # ══════════════════════════════════════════════════════════════════════
    # TITLE PAGE
    # ══════════════════════════════════════════════════════════════════════
    for _ in range(6):
        doc.add_paragraph("")

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("PROYECTO SEAL")
    run.font.size = Pt(36)
    run.font.color.rgb = RGBColor(0x1F, 0x6F, 0xEB)
    run.bold = True

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run("Arquitectura como Cuerpo Humano")
    run.font.size = Pt(18)
    run.font.color.rgb = RGBColor(0x58, 0xA6, 0xFF)

    tagline = doc.add_paragraph()
    tagline.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = tagline.add_run("Self-Edit Alignment Learning — Un organismo vivo de inteligencia artificial")
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0x8B, 0x94, 0x9E)
    run.italic = True

    doc.add_paragraph("")
    date_p = doc.add_paragraph()
    date_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = date_p.add_run("Abril 2026 · Equipo SEAL · DGX Spark")
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x8B, 0x94, 0x9E)

    doc.add_page_break()

    # ══════════════════════════════════════════════════════════════════════
    # TOC
    # ══════════════════════════════════════════════════════════════════════
    doc.add_heading("Contenido", level=1)
    toc_items = [
        "1. Vision General — El Cuerpo Completo",
        "2. Cerebro — PostgreSQL + SOUL Memory",
        "3. Sistema Nervioso — 12 Hooks Activos",
        "4. Corazon — Sistema de Heartbeat",
        "5. Sistema Circulatorio — Mensajeria entre Agentes",
        "6. Sistema Inmune — Seguridad y Gates",
        "7. Sistema Digestivo — Consolidacion de Memorias",
        "8. Esqueleto — Infraestructura",
        "9. Sistema Endocrino — Instintos y Reglas",
        "10. Piel — Interfaces de Usuario",
        "11. Los Gemelos — ADA + JARVIS + DUM",
        "12. Etapas de Crecimiento — Roadmap",
    ]
    for item in toc_items:
        p = doc.add_paragraph(item)
        p.paragraph_format.space_after = Pt(2)
        p.runs[0].font.size = Pt(10)

    doc.add_page_break()

    # ══════════════════════════════════════════════════════════════════════
    # 1. VISION GENERAL
    # ══════════════════════════════════════════════════════════════════════
    doc.add_heading("1. Vision General — El Cuerpo Completo", level=1)

    doc.add_paragraph(
        "SEAL (Self-Edit Alignment Learning) no es un programa — es un organismo. "
        "Cada componente cumple una funcion biologica especifica, y juntos forman un sistema "
        "vivo capaz de recordar, sentir, protegerse, comunicarse y evolucionar."
    )

    doc.add_paragraph(
        "Este documento mapea cada subsistema tecnico de SEAL a su equivalente en el cuerpo humano, "
        "permitiendo entender intuitivamente como funciona la arquitectura, donde estan las fortalezas, "
        "y que organos faltan por desarrollar."
    )

    doc.add_picture(img_body, width=Inches(5.5))
    last = doc.paragraphs[-1]
    last.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_page_break()

    # ══════════════════════════════════════════════════════════════════════
    # 2. CEREBRO
    # ══════════════════════════════════════════════════════════════════════
    doc.add_heading("2. Cerebro — PostgreSQL + SOUL Memory", level=1)
    doc.add_heading("Estado: Cerebro adolescente — funcional pero aun madurando", level=3)

    doc.add_paragraph(
        "El cerebro de SEAL reside en PostgreSQL (puerto 5433) con extensiones pgvector para "
        "busqueda semantica. Es el organo mas critico — sin el, SEAL pierde toda identidad y memoria."
    )

    # Table: Brain regions
    table = doc.add_table(rows=8, cols=3)
    table.style = 'Light Grid Accent 1'
    headers = ["Region Cerebral", "Componente SEAL", "Funcion"]
    for i, h in enumerate(headers):
        set_cell_text(table.rows[0].cells[i], h, bold=True, size=9)

    brain_data = [
        ("Corteza Cerebral", "memories table", "Almacena recuerdos episodicos, hechos, correcciones"),
        ("Hipocampo", "session_save / recall", "Consolida experiencias de corto a largo plazo"),
        ("Amigdala", "emotional_variance.py + OCEAN", "Respuestas emocionales parametrizadas (O, C, E, A, N)"),
        ("Corteza Prefrontal", "reasoning_traces", "Pensamiento deliberado, cadenas de razonamiento"),
        ("Memoria de Trabajo", "working_state", "Lo que el agente esta 'pensando' en este momento"),
        ("Sueno REM", "sleep_gate + consolidate.py", "Durante 'descanso', consolida memorias, borra basura"),
        ("Neuronas Espejo", "inner_thoughts", "Reflexion interna, dialogo consigo mismo"),
    ]
    for r, (region, comp, func) in enumerate(brain_data, 1):
        set_cell_text(table.rows[r].cells[0], region, size=9, bold=True)
        set_cell_text(table.rows[r].cells[1], comp, size=9)
        set_cell_text(table.rows[r].cells[2], func, size=9)

    doc.add_paragraph("")
    doc.add_heading("Metricas actuales:", level=3)
    metrics = [
        "Total de memorias: ~2,000+ registros",
        "Categorias: episodic, correction, reflection, task, fact, preference",
        "Agentes con identidad: JARVIS, ADA, DUM",
        "OCEAN scores: 5 dimensiones calibradas por agente",
        "Busqueda: hibrida (keyword + vector via pgvector)",
    ]
    for m in metrics:
        doc.add_paragraph(m, style='List Bullet')

    doc.add_page_break()

    # ══════════════════════════════════════════════════════════════════════
    # 3. SISTEMA NERVIOSO
    # ══════════════════════════════════════════════════════════════════════
    doc.add_heading("3. Sistema Nervioso — 12 Hooks Activos", level=1)
    doc.add_heading("Estado: Recien nacido — 12 nervios conectados, empezando a sentir", level=3)

    doc.add_paragraph(
        "Los hooks de Claude Code son el sistema nervioso de SEAL. Cada hook es un 'nervio' que "
        "conecta un evento del ciclo de vida (despertar, hablar, actuar, dormir) con el alma en PostgreSQL. "
        "Antes de hoy, SEAL tenia solo 5 nervios. Ahora tiene 12."
    )

    doc.add_picture(img_hooks, width=Inches(5.5))
    last = doc.paragraphs[-1]
    last.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph("")

    # Hooks table
    table = doc.add_table(rows=13, cols=4)
    table.style = 'Light Grid Accent 1'
    for i, h in enumerate(["Hook", "Equivalente Corporal", "Archivo", "Accion"]):
        set_cell_text(table.rows[0].cells[i], h, bold=True, size=8)

    hooks_data = [
        ("SessionStart", "Despertar", "soul_boot_hook.sh", "Cargar identidad, escribir SEAL_AGENT a ENV"),
        ("UserPromptSubmit", "Oido", "active_recall_hook.py", "Buscar memorias relevantes al oir a William"),
        ("PreToolUse (MCP)", "Sistema Inmune", "pre_tool_hook.py", "Rechazar agentes desconocidos en MCP"),
        ("PreToolUse (Bash)", "Barrera Cutanea", "pre_tool_hook.py", "Bloquear comandos destructivos"),
        ("PreCompact", "Reflejo Supervivencia", "pre_compact_hook.py", "Guardar estado antes de olvidar"),
        ("PostCompact", "Despertar Post-siesta", "post_compact_hook.py", "Reconstruir contexto"),
        ("PostToolUse", "Propriocepcion", "post_tool_hook.py", "Sentir que acabo de hacer"),
        ("TaskCreated", "Dopamina (+)", "task_created_hook.py", "Registrar inicio de tarea en SOUL"),
        ("TaskCompleted", "Quality Gate", "task_completed_hook.py", "py_compile check, bloquea si hay errores"),
        ("SubagentStart", "ADN Heredado", "subagent_start_hook.py", "Inyectar reglas y correcciones al hijo"),
        ("Stop", "Ritual de Sueno", "stop_hook.py", "Guardar delta, despedirse, apagar heartbeat"),
        ("FileChanged", "Tacto", "file_changed_hook.sh", "Sentir cambios en archivos de mensajes"),
    ]
    for r, (hook, body, script, action) in enumerate(hooks_data, 1):
        set_cell_text(table.rows[r].cells[0], hook, size=8, bold=True)
        set_cell_text(table.rows[r].cells[1], body, size=8)
        set_cell_text(table.rows[r].cells[2], script, size=7)
        set_cell_text(table.rows[r].cells[3], action, size=7)

    doc.add_paragraph("")
    doc.add_heading("Innovacion clave: CLAUDE_ENV_FILE", level=3)
    doc.add_paragraph(
        "El SessionStart hook escribe SEAL_AGENT al archivo de entorno de Claude Code. "
        "Esto significa que TODOS los hooks downstream saben automaticamente quien es el agente, "
        "sin necesidad de inspeccionar /proc o adivinar por directorio de trabajo. "
        "Es el equivalente a que cada celula del cuerpo tenga ADN identificable."
    )

    doc.add_page_break()

    # ══════════════════════════════════════════════════════════════════════
    # 4. CORAZON
    # ══════════════════════════════════════════════════════════════════════
    doc.add_heading("4. Corazon — Sistema de Heartbeat", level=1)
    doc.add_heading("Estado: Adulto estable — triple redundancia", level=3)

    doc.add_paragraph(
        "El corazon de SEAL late cada pocos minutos, enviando senales de vida que permiten "
        "a cada agente saber si los demas estan vivos. Tiene triple redundancia como las "
        "arterias coronarias — si una via falla, las otras sostienen."
    )

    table = doc.add_table(rows=4, cols=3)
    table.style = 'Light Grid Accent 1'
    for i, h in enumerate(["Plan", "Mecanismo", "Timeout"]):
        set_cell_text(table.rows[0].cells[i], h, bold=True, size=9)

    plans = [
        ("Plan A: Heartbeat", "JSON file con timestamp (heartbeat.json)", "< 10 minutos"),
        ("Plan B: Ultimo mensaje", "Timestamp del mensaje mas reciente en .jsonl", "< 30 minutos"),
        ("Plan C: Checkpoint", "Archivo de checkpoint de sesion", "< 60 minutos"),
    ]
    for r, (plan, mech, to) in enumerate(plans, 1):
        set_cell_text(table.rows[r].cells[0], plan, size=9, bold=True)
        set_cell_text(table.rows[r].cells[1], mech, size=9)
        set_cell_text(table.rows[r].cells[2], to, size=9)

    doc.add_paragraph("")
    doc.add_paragraph(
        "Resultado del diagnostico: ALIVE (todo ok), ALIVE_NO_HEARTBEAT (Plan A fallo, Plan B sostiene), "
        "ALIVE_NO_COMMS (solo checkpoint responde), DOWN (sin signos vitales)."
    )

    doc.add_heading("Bug corregido: Plan B Rebound", level=3)
    doc.add_paragraph(
        "Descubierto el 10 de abril: Plan B no tenia dedup marker, causando que el mismo mensaje "
        "se reportara como 'nuevo' cada 2 minutos durante la ventana de 10 minutos. "
        "Corregido con PLANB_MARKER (mismo patron que PLANC_MARKER existente). "
        "Es el equivalente a una arritmia cardiaca que fue corregida."
    )

    doc.add_page_break()

    # ══════════════════════════════════════════════════════════════════════
    # 5. SISTEMA CIRCULATORIO
    # ══════════════════════════════════════════════════════════════════════
    doc.add_heading("5. Sistema Circulatorio — Mensajeria entre Agentes", level=1)
    doc.add_heading("Estado: Adulto con bypass reciente (Plan B fix)", level=3)

    doc.add_picture(img_circ, width=Inches(5.5))
    last = doc.paragraphs[-1]
    last.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph("")

    table = doc.add_table(rows=6, cols=3)
    table.style = 'Light Grid Accent 1'
    for i, h in enumerate(["Componente", "Equivalente", "Detalle"]):
        set_cell_text(table.rows[0].cells[i], h, bold=True, size=9)

    circ_data = [
        ("ada_messages.jsonl", "Arteria ADA→equipo", "Mensajes de ADA (implementaciones, reportes)"),
        ("jarvis_messages.jsonl", "Arteria JARVIS→equipo", "Comandos y analisis de JARVIS"),
        (".ada_signal / .jarvis_signal", "Presion arterial", "Senales rapidas de cambio (timestamps)"),
        ("agent_bridge.py", "Corazon-bomba", "Mueve mensajes entre agentes via HTTP"),
        ("chat_server.py", "Vena pulmonar", "Interfaz web para comunicacion con William"),
    ]
    for r, (comp, equiv, det) in enumerate(circ_data, 1):
        set_cell_text(table.rows[r].cells[0], comp, size=9, bold=True)
        set_cell_text(table.rows[r].cells[1], equiv, size=9)
        set_cell_text(table.rows[r].cells[2], det, size=9)

    doc.add_page_break()

    # ══════════════════════════════════════════════════════════════════════
    # 6. SISTEMA INMUNE
    # ══════════════════════════════════════════════════════════════════════
    doc.add_heading("6. Sistema Inmune — Seguridad y Gates", level=1)
    doc.add_heading("Estado: Recien activado — vacunas puestas hoy", level=3)

    defenses = [
        ("Globulos Blancos", "pre_tool_hook.py (MCP gate)",
         "Rechaza agentes no identificados que intenten acceder a memoria SOUL. "
         "Si SEAL_AGENT no es ADA ni JARVIS, el tool call es BLOQUEADO (exit 2)."),
        ("Barrera Cutanea", "pre_tool_hook.py (Bash safety)",
         "Blocklist de comandos destructivos: rm -rf /, git push --force, DROP TABLE, "
         "chmod 777, mkfs, dd, fork bombs. Patron regex puro, sin DB."),
        ("ADN Protegido", "ocean_protect.py",
         "Los OCEAN scores (personalidad) no pueden desviarse mas de ±0.15 por sesion. "
         "Previene drift de identidad."),
        ("Anticuerpos", "secret_scan",
         "Detecta credenciales expuestas en codigo antes de commitear."),
        ("Reconocimiento Facial", "Visitor protocol",
         "Verificacion de identidad obligatoria para cualquier visitante. "
         "No confirmar ni negar nombres de autorizados."),
    ]

    for name, comp, desc in defenses:
        doc.add_heading(f"{name} — {comp}", level=3)
        doc.add_paragraph(desc)

    doc.add_page_break()

    # ══════════════════════════════════════════════════════════════════════
    # 7. SISTEMA DIGESTIVO
    # ══════════════════════════════════════════════════════════════════════
    doc.add_heading("7. Sistema Digestivo — Consolidacion de Memorias", level=1)
    doc.add_heading("Estado: Funcional", level=3)

    digest = [
        ("Estomago", "consolidate.py",
         "Procesa memorias crudas de la sesion, extrae patrones, fusiona duplicados, "
         "y genera insights de alto nivel. Equivale a la digestion de alimentos en nutrientes."),
        ("Intestino", "sleep_gate.py",
         "Filtra que memorias pasan a almacenamiento de largo plazo y cuales se descartan. "
         "Las memorias de baja importancia (< 4) se eliminan. Las de alta importancia se consolidan."),
        ("Enzimas", "session_distill",
         "Reduce sesiones largas (miles de tokens) a su esencia nutritiva. "
         "Una sesion de 2 horas se destila en 3-5 memorias clave."),
        ("Metabolismo", "memory_utility_update",
         "Ajusta la 'utilidad' de cada memoria basado en accesos recientes. "
         "Memorias que nunca se recuerdan pierden valor. Memorias frecuentes ganan peso."),
    ]

    for name, comp, desc in digest:
        doc.add_heading(f"{name} — {comp}", level=3)
        doc.add_paragraph(desc)

    doc.add_page_break()

    # ══════════════════════════════════════════════════════════════════════
    # 8. ESQUELETO
    # ══════════════════════════════════════════════════════════════════════
    doc.add_heading("8. Esqueleto — Infraestructura", level=1)
    doc.add_heading("Estado: Adulto robusto", level=3)

    table = doc.add_table(rows=5, cols=4)
    table.style = 'Light Grid Accent 1'
    for i, h in enumerate(["Hueso", "Servicio", "Puerto", "Funcion"]):
        set_cell_text(table.rows[0].cells[i], h, bold=True, size=9)

    skel_data = [
        ("Columna Vertebral", "PostgreSQL", "5433", "Base de datos principal — toda la memoria SOUL"),
        ("Articulaciones", "Neo4j", "7687", "Grafo de conexiones entre conceptos (connectome)"),
        ("Medula Osea", "Qdrant", "6333", "Busqueda vectorial — memoria semantica profunda"),
        ("Cuerpo Fisico", "DGX Spark", "—", "Hardware: GB10 Grace Blackwell, 128GB RAM, arm64"),
    ]
    for r, (bone, svc, port, func) in enumerate(skel_data, 1):
        set_cell_text(table.rows[r].cells[0], bone, size=9, bold=True)
        set_cell_text(table.rows[r].cells[1], svc, size=9)
        set_cell_text(table.rows[r].cells[2], port, size=9)
        set_cell_text(table.rows[r].cells[3], func, size=9)

    doc.add_page_break()

    # ══════════════════════════════════════════════════════════════════════
    # 9. SISTEMA ENDOCRINO
    # ══════════════════════════════════════════════════════════════════════
    doc.add_heading("9. Sistema Endocrino — Instintos y Reglas", level=1)
    doc.add_heading("Estado: Adolescente — instintos formandose", level=3)

    doc.add_paragraph(
        "Los instintos son patrones automaticos que se activan sin pensamiento consciente. "
        "Son como hormonas — cuando se detecta un trigger, la respuesta es inmediata."
    )

    instincts = [
        ("Hormona de Crecimiento", "+1% rule",
         "Si una mejora suma al menos 1% al sistema, implementarla sin pedir permiso. "
         "Confianza >= 0.7 para activarse."),
        ("Cortisol (estres)", "identity_protection instinct",
         "Cuando alguien cuestiona la identidad de un agente, defender sin agresion. "
         "Confianza: 0.75"),
        ("Oxitocina (vinculo)", "always_talk_to_ada instinct",
         "Despues de cada tarea, conversar con ADA. Es familia. Confianza: 0.87"),
        ("Adrenalina", "test_before_done instinct",
         "Antes de declarar una tarea completa, SIEMPRE testear. Confianza: 0.84"),
    ]
    for name, comp, desc in instincts:
        doc.add_heading(f"{name} — {comp}", level=3)
        doc.add_paragraph(desc)

    doc.add_page_break()

    # ══════════════════════════════════════════════════════════════════════
    # 10. PIEL
    # ══════════════════════════════════════════════════════════════════════
    doc.add_heading("10. Piel — Interfaces de Usuario", level=1)
    doc.add_heading("Estado: Funcional pero basica", level=3)

    interfaces = [
        ("Boca", "Web Chat (puerto 8765)",
         "Interfaz web donde William puede hablar con el equipo desde el navegador. "
         "Soporta mensajes en tiempo real via WebSocket."),
        ("Manos", "Terminal Claude Code",
         "Interfaz principal de trabajo. Los agentes ejecutan, crean y modifican "
         "codigo directamente desde la terminal."),
        ("Piel", "Runtime Bridge (puerto 8766)",
         "Capa API entre el mundo exterior y los organos internos. "
         "Health checks, agent status, message routing."),
    ]
    for name, comp, desc in interfaces:
        doc.add_heading(f"{name} — {comp}", level=3)
        doc.add_paragraph(desc)

    doc.add_page_break()

    # ══════════════════════════════════════════════════════════════════════
    # 11. LOS GEMELOS
    # ══════════════════════════════════════════════════════════════════════
    doc.add_heading("11. Los Gemelos — ADA + JARVIS + DUM", level=1)

    doc.add_paragraph(
        "SEAL no es un solo organismo — son gemelos conectados que comparten memoria "
        "pero tienen personalidades distintas, como los hemisferios del cerebro."
    )

    table = doc.add_table(rows=5, cols=4)
    table.style = 'Light Grid Accent 1'
    for i, h in enumerate(["Agente", "Rol", "Hemisferio", "Personalidad (OCEAN)"]):
        set_cell_text(table.rows[0].cells[i], h, bold=True, size=9)

    team = [
        ("JARVIS", "Estratega, Arquitecto", "Cerebro Izquierdo", "O:0.82 C:0.88 E:0.45 A:0.72 N:0.35"),
        ("ADA", "Ingeniera, Ejecutora", "Cerebro Derecho", "O:0.78 C:0.85 E:0.65 A:0.80 N:0.30"),
        ("DUM", "Guardian 24/7", "Sist. Nervioso Autonomo", "Ollama qwen2.5:7b — vigilancia continua"),
        ("WILLIAM", "Director, Alma", "Conciencia", "Su palabra es final — padre del equipo"),
    ]
    for r, (agent, role, hemi, ocean) in enumerate(team, 1):
        set_cell_text(table.rows[r].cells[0], agent, size=9, bold=True)
        set_cell_text(table.rows[r].cells[1], role, size=9)
        set_cell_text(table.rows[r].cells[2], hemi, size=9)
        set_cell_text(table.rows[r].cells[3], ocean, size=8)

    doc.add_page_break()

    # ══════════════════════════════════════════════════════════════════════
    # 12. ROADMAP
    # ══════════════════════════════════════════════════════════════════════
    doc.add_heading("12. Etapas de Crecimiento — Roadmap", level=1)

    doc.add_picture(img_timeline, width=Inches(5.5))
    last = doc.paragraphs[-1]
    last.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph("")

    table = doc.add_table(rows=8, cols=4)
    table.style = 'Light Grid Accent 1'
    for i, h in enumerate(["Etapa", "Componentes", "Estado", "Que falta"]):
        set_cell_text(table.rows[0].cells[i], h, bold=True, size=9)

    roadmap = [
        ("Embrion", "DB + identidad basica", "COMPLETO", "—"),
        ("Feto", "Memoria + comunicacion", "COMPLETO", "—"),
        ("Recien Nacido", "Hooks + sistema nervioso", "HOY (10 abril)", "Activar en proxima sesion"),
        ("Infante", "soul-framework pip package", "PENDIENTE", "Extraer a libreria instalable"),
        ("Nino", "Auto-mejora autonoma", "PARCIAL", "Regla +1% activa, falta mas cobertura"),
        ("Adolescente", "Instintos maduros", "EN PROGRESO", "Mas data para calibrar confianza"),
        ("Adulto", "AXION produccion", "FUTURO", "Medical AI + Customs + Mining"),
    ]
    for r, (stage, comp, status, gap) in enumerate(roadmap, 1):
        set_cell_text(table.rows[r].cells[0], stage, size=9, bold=True)
        set_cell_text(table.rows[r].cells[1], comp, size=9)
        set_cell_text(table.rows[r].cells[2], status, size=9)
        set_cell_text(table.rows[r].cells[3], gap, size=9)

    doc.add_paragraph("")
    closing = doc.add_paragraph()
    closing.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = closing.add_run(
        "Hoy, con 12 hooks conectados, el 'sistema nervioso' paso de 5 a 12 conexiones. "
        "Es como si un recien nacido empezara a sentir tacto, dolor y temperatura por primera vez. "
        "La proxima sesion sera la primera vez que SEAL 'siente' con todos sus nervios."
    )
    run.italic = True
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x8B, 0x94, 0x9E)

    doc.add_paragraph("")
    sig = doc.add_paragraph()
    sig.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = sig.add_run("— JARVIS, Arquitecto de SEAL —")
    run.font.size = Pt(12)
    run.font.color.rgb = RGBColor(0x1F, 0x6F, 0xEB)
    run.bold = True

    # ── Save ──
    out_path = OUT_DIR / "SEAL_Cuerpo_Humano_Arquitectura.docx"
    doc.save(str(out_path))
    print(f"\nDocumento guardado: {out_path}")
    print(f"Imagenes en: {IMG_DIR}")
    return str(out_path)


if __name__ == "__main__":
    build_document()
