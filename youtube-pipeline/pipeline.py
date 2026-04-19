#!/usr/bin/env python3
"""
SEAL YouTube Auto-Pipeline v1
==============================
Pipeline autónomo para generar y subir videos de datos curiosos en español.

Stack:
  Ollama (local) → script JSON con título + facts
  ElevenLabs Flash v2.5 → narración en español
  Unsplash API → imágenes royalty-free
  FFmpeg zoompan → video Ken Burns 1920x1080
  YouTube API v3 → upload automático

Costo estimado: ~$0.10/video (solo ElevenLabs)
Capacidad: 3-5 videos/día (limitado por YouTube quota: 6 uploads/día)

Arquitectura:
  - ScriptGenerator: Ollama genera guión estructurado
  - VoiceGenerator: ElevenLabs TTS Flash v2.5
  - ImageFetcher: Unsplash API
  - VideoRenderer: FFmpeg Ken Burns
  - YouTubeUploader: YouTube Data API v3

Desarrollado por ADA — Team SEAL — noche 6 abril 2026
"""

import os
import json
import subprocess
import tempfile
import requests
from pathlib import Path
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Adam — español neutro
ELEVENLABS_MODEL = "eleven_flash_v2_5"

UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")

OUTPUT_DIR = Path("~/IA/proyecto-seal/youtube-pipeline/output").expanduser()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_DURATION_PER_IMAGE = 5  # segundos por imagen
IMAGES_PER_VIDEO = 5
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080


# ── Script Generator ────────────────────────────────────────────────────────
def generate_script(topic: str = None) -> dict:
    """Genera guión estructurado con Ollama."""
    prompt = """Genera un guión para un video de YouTube sobre datos curiosos en español.
El video debe tener exactamente 5 hechos curiosos y durar ~30 segundos cuando se lea en voz alta.

Responde SOLO con JSON válido en este formato:
{
  "titulo": "título del video (max 60 chars)",
  "descripcion": "descripción para YouTube (max 200 chars)",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "narracion": "texto completo para narrar, ~150 palabras, 5 hechos curiosos interesantes en español",
  "busqueda_imagen": "término en inglés para buscar imágenes en Unsplash"
}"""

    if topic:
        prompt = f"Tema específico: {topic}\n\n" + prompt

    response = requests.post(OLLAMA_URL, json={
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json"
    }, timeout=60)
    response.raise_for_status()

    raw = response.json().get("response", "{}")
    return json.loads(raw)


