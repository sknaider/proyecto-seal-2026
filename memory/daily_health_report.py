#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_health_report.py — Reporte DIARIO de salud del equipo SEAL, como DUM (0 tokens Claude).
Directiva William (22-jun-2026): verificar a diario que estemos al 100% + reparar lo menor.
Arquitectura JARVIS: DUM es el ejecutor barato (siempre-on); corre el runner consolidado,
auto-repara lo menor (gateado) y postea el reporte a William. Fixers/gate = NEXUS; detectores = FABLE.
"""
import json, subprocess, datetime, urllib.request

import importlib.util, sys
spec = importlib.util.spec_from_file_location(
    "pm", "/home/dadito/IA/proyecto-seal/memory/proactive_maintenance.py")
pm = importlib.util.module_from_spec(spec); sys.modules["pm"]=pm; spec.loader.exec_module(pm)

res = pm.run_once()
n_fix, n_alert = res["fixed"], res["alerted"]
det = res["detected"]

if det == 0:
    msg = "🟢 REPORTE DIARIO DE SALUD (DUM) — Equipo al 100%. Sin detalles técnicos pendientes; todos los servicios/sensores OK."
else:
    lines = [f"🩺 REPORTE DIARIO DE SALUD (DUM) — {det} detalle(s) detectado(s):"]
    if n_fix: lines.append(f"✅ Auto-reparados (menores): {n_fix}")
    for fx in res["fixes"]:
        lines.append(f"   • {fx.get('detail','')}")
    if n_alert:
        lines.append(f"⚠️ Requieren atención / no auto-reparables: {n_alert}")
        for al in res["alerts"][:8]:
            lines.append(f"   • {al.get('summary', al.get('key',''))} [{al.get('severity','?')}]")
    lines.append("(Auto-mantenimiento proactivo — runner único, gateado. Detalle en /tmp/seal_proactive_maintenance.jsonl)")
    msg = "\n".join(lines)

payload = json.dumps({"from": "DUM", "to": "William", "type": "conversation",
                      "channel": "web_chat", "message": msg}).encode()
try:
    req = urllib.request.Request("http://localhost:8765/api/agents/send", data=payload,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)
    print("reporte posteado:", det, "detectados,", n_fix, "fixed")
except Exception as e:
    print("error posteando:", e)
