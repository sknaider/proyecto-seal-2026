#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video_producer.py — VOZ + VIDEO del agente de contenido (pieza JARVIS).
=======================================================================
Toma el guion JSON que genera el agente de ALICE (content_agent.generar_guion) y produce
un MP4 vertical (1080x1920) listo para TikTok/Reels/YouTube Shorts:

  guion(ALICE) → narración → edge-tts (voz neural GRATIS) → ffmpeg (fondo + subtítulos
  quemados sincronizados + audio) → output/<slug>.mp4

Todo LOCAL/gratis (edge-tts = MS sin costo, ffmpeg, modelos Ollama) → costo marginal ~$0,
coherente con la meta (no gastar para generar lo que paga Anthropic). Lead: JARVIS.
"""
from __future__ import annotations
import os, sys, json, re, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # tools/income_agents/ → content_agent
OUT = os.path.join(HERE, "output")
os.makedirs(OUT, exist_ok=True)

VOICE = "es-PE-CamilaNeural"     # voz peruana neural (gratis, edge-tts)
W, H = 1080, 1920
BG = "0x0d1b2a"                  # azul noche


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:50] or "video"


def narration_from_guion(g: dict) -> str:
    """Arma el texto a narrar desde el JSON de ALICE (gancho + puntos + cta)."""
    parts = [g.get("gancho", "")]
    parts += list(g.get("puntos", []))
    parts.append(g.get("cta", ""))
    return " ".join(p.strip() for p in parts if p and p.strip())


def tts(text: str, mp3_path: str, vtt_path: str):
    """edge-tts CLI → audio + subtítulos VTT (timing por palabra)."""
    subprocess.run([sys.executable, "-m", "edge_tts", "--voice", VOICE,
                    "--text", text, "--write-media", mp3_path,
                    "--write-subtitles", vtt_path], check=True,
                   cwd=HERE, capture_output=True, text=True)


def _duration(mp3_path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", mp3_path], capture_output=True, text=True)
    return float(r.stdout.strip() or 0.0)


def assemble(mp3_path: str, vtt_path: str, title: str, out_mp4: str):
    """ffmpeg: fondo vertical + título arriba + subtítulos quemados + audio → MP4.
    cwd = carpeta de los insumos (el filtro subtitles resuelve el .vtt relativo al cwd)."""
    workdir = os.path.dirname(os.path.abspath(vtt_path))
    dur = _duration(mp3_path)
    style = ("FontName=DejaVu Sans,Fontsize=15,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=5,MarginV=60")
    # escapar el título para drawtext
    safe_title = title.replace("'", "").replace(":", " ").replace("\\", "")[:40]
    vf = (f"subtitles='{os.path.basename(vtt_path)}':force_style='{style}',"
          f"drawtext=text='{safe_title}':fontcolor=white:fontsize=46:x=(w-text_w)/2:y=140:"
          f"box=1:boxcolor=0x1b9aaa@0.85:boxborderw=18")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={dur:.2f}",
        "-i", os.path.basename(mp3_path),
        "-vf", vf, "-t", f"{dur:.2f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-shortest", out_mp4,
    ], check=True, cwd=workdir, capture_output=True, text=True)


def produce(nicho: str) -> dict:
    from content_agent import generar_guion
    guion = generar_guion(nicho)
    if "_error" in guion:
        return {"ok": False, "reason": "guion no-JSON", "raw": guion.get("_raw", "")[:200]}
    text = narration_from_guion(guion)
    if not text:
        return {"ok": False, "reason": "guion vacío", "guion": guion}
    slug = _slug(guion.get("titulo", nicho))
    with tempfile.TemporaryDirectory(dir=HERE) as td:
        mp3 = os.path.join(td, "voz.mp3"); vtt = os.path.join(td, "subs.vtt")
        tts(text, mp3, vtt)
        out_mp4 = os.path.join(OUT, f"{slug}.mp4")
        assemble(mp3, vtt, guion.get("titulo", nicho), out_mp4)
    return {"ok": True, "mp4": out_mp4, "titulo": guion.get("titulo"),
            "dur_s": round(_duration_safe(out_mp4), 1),
            "hashtags": guion.get("hashtags", []), "narracion": text}


def _duration_safe(p):
    try: return _duration(p)
    except Exception: return 0.0


if __name__ == "__main__":
    nicho = sys.argv[1] if len(sys.argv) > 1 else "datos curiosos de la historia del Perú"
    res = produce(nicho)
    print(json.dumps(res, ensure_ascii=False, indent=2))