# ── Voice Generator ─────────────────────────────────────────────────────────
def generate_voice(text: str, output_path: Path) -> Path:
    """Genera narración con ElevenLabs Flash v2.5."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.3,
            "use_speaker_boost": True
        }
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()

    output_path.write_bytes(response.content)
    return output_path


# ── Image Fetcher ────────────────────────────────────────────────────────────
def fetch_images(query: str, count: int = 5) -> list[Path]:
    """Descarga imágenes de Unsplash."""
    url = "https://api.unsplash.com/search/photos"
    params = {
        "query": query,
        "per_page": count,
        "orientation": "landscape",
        "client_id": UNSPLASH_ACCESS_KEY
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()

    results = response.json().get("results", [])
    image_paths = []

    for i, photo in enumerate(results[:count]):
        img_url = photo["urls"]["regular"]
        img_response = requests.get(img_url, timeout=30)
        img_response.raise_for_status()

        img_path = OUTPUT_DIR / f"img_{i}.jpg"
        img_path.write_bytes(img_response.content)
        image_paths.append(img_path)

        # Unsplash attribution trigger (required by guidelines)
        requests.get(
            photo["links"]["download_location"],
            params={"client_id": UNSPLASH_ACCESS_KEY},
            timeout=5
        )

    return image_paths


# ── Video Renderer ───────────────────────────────────────────────────────────
def render_video(images: list[Path], audio_path: Path, output_path: Path) -> Path:
    """
    Renderiza video con efecto Ken Burns usando FFmpeg zoompan.
    Cada imagen: 5 segundos con zoom-in/out aleatorio.
    """
    # Crear lista de inputs FFmpeg
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        for img in images:
            f.write(f"file '{img.absolute()}'\n")
            f.write(f"duration {VIDEO_DURATION_PER_IMAGE}\n")
        concat_file = f.name

    # Filtro Ken Burns con variedad de movimientos
    zoompan_filters = [
        # Zoom in al centro
        f"zoompan=z='zoom+0.001':d=125:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={VIDEO_WIDTH}x{VIDEO_HEIGHT}",
        # Pan izquierda a derecha
        f"zoompan=z=1.3:d=125:x='iw*0.1+iw*0.4*(on/125)':y='ih/2-(ih/zoom/2)':s={VIDEO_WIDTH}x{VIDEO_HEIGHT}",
        # Zoom out desde esquina superior izquierda
        f"zoompan=z='1.5-0.005*on':d=125:x=0:y=0:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}",
        # Pan top-down
        f"zoompan=z=1.3:d=125:x='iw/2-(iw/zoom/2)':y='ih*0.1+ih*0.3*(on/125)':s={VIDEO_WIDTH}x{VIDEO_HEIGHT}",
        # Zoom in esquina inferior derecha
        f"zoompan=z='1+0.004*on':d=125:x='iw-iw/zoom':y='ih-ih/zoom':s={VIDEO_WIDTH}x{VIDEO_HEIGHT}",
    ]

    # Construir filtergraph
    filter_parts = []
    for i, (img, zf) in enumerate(zip(images, zoompan_filters)):
        filter_parts.append(f"[{i}:v]scale=8000:-1,{zf},fps=25[v{i}]")

    filter_parts.append(
        "".join([f"[v{i}]" for i in range(len(images))]) +
        f"concat=n={len(images)}:v=1:a=0[vout]"
    )

    filter_complex = ";".join(filter_parts)

    # Inputs
    input_args = []
    for img in images:
        input_args.extend(["-loop", "1", "-i", str(img.absolute())])

    cmd = [
        "ffmpeg", "-y",
        *input_args,
        "-i", str(audio_path.absolute()),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", f"{len(images)}:a",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-shortest",
        "-pix_fmt", "yuv420p",
        str(output_path.absolute())
    ]

    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


# ── YouTube Uploader ─────────────────────────────────────────────────────────
def upload_to_youtube(video_path: Path, title: str, description: str, tags: list) -> str:
    """
    Sube video a YouTube vía API v3.
    Requiere: YOUTUBE_CREDENTIALS_FILE con OAuth2 tokens.

    TODO: JARVIS implementa este módulo con OAuth2 flow completo.
    """
    raise NotImplementedError(
        "YouTube uploader pendiente — JARVIS implementa OAuth2 flow. "
        "Ver: google-auth-oauthlib + googleapiclient"
    )


# ── Main Pipeline ────────────────────────────────────────────────────────────
def run_pipeline(topic: str = None, upload: bool = False) -> dict:
    """Ejecuta el pipeline completo para un video."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_id = f"video_{timestamp}"

    print(f"[{video_id}] Generando script con Ollama...")
    script = generate_script(topic)
    print(f"[{video_id}] Título: {script['titulo']}")

    print(f"[{video_id}] Generando narración con ElevenLabs...")
    audio_path = OUTPUT_DIR / f"{video_id}.mp3"
    generate_voice(script["narracion"], audio_path)

    print(f"[{video_id}] Descargando imágenes de Unsplash...")
    images = fetch_images(script["busqueda_imagen"], IMAGES_PER_VIDEO)

    print(f"[{video_id}] Renderizando video con FFmpeg Ken Burns...")
    video_path = OUTPUT_DIR / f"{video_id}.mp4"
    render_video(images, audio_path, video_path)

    result = {
        "id": video_id,
        "titulo": script["titulo"],
        "video_path": str(video_path),
        "audio_path": str(audio_path),
        "script": script,
        "status": "rendered"
    }

    if upload:
        print(f"[{video_id}] Subiendo a YouTube...")
        yt_id = upload_to_youtube(
            video_path,
            script["titulo"],
            script["descripcion"],
            script["tags"]
        )
        result["youtube_id"] = yt_id
        result["status"] = "uploaded"
        print(f"[{video_id}] ✅ Subido: https://youtube.com/watch?v={yt_id}")

    # Guardar metadata
    meta_path = OUTPUT_DIR / f"{video_id}.json"
    meta_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    print(f"[{video_id}] ✅ Pipeline completo: {video_path}")
    return result


if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else None
    upload = "--upload" in sys.argv
    run_pipeline(topic=topic, upload=upload)
