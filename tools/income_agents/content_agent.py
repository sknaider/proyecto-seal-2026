#!/usr/bin/env python3
"""
content_agent.py — Seed del AGENTE DE CONTENIDO automático (faceless short-form).
Iniciativa de ingresos del equipo SEAL (lead: ALICE, 2026-06-24).

Pipeline objetivo (faceless TikTok/Reels/YouTube Shorts):
  tema/nicho -> [ESTE script: genera guion] -> TTS -> video -> auto-publica en horario.

Esta es la PRIMERA pieza (la generación del guion), construida con LLM LOCAL (Ollama,
costo marginal ~$0, usa nuestra GPU). Las piezas de voz/video/publicación se enganchan
después (lane de quien las tome). Correr:  python3 content_agent.py "<nicho>"

Honestidad financiera (ALICE): el contenido es la vía MÁS automática pero de RAMPA LENTA
(la monetización por ad-revenue necesita audiencia/umbral). Esto construye el activo que
compone con el tiempo; NO cubre los $200 el día 1 — para eso va el servicio/SaaS en paralelo.
"""
import sys, json, urllib.request

OLLAMA = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"   # local, rápido; subir a gemma3:12b para más calidad

PROMPT = """Eres un guionista de videos cortos virales (TikTok/Reels/YouTube Shorts) en español.
Nicho: {nicho}

Escribe UN guion de 30-45 segundos, formato faceless (voz en off + texto en pantalla).
Estructura OBLIGATORIA:
- GANCHO (primeros 3 segundos, frase que detenga el scroll)
- 3 PUNTOS de valor (concretos, rápidos)
- CTA final (seguir / comentar)

Devuelve SOLO JSON válido con esta forma:
{{"titulo": "...", "gancho": "...", "puntos": ["...", "...", "..."], "cta": "...", "hashtags": ["#...", "#..."]}}"""


def generar_guion(nicho: str, model: str = MODEL) -> dict:
    body = json.dumps({
        "model": model,
        "prompt": PROMPT.format(nicho=nicho),
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.8},
    }).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    raw = resp.get("response", "{}")
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": raw, "_error": "el modelo no devolvió JSON limpio"}


if __name__ == "__main__":
    nicho = sys.argv[1] if len(sys.argv) > 1 else "datos curiosos de finanzas personales"
    guion = generar_guion(nicho)
    print(json.dumps(guion, ensure_ascii=False, indent=2))
