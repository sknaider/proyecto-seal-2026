#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
broll.py — B-roll de video para el agente de contenido (mejora "imágenes+edición", ALICE 2026-06-24).
William: el video plano necesita imágenes. Esto baja CLIPS de video verticales RELEVANTES de
Pexels (gratis, API key ya existente) y arma un FONDO con transiciones, sobre el que el
video_producer monta voz+subtítulos. Costo ~$0, NO usa GPU (no recalienta el Spark).

API key: lee PEXELS_API_KEY del entorno o de multimedia-pipeline/.env (ya configurada).
"""
from __future__ import annotations
import os, re, json, subprocess, urllib.request, urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))


def get_key() -> str:
    k = os.environ.get("PEXELS_API_KEY", "").strip()
    if k:
        return k
    # fallback: .env de la multimedia-pipeline del equipo
    envp = os.path.join(_HERE, "..", "..", "..", "multimedia-pipeline", ".env")
    try:
        for line in open(os.path.abspath(envp), encoding="utf-8"):
            if line.startswith("PEXELS_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def search_clips(query: str, per_page: int = 6, key: str = "") -> list:
    """Devuelve [{'id','duration','download_url'}] de clips VERTICALES de Pexels.
    Usa CURL (no urllib): Cloudflare de Pexels flaggea la firma TLS de urllib de Python
    (401 intermitente con cuota intacta); curl pasa consistente. Causa raíz, ALICE 2026-06-24."""
    import time
    key = key or get_key()
    if not key:
        return []
    url = ("https://api.pexels.com/videos/search?"
           + urllib.parse.urlencode({"query": query, "per_page": per_page,
                                     "orientation": "portrait"}))
    data = None
    for attempt in range(4):
        r = subprocess.run(["curl", "-s", "-m", "20", "-H", f"Authorization: {key}",
                            "-A", "curl/8.0", url], capture_output=True, text=True)
        try:
            j = json.loads(r.stdout)
            if "videos" in j:
                data = j
                break
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    if data is None:
        return []
    out = []
    for v in data.get("videos", []):
        # elegir el archivo vertical de mayor resolución <= 1920 de alto (peso razonable)
        files = [f for f in v.get("video_files", []) if (f.get("height") or 0) >= 1080
                 and (f.get("width") or 0) < (f.get("height") or 1)]
        files.sort(key=lambda f: f.get("height", 0))
        if files:
            out.append({"id": v["id"], "duration": v.get("duration", 0), "download_url": files[0]["link"]})
    return out


def download(url: str, path: str) -> bool:
    subprocess.run(["curl", "-s", "-m", "90", "-A", "curl/8.0", "-o", path, url],
                   capture_output=True)
    return os.path.exists(path) and os.path.getsize(path) > 10000


def fetch_clips(queries: list, n: int, workdir: str, key: str = "") -> list:
    """Baja hasta n clips relevantes. Pacea las llamadas (Pexels tira 401 transitorio bajo
    ráfaga) y corta apenas una query devuelve suficientes."""
    import time
    key = key or get_key()
    paths, seen = [], set()
    for qi, q in enumerate(queries):
        if len(paths) >= n:
            break
        if qi:
            time.sleep(1.2)                      # pacing entre queries
        clips = search_clips(q, per_page=8, key=key)
        for c in clips:
            if len(paths) >= n or c["id"] in seen:
                continue
            seen.add(c["id"])
            p = os.path.join(workdir, f"broll_{c['id']}.mp4")
            if download(c["download_url"], p):
                paths.append(p)
    return paths


def build_background(clip_paths: list, duration: float, out_path: str, w: int = 1080, h: int = 1920) -> bool:
    """Monta los clips en un fondo vertical de 'duration' seg: cada clip scale+crop a WxH,
    recortado a un trozo, concatenados con crossfade, y el conjunto loopeado hasta cubrir
    la narración. Devuelve True si generó el MP4."""
    if not clip_paths:
        return False
    n = len(clip_paths)
    seg = max(2.5, duration / n + 0.6)   # cada clip cubre ~su parte + solape para el xfade
    xf = 0.5                              # duración del crossfade
    # 1) normalizar cada clip: scale para cubrir, crop a WxH, trim a seg, sin audio, fps 30
    norm = []
    workdir = os.path.dirname(os.path.abspath(out_path))
    for i, src in enumerate(clip_paths):
        npath = os.path.join(workdir, f"norm_{i}.mp4")
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
              f"crop={w}:{h},setsar=1,fps=30")
        r = subprocess.run(["ffmpeg", "-y", "-i", src, "-t", f"{seg:.2f}", "-an",
                            "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", npath],
                           capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(npath):
            norm.append(npath)
    if not norm:
        return False
    # 2) encadenar con crossfade (xfade secuencial entre clips)
    if len(norm) == 1:
        chained = norm[0]
    else:
        inputs = []
        for p in norm:
            inputs += ["-i", p]
        parts, cur_label, t = [], "0:v", seg - xf
        for i in range(1, len(norm)):
            nl = f"x{i}"
            parts.append(f"[{cur_label}][{i}:v]xfade=transition=fade:duration={xf}:offset={t:.2f}[{nl}]")
            cur_label, t = nl, t + seg - xf
        chained = os.path.join(workdir, "chained.mp4")
        r = subprocess.run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(parts),
                            "-map", f"[{cur_label}]", "-c:v", "libx264", "-pix_fmt", "yuv420p", chained],
                           capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(chained):
            chained = norm[0]   # fallback: primer clip si el xfade falla
    # 3) loopear hasta cubrir la duración de la narración
    r = subprocess.run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", chained, "-t", f"{duration:.2f}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path],
                       capture_output=True, text=True)
    return r.returncode == 0 and os.path.exists(out_path)


def queries_from_guion(g: dict, topic: str) -> list:
    """Keywords para buscar b-roll: hashtags + título + tema. Pexels prefiere inglés pero
    tolera ES; usamos lo disponible + el tema como red de seguridad."""
    qs = []
    for h in (g.get("hashtags") or [])[:3]:
        qs.append(re.sub(r"[#_]", " ", h).strip())
    if g.get("titulo"):
        qs.append(g["titulo"])
    qs.append(topic)
    qs.append("cinematic background")   # red de seguridad: siempre devuelve algo
    return [q for q in qs if q]


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "soccer"
    print("key:", "OK" if get_key() else "MISSING")
    print(json.dumps(search_clips(q, 4), ensure_ascii=False, indent=2))
