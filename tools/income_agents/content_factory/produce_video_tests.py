#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate 10 local video quality tests for the income content agent.

The goal is to compare a ladder from a minimal vertical clip to a polished
short-form template without using platform credentials or paid APIs.
"""

from __future__ import annotations

import json
import os
import argparse
import re
import shutil
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path


HERE = Path(__file__).resolve().parent
OUT = HERE / "video_tests"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
PYTHON = HERE / ".venv" / "bin" / "python"
VOICE = "es-PE-CamilaNeural"
W, H, FPS = 720, 1280, 30


@dataclass
class VideoSpec:
    idx: int
    slug: str
    label: str
    level: str
    duration: float
    script: str
    title: str
    scenes: list[str]
    background: str = "gradient"
    voice: bool = False
    music: bool = False
    subtitles: bool = False
    cards: bool = False
    progress: bool = False
    end_card: bool = False


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def ass_time(seconds: float) -> str:
    cs = int(round(seconds * 100))
    h = cs // 360000
    cs %= 360000
    m = cs // 6000
    cs %= 6000
    s = cs // 100
    c = cs % 100
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def ass_escape(text: str) -> str:
    text = text.replace("{", "(").replace("}", ")")
    return text.replace("\n", r"\N")


def wrap_ass(text: str, width: int = 24) -> str:
    return r"\N".join(textwrap.wrap(text, width=width, break_long_words=False))


def drawtext_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace("%", "\\%")
    )


def source_for(spec: VideoSpec) -> str:
    if spec.background == "basic":
        return f"color=c=0x111827:s={W}x{H}:d={spec.duration}:r={FPS}"
    if spec.background == "testsrc":
        return f"testsrc2=s={W}x{H}:d={spec.duration}:r={FPS}"
    if spec.background == "premium":
        return (
            f"gradients=s={W}x{H}:r={FPS}:d={spec.duration}:"
            "c0=0x030712:c1=0x0f766e:c2=0x2563eb:c3=0xf59e0b:n=4:t=spiral:speed=0.035"
        )
    return (
        f"gradients=s={W}x{H}:r={FPS}:d={spec.duration}:"
        "c0=0x0f172a:c1=0x0e7490:c2=0x22c55e:c3=0xf8fafc:n=4:t=radial:speed=0.02"
    )


def visual_filters(spec: VideoSpec, ass_name: str) -> str:
    filters: list[str] = []
    if spec.background == "testsrc":
        filters += ["eq=saturation=0.65:contrast=1.1:brightness=-0.04"]
    else:
        filters += ["noise=alls=7:allf=t+u", "eq=saturation=1.08:contrast=1.04"]

    if spec.cards:
        filters += [
            "drawbox=x=44:y=210:w=632:h=170:color=0x000000@0.35:t=fill",
            "drawbox=x=44:y=420:w=632:h=170:color=0x000000@0.30:t=fill",
            "drawbox=x=44:y=630:w=632:h=170:color=0x000000@0.25:t=fill",
        ]
    if spec.progress:
        filters += [
            "drawbox=x=70:y=1192:w=580:h=10:color=0xffffff@0.25:t=fill",
            f"drawbox=x=70:y=1192:w='580*t/{spec.duration:.2f}':h=10:color=0x22c55e@0.95:t=fill",
        ]
    if spec.level in {"brand", "pro"}:
        filters += [
            "drawbox=x=0:y=0:w=720:h=86:color=0x030712@0.76:t=fill",
            "drawbox=x=38:y=28:w=30:h=30:color=0x22c55e@0.95:t=fill",
            (
                f"drawtext=fontfile={FONT_BOLD}:text='SEAL INCOME':"
                "fontcolor=white:fontsize=24:x=84:y=24"
            ),
        ]
    if spec.end_card:
        start = max(0.0, spec.duration - 2.4)
        filters += [
            f"drawbox=x=36:y=900:w=648:h=230:color=0x030712@0.76:t=fill:enable='gte(t,{start:.2f})'",
            (
                f"drawtext=fontfile={FONT_BOLD}:text='Comenta IA y te mando la plantilla':"
                f"fontcolor=white:fontsize=31:x=(w-text_w)/2:y=950:enable='gte(t,{start:.2f})'"
            ),
        ]
    filters.append(f"subtitles='{ass_name}'")
    return ",".join(filters)


def write_ass(spec: VideoSpec, path: Path) -> None:
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        "Style: Title,DejaVu Sans,48,&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,1,3,1,5,40,40,40,1",
        "Style: Big,DejaVu Sans,42,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,-1,0,0,0,100,100,0,0,1,3,1,5,48,48,60,1",
        "Style: Caption,DejaVu Sans,34,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,-1,0,0,0,100,100,0,0,1,3,1,2,48,48,110,1",
        "Style: Small,DejaVu Sans,24,&H00E5E7EB,&H000000FF,&H00000000,&H77000000,0,0,0,0,100,100,0,0,1,2,0,2,36,36,44,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def event(start: float, end: float, style: str, text: str, x: int | None = None, y: int | None = None, fs: int | None = None) -> None:
        tags = ""
        if x is not None and y is not None:
            tags += rf"\pos({x},{y})\an5"
        if fs:
            tags += rf"\fs{fs}"
        body = ass_escape(text)
        if tags:
            body = "{" + tags + "}" + body
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{body}")

    event(0.2, min(3.2, spec.duration), "Title", wrap_ass(spec.title, 18), W // 2, 170, 46)
    if spec.subtitles:
        chunks = list(textwrap.wrap(spec.script, width=42, break_long_words=False))
        step = max(1.5, (spec.duration - 1.5) / max(1, len(chunks)))
        t = 1.1
        for chunk in chunks:
            event(t, min(spec.duration - 0.3, t + step + 0.25), "Caption", wrap_ass(chunk, 28))
            t += step
    else:
        for idx, scene in enumerate(spec.scenes):
            start = 2.4 + idx * 2.0
            event(start, min(spec.duration - 0.5, start + 1.8), "Big", wrap_ass(scene, 21), W // 2, 540 + (idx % 3) * 190, 38)
    if spec.level in {"brand", "pro"}:
        event(0.0, spec.duration, "Small", "Plantilla local - sin publicar", W // 2, 1232, 23)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_tts(spec: VideoSpec, mp3: Path) -> None:
    run([
        str(PYTHON),
        "-m",
        "edge_tts",
        "--voice",
        VOICE,
        "--text",
        spec.script,
        "--write-media",
        str(mp3),
    ])


def make_video(spec: VideoSpec) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{spec.idx:02d}_{spec.slug}.mp4"
    with tempfile.TemporaryDirectory(dir=OUT) as tmp_raw:
        tmp = Path(tmp_raw)
        ass = tmp / "overlay.ass"
        write_ass(spec, ass)
        audio = tmp / "voice.mp3"
        inputs = ["-f", "lavfi", "-i", source_for(spec)]
        map_audio: list[str] = []
        if spec.voice:
            make_tts(spec, audio)
            inputs += ["-i", str(audio)]
            map_audio = ["-map", "0:v", "-map", "1:a"]
        elif spec.music:
            inputs += ["-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=44100:d={spec.duration}"]
            map_audio = ["-map", "0:v", "-map", "1:a"]
        else:
            inputs += ["-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100:d={spec.duration}"]
            map_audio = ["-map", "0:v", "-map", "1:a"]

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-vf",
            visual_filters(spec, ass.name),
            *map_audio,
            "-t",
            f"{spec.duration:.2f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(out),
        ]
        run(cmd, cwd=tmp)
    return inspect_video(out, spec)


def inspect_video(path: Path, spec: VideoSpec) -> dict:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration",
        "-show_entries",
        "format=size,duration",
        "-of",
        "json",
        str(path),
    ]
    data = json.loads(subprocess.check_output(cmd, text=True))
    stream = data["streams"][0]
    fmt = data["format"]
    return {
        "idx": spec.idx,
        "level": spec.level,
        "label": spec.label,
        "path": str(path),
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "duration": round(float(fmt["duration"]), 2),
        "size_kb": round(int(fmt["size"]) / 1024, 1),
    }


def specs(duration: float | None = None) -> list[VideoSpec]:
    long_mode = bool(duration and duration >= 60)
    d = float(duration) if duration else 0.0
    if long_mode:
        script = (
            "Si tienes un negocio local, este video es para ti. Hoy puedes usar inteligencia artificial "
            "sin contratar un equipo enorme. Primero, puedes responder preguntas repetidas por WhatsApp "
            "o Instagram sin estar pegado al celular. Segundo, puedes convertir cada consulta en una lista "
            "ordenada de prospectos, con nombre, necesidad y próximo paso. Tercero, puedes crear contenido "
            "corto cada semana para que más clientes te encuentren. Cuarto, puedes revisar qué videos generan "
            "mensajes reales y no solo vistas vacías. La idea no es reemplazar tu negocio; es quitarle tareas "
            "repetitivas para que vendas mejor. Si quieres una plantilla simple para empezar, comenta IA y te "
            "mando el flujo base."
        )
    else:
        script = (
            "Si tienes un negocio local, la inteligencia artificial ya puede ahorrarte horas cada semana. "
            "Primero, responde preguntas frecuentes automáticamente. Segundo, convierte mensajes en prospectos. "
            "Tercero, crea contenido corto para atraer clientes. Comenta IA y te mando una plantilla simple."
        )
    def dur(short: float) -> float:
        return d if long_mode else short
    def bg(short_bg: str) -> str:
        if long_mode and short_bg == "basic":
            return "gradient"
        return short_bg
    if long_mode:
        return [
            VideoSpec(1, "pro-ia-negocios", "Profesional IA negocios", "pro", d, script, "IA para negocios locales", ["Hook", "Sistema", "CTA"], "premium", voice=True, subtitles=True, cards=True, progress=True, end_card=True),
            VideoSpec(2, "pro-whatsapp-ventas", "Profesional WhatsApp ventas", "pro", d, script, "Convierte chats en ventas", ["Dolor", "Automatización", "Prospectos"], "premium", voice=True, subtitles=True, cards=True, progress=True, end_card=True),
            VideoSpec(3, "pro-reels-pymes", "Profesional reels pymes", "pro", d, script, "20 reels al mes", ["Contenido", "Frecuencia", "Reporte"], "premium", voice=True, subtitles=True, cards=True, progress=True, end_card=True),
            VideoSpec(4, "pro-dashboard", "Profesional dashboard", "pro", d, script, "Mide lo que vende", ["Retención", "Comentarios", "Leads"], "testsrc", voice=True, subtitles=True, cards=True, progress=True, end_card=True),
            VideoSpec(5, "pro-oferta-local", "Profesional oferta local", "pro", d, script, "Oferta IA para pymes", ["Diagnóstico", "Implementación", "Seguimiento"], "premium", voice=True, subtitles=True, cards=True, progress=True, end_card=True),
            VideoSpec(6, "pro-automatizacion", "Profesional automatización", "pro", d, script, "Automatiza tareas repetidas", ["Responder", "Ordenar", "Vender"], "premium", voice=True, subtitles=True, cards=True, progress=True, end_card=True),
            VideoSpec(7, "pro-consultoria", "Profesional consultoría", "pro", d, script, "Consultoría IA simple", ["Problema real", "Solución simple", "Resultado"], "premium", voice=True, subtitles=True, cards=True, progress=True, end_card=True),
            VideoSpec(8, "pro-plantilla", "Profesional plantilla", "pro", d, script, "Plantilla IA lista", ["Copiar", "Adaptar", "Publicar"], "testsrc", voice=True, subtitles=True, cards=True, progress=True, end_card=True),
            VideoSpec(9, "pro-caso-venta", "Profesional caso venta", "pro", d, script, "De mensaje a cliente", ["Entrada", "Filtro", "Cierre"], "premium", voice=True, subtitles=True, cards=True, progress=True, end_card=True),
            VideoSpec(10, "pro-master-template", "Profesional master template", "pro", d, script, "Sistema de ingresos IA", ["Contenido", "Métrica", "Oferta"], "premium", voice=True, subtitles=True, cards=True, progress=True, end_card=True),
        ]
    return [
        VideoSpec(1, "basic-animated", "Animado básico", "basic", dur(6), script, "IA para negocios", ["Texto limpio", "Vertical", "Movimiento"], bg("basic"), music=True, progress=long_mode),
        VideoSpec(2, "title-music", "Título + música simple", "basic", dur(7), script, "Ahorra 5 horas con IA", ["Música simple", "CTA visible"], "gradient", music=True, progress=long_mode),
        VideoSpec(3, "voiceover", "Voz neural + título", "voice", dur(11), script, "Tu asistente IA 24/7", ["Voz neural", "Fondo limpio"], "gradient", voice=True, progress=long_mode),
        VideoSpec(4, "captions", "Voz + subtítulos", "caption", dur(12), script, "3 usos de IA hoy", ["Subtítulos", "Retención"], "gradient", voice=True, subtitles=True, progress=long_mode),
        VideoSpec(5, "animated-bg", "Fondo animado + escenas", "motion", dur(10), script, "El cliente no espera", ["Responde rápido", "Captura leads", "Vende mejor"], "testsrc", music=True, progress=True),
        VideoSpec(6, "cards", "Cards de valor", "cards", dur(12), script, "Oferta para pymes", ["1. Respuestas automáticas", "2. Reels semanales", "3. Reporte simple"], "gradient", voice=True, cards=True, progress=long_mode),
        VideoSpec(7, "brand-template", "Template con marca", "brand", dur(12), script, "Sistema IA local", ["Marca arriba", "CTA abajo", "Formato reusable"], "premium", voice=True, subtitles=True, progress=True),
        VideoSpec(8, "broll-style", "Estilo b-roll generado", "motion", dur(12), script, "Contenido que vende", ["Hook", "Prueba", "Oferta"], "testsrc", voice=True, subtitles=True, progress=True),
        VideoSpec(9, "sales-reel", "Reel de venta", "brand", dur(13), script, "20 reels + IA mensual", ["Problema", "Solución", "Oferta"], "premium", voice=True, subtitles=True, cards=True, progress=True, end_card=True),
        VideoSpec(10, "professional-template", "Template profesional completo", "pro", dur(14), script, "Recupera tu tiempo con IA", ["Hook fuerte", "Sistema claro", "CTA final"], "premium", voice=True, subtitles=True, cards=True, progress=True, end_card=True),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=None, help="Duration in seconds for every generated video.")
    parser.add_argument("--out-dir", default=None, help="Output directory. Defaults to content_factory/video_tests.")
    parser.add_argument("--resolution", default=None, help="Resolution like 1080x1920. Defaults to 720x1280.")
    args = parser.parse_args()

    global OUT, W, H
    if args.out_dir:
        OUT = Path(args.out_dir).expanduser().resolve()
    if args.resolution:
        try:
            width, height = args.resolution.lower().split("x", 1)
            W, H = int(width), int(height)
        except Exception as exc:
            raise SystemExit(f"invalid --resolution {args.resolution!r}: {exc}")

    if not Path(FONT).exists() or not Path(FONT_BOLD).exists():
        raise SystemExit("missing DejaVu fonts")
    if not PYTHON.exists():
        raise SystemExit(f"missing venv python: {PYTHON}")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("missing ffmpeg/ffprobe")

    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for spec in specs(args.duration):
        print(f"[{spec.idx}/10] {spec.label} -> {spec.slug}", flush=True)
        results.append(make_video(spec))

    manifest = OUT / "manifest.json"
    manifest.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index = OUT / "README.md"
    lines = ["# Video Tests", "", "| # | Nivel | Archivo | Duracion | Peso |", "|---|---|---|---:|---:|"]
    for item in results:
        name = Path(item["path"]).name
        lines.append(f"| {item['idx']} | {item['level']} | `{name}` | {item['duration']}s | {item['size_kb']} KB |")
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(OUT), "count": len(results)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
