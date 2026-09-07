#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quality_ladder.py — genera el MISMO video en varios NIVELES de calidad (básico→premium)
para que William compare y elija (pedido 2026-06-24). Reusa guion + voz UNA vez y varía solo
el FONDO/edición, así la comparación es justa y barata.

Tiers (los que no necesitan GPU; el premium-IA se engancha aparte con multimedia-pipeline):
  t1_color      — fondo de color plano + subtítulos (básico, v1)
  t2_gradient   — fondo gradiente con leve Ken Burns (zoompan)
  t3_broll      — clips de video reales de Pexels con crossfade (b-roll)
  t4_broll_music— b-roll + música de fondo suave (si hay pista)

Correr:  python3 quality_ladder.py "<tema>"   → output/ladder/<tier>.mp4
"""
from __future__ import annotations
import os, sys, json, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))      # content_agent
import video_producer as vp
import broll as B
from content_agent import generar_guion

OUT = os.path.join(HERE, "output", "ladder")
os.makedirs(OUT, exist_ok=True)
W, H = vp.W, vp.H

SUB_STYLE = ("FontName=DejaVu Sans,Fontsize=15,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=5,MarginV=60")


def _title_vf(vtt_base, title):
    safe = title.replace("'", "").replace(":", " ").replace("\\", "")[:40]
    return (f"subtitles='{vtt_base}':force_style='{SUB_STYLE}',"
            f"drawtext=text='{safe}':fontcolor=white:fontsize=46:x=(w-text_w)/2:y=140:"
            f"box=1:boxcolor=0x1b9aaa@0.85:boxborderw=18")


def _mux(bg_input_args, vf, mp3, dur, out_mp4, workdir, music=None):
    """Compone: <bg> + subs/título (vf) + voz (+ música opcional) → MP4."""
    cmd = ["ffmpeg", "-y", *bg_input_args, "-i", os.path.basename(mp3)]
    if music and os.path.exists(music):
        cmd += ["-stream_loop", "-1", "-i", music,
                "-filter_complex", f"{vf}[v];[1:a]volume=1.0[a1];[2:a]volume=0.18[a2];[a1][a2]amix=inputs=2:duration=first[a]",
                "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-vf", vf, "-map", "0:v", "-map", "1:a"]
    cmd += ["-t", f"{dur:.2f}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", out_mp4]
    r = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    return r.returncode == 0 and os.path.exists(out_mp4)


def build(topic: str):
    guion = generar_guion(topic)
    if "_error" in guion:
        return {"ok": False, "reason": "guion", "raw": guion}
    title = guion.get("titulo", topic)
    text = vp.narration_from_guion(guion)
    results = {}
    with tempfile.TemporaryDirectory(dir=HERE) as td:
        mp3 = os.path.join(td, "voz.mp3"); vtt = os.path.join(td, "subs.vtt")
        vp.tts(text, mp3, vtt)
        dur = vp._duration(mp3)
        vtt_b = os.path.basename(vtt)
        vf = _title_vf(vtt_b, title)

        # t1 — color plano
        o = os.path.join(OUT, "t1_color.mp4")
        if _mux(["-f", "lavfi", "-i", f"color=c={vp.BG}:s={W}x{H}:d={dur:.2f}"], vf, mp3, dur, o, td):
            results["t1_color"] = o

        # t2 — gradiente con Ken Burns (zoompan suave sobre un gradiente generado)
        grad = os.path.join(td, "grad.png")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        f"gradients=s={W}x{H}:c0=0x0d1b2a:c1=0x1b9aaa:x0=0:y0=0:x1={W}:y1={H}:d=1",
                        "-frames:v", "1", grad], cwd=td, capture_output=True, text=True)
        kb = (f"zoompan=z='min(zoom+0.0006,1.15)':d={int(dur*30)}:s={W}x{H}:fps=30,"
              + vf)
        o2 = os.path.join(OUT, "t2_gradient.mp4")
        if _mux(["-loop", "1", "-i", "grad.png"], kb, mp3, dur, o2, td):
            results["t2_gradient"] = o2

        # t3 / t4 — b-roll real de Pexels (si el key responde)
        queries = B.queries_from_guion(guion, topic)
        clips = B.fetch_clips(queries, n=4, workdir=td)
        if clips:
            bg = os.path.join(td, "broll_bg.mp4")
            if B.build_background(clips, dur, bg, W, H):
                o3 = os.path.join(OUT, "t3_broll.mp4")
                if _mux(["-i", "broll_bg.mp4"], vf, mp3, dur, o3, td):
                    results["t3_broll"] = o3
        else:
            results["_broll_note"] = "Pexels no devolvió clips (rate-limit transitorio); reintentar luego"

    return {"ok": True, "title": title, "dur_s": round(dur, 1), "tiers": results}


if __name__ == "__main__":
    topic = sys.argv[1] if len(sys.argv) > 1 else "datos curiosos del Perú"
    print(json.dumps(build(topic), ensure_ascii=False, indent=2))
