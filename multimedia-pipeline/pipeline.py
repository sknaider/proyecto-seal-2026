#!/usr/bin/env python3
"""
SOUL MULTIMEDIA PIPELINE v2.1
==============================
Spark (LLM + TTS + orquestación) ↔ RTX 5090 (ComfyUI + Wan 2.2)
Desarrollado por ADA — Team SEAL — Abril 2026

ESTADO DE COMPONENTES:
  ✅ ScriptWriter      — Ollama qwen2.5:7b, genera guiones estructurados
  ✅ VoiceGenerator    — edge-tts (es-MX-DaliaNeural), funcional
  ✅ MediaCombiner     — ffmpeg, Ken Burns + concat
  ✅ FallbackPipeline  — pipeline completo sin RTX 5090 (probado, 178s)
  ✅ ImageGenerator    — PIL local en Spark (escenas temáticas con degradado)
  ✅ VideoGenerator    — Wan 2.2 I2V workflow real (API format, lightx2v 4-step)
  ❌ VoiceCloning      — Chatterbox pendiente instalación

ARQUITECTURA:
  Spark genera: guión → imágenes locales (PIL) → audio (edge-tts)
  RTX 5090 anima: imagen → Wan2.2 I2V → video corto por escena
  Si RTX 5090 no disponible: Ken Burns fallback (sin cambio de calidad de audio)

NOTAS MODELO:
  - FLUX no instalado en DADITOGAMER (solo LoRA Hyper-FLUX, falta checkpoint)
  - Wan2.2 I2V: Q5_K_M + lightx2v 4-step LoRA = ~30s/video en RTX 5090
  - ComfyUI necesita sesión interactiva para lanzar (doble click en launch_rtx5090_fix.bat)

PARA ACTIVAR RTX 5090:
  1. Abrir launch_rtx5090_fix.bat en DADITOGAMER (doble clic)
  2. Esperar ~2 min hasta que cargue el modelo
  3. python3 pipeline.py --check → verifica todo
"""

import os, sys, json, subprocess, requests, time, hashlib, threading, logging, random
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

# Auto-load .env si existe (permite configurar API keys sin exportar manualmente)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────────────────────────────────────

CONFIG = {
    # ── Spark (este nodo) ──────────────────────────────────────────────────
    "spark_tailscale_ip": "100.75.201.110",
    "spark_local_ip":     "192.168.68.200",

    # ── RTX 5090 / DADITOGAMER ─────────────────────────────────────────────
    # TODO William: instalar Tailscale en DADITOGAMER y poner la IP aquí
    "rtx5090_tailscale_ip": "100.72.212.53",  # DADITOGAMER — Tailscale activo
    "rtx5090_local_ip":     "192.168.68.62",  # DADITOGAMER — IP red local
    "rtx5090_user":         "dadito",
    "comfyui_port":         8188,

    # ── Ollama (Spark) ─────────────────────────────────────────────────────
    "ollama_url":   "http://localhost:11434/api/generate",
    "ollama_model": "qwen2.5:7b",

    # ── edge-tts (Spark) ──────────────────────────────────────────────────
    "edge_tts_bin":      "/home/dadito/IA/seal-spark/.venv/bin/edge-tts",
    "voice_jarvis":      "es-MX-JorgeNeural",    # voz masculina neutra
    "voice_ada":         "es-MX-DaliaNeural",    # voz femenina
    "voice_narrador":    "es-AR-TomasNeural",    # narrador documental

    # ── Rutas Spark ───────────────────────────────────────────────────────
    "base_dir":     Path.home() / "IA/proyecto-seal/multimedia-pipeline",
    "output_dir":   Path.home() / "IA/proyecto-seal/multimedia-pipeline/output",
    "temp_dir":     Path.home() / "IA/proyecto-seal/multimedia-pipeline/temp",
    "audio_dir":    Path.home() / "IA/proyecto-seal/multimedia-pipeline/audio",
    "scripts_dir":  Path.home() / "IA/proyecto-seal/multimedia-pipeline/scripts",
    "logs_dir":     Path.home() / "IA/proyecto-seal/multimedia-pipeline/logs",
    "voices_dir":   Path.home() / "IA/proyecto-seal/multimedia-pipeline/voices",

    # ── Límites (Protocolo de Seguridad) ──────────────────────────────────
    "max_videos_per_day":     10,
    "max_storage_gb":         50,
    "max_video_duration_sec": 300,   # 5 min máximo por video
    "max_retries":            3,
    "comfyui_timeout_sec":    600,   # 10 min por imagen/video

    # ── Imágenes stock (Unsplash / Pexels) ────────────────────────────────
    # Unsplash: https://unsplash.com/developers (gratis, 50 req/h)
    "unsplash_access_key": os.environ.get("UNSPLASH_ACCESS_KEY", ""),
    # Pexels: https://www.pexels.com/api/ (gratis, 200 req/h)
    "pexels_api_key":      os.environ.get("PEXELS_API_KEY", ""),
    "stock_image_width":   848,
    "stock_image_height":  480,
}

# Crear directorios
for key in ["output_dir","temp_dir","audio_dir","scripts_dir","logs_dir","voices_dir"]:
    CONFIG[key].mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# LOGGING — Todo queda registrado para William
# ──────────────────────────────────────────────────────────────────────────────

class SoulLogger:
    def __init__(self):
        today = datetime.now().strftime('%Y%m%d')
        self.jsonl_path = CONFIG["logs_dir"] / f"media_{today}.jsonl"
        logging.basicConfig(
            level=logging.INFO,
            format='[%(asctime)s] %(levelname)s %(message)s',
            datefmt='%H:%M:%S'
        )
        self.logger = logging.getLogger("SOUL")

    def log(self, agent: str, action: str, details, status: str = "ok"):
        entry = {
            "ts": datetime.now().isoformat(),
            "agent": agent,
            "action": action,
            "details": details if isinstance(details, dict) else {"msg": str(details)},
            "status": status,
        }
        with open(self.jsonl_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        symbol = "✅" if status == "ok" else ("⚠️" if status == "warning" else "❌")
        self.logger.info(f"{symbol} [{agent}] {action}: {str(details)[:120]}")

log = SoulLogger()


# ──────────────────────────────────────────────────────────────────────────────
# UTILIDADES
# ──────────────────────────────────────────────────────────────────────────────

def retry(fn, retries=3, delay=5, label="op"):
    """Ejecuta fn con reintentos exponenciales."""
    for attempt in range(retries):
        try:
            result = fn()
            if result is not None:
                return result
        except Exception as e:
            log.log("RETRY", label, {"attempt": attempt+1, "error": str(e)}, "warning")
            if attempt < retries - 1:
                time.sleep(delay * (2 ** attempt))
    return None

def extract_json(text: str) -> dict:
    """Extrae JSON del output de Ollama (maneja markdown fences y control chars)."""
    text = text.strip()
    import re
    if '```' in text:
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\s*```\s*$', '', text, flags=re.MULTILINE)
        text = text.strip()
    try: return json.loads(text)
    except: pass
    try: return json.loads(text.replace('\n','\\n').replace('\r',''))
    except: pass
    # Regex fallback
    d = {}
    for key in ['titulo','narracion','descripcion','scenes']:
        m = re.search(rf'"{key}"\s*:\s*"(.*?)"(?:\s*[,}}])', text, re.DOTALL)
        if m: d[key] = m.group(1).replace('\\n','\n')
    if d: return d
    raise ValueError(f"No JSON encontrado en: {text[:200]}")

def get_rtx5090_url() -> Optional[str]:
    """Retorna la URL base de ComfyUI en RTX 5090, o None si no está disponible."""
    ip = CONFIG["rtx5090_tailscale_ip"]
    if not ip:
        return None
    port = CONFIG["comfyui_port"]
    try:
        r = requests.get(f"http://{ip}:{port}/system_stats", timeout=5)
        if r.status_code == 200:
            return f"http://{ip}:{port}"
    except:
        pass
    # Intentar IP local como fallback
    try:
        local = CONFIG["rtx5090_local_ip"]
        r = requests.get(f"http://{local}:{port}/system_stats", timeout=3)
        if r.status_code == 200:
            log.log("SYSTEM", "comfyui_fallback", f"Usando IP local {local}", "warning")
            return f"http://{local}:{port}"
    except:
        pass
    return None


# ──────────────────────────────────────────────────────────────────────────────
# PASO 1: GUIÓN — Ollama genera contenido estructurado
# ──────────────────────────────────────────────────────────────────────────────

class ScriptWriter:

    TEMPLATES = {
        "curiosidades": """Eres un narrador de YouTube en español latinoamericano. Crea un guión completo sobre: {topic}

REGLAS CRÍTICAS:
- Campo "texto" en cada escena = lo que el narrador DICE EN VOZ ALTA (mínimo 60 palabras por escena)
- La narración debe ser fluida, emocional, documental al estilo National Geographic
- Total de narración: mínimo 400 palabras en los 5 "texto" combinados
- "prompt_imagen" = descripción en INGLÉS para IA generativa (escena visual, no narración)
- "prompt_video" = movimiento de cámara en inglés (slow zoom, pan, etc.)

Responde SOLO con JSON sin markdown ni código:
{{"titulo": "titulo llamativo maximo 55 caracteres",
  "descripcion": "descripcion YouTube max 200 chars",
  "tags": ["tag1","tag2","tag3","tag4","tag5"],
  "narracion": "narración completa unificada de 400+ palabras",
  "escenas": [
    {{"num": 1, "texto": "Texto narrado en voz alta para escena 1, minimo 60 palabras, emocionante y fluido, que introduce el tema y engancha al espectador desde el primer segundo con un dato sorprendente.", "prompt_imagen": "cinematic dark space nebula cosmic dust glowing particles", "prompt_video": "slow zoom out revealing cosmic scale", "emocion": "neutral"}},
    {{"num": 2, "texto": "Texto narrado en voz alta para escena 2, minimo 60 palabras, desarrolla el dato principal con detalles fascinantes que el espectador no conocía.", "prompt_imagen": "...", "prompt_video": "...", "emocion": "curious"}},
    {{"num": 3, "texto": "Texto narrado en voz alta para escena 3, minimo 60 palabras, escalada de tension o asombro, el dato más impactante.", "prompt_imagen": "...", "prompt_video": "...", "emocion": "excited"}},
    {{"num": 4, "texto": "Texto narrado en voz alta para escena 4, minimo 60 palabras, reflexión profunda sobre la implicancia del dato.", "prompt_imagen": "...", "prompt_video": "...", "emocion": "neutral"}},
    {{"num": 5, "texto": "Texto narrado en voz alta para escena 5, minimo 60 palabras, cierre emotivo y llamado a la acción que invite a suscribirse.", "prompt_imagen": "...", "prompt_video": "...", "emocion": "confident"}}
  ]
}}""",

        "demo_tecnico": """Eres un narrador técnico de YouTube en español. Genera un guión demostrando: {topic}

Responde SOLO con JSON sin markdown:
{{"titulo": "Demo: {topic} - max 55 chars",
  "descripcion": "descripcion tecnica max 200 chars",
  "tags": ["ia","tecnologia","demo","tutorial","python"],
  "narracion": "narración técnica 300+ palabras explicando el demo",
  "escenas": [
    {{"num": 1, "texto": "intro del proyecto", "prompt_imagen": "futuristic AI lab dark holographic displays", "prompt_video": "slow pan across screens", "emocion": "confident"}},
    {{"num": 2, "texto": "explicación técnica", "prompt_imagen": "code visualization neural networks glowing", "prompt_video": "zoom into code matrix", "emocion": "curious"}},
    {{"num": 3, "texto": "resultado final", "prompt_imagen": "victory achievement golden light tech", "prompt_video": "reveal shot pull back epic", "emocion": "excited"}}
  ]
}}""",
    }

    def create_script(self, topic: str, template: str = "curiosidades", agent: str = "ADA") -> Optional[dict]:
        prompt = self.TEMPLATES.get(template, self.TEMPLATES["curiosidades"]).format(topic=topic)

        def _generate():
            r = requests.post(CONFIG["ollama_url"], json={
                "model": CONFIG["ollama_model"],
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 4000, "temperature": 0.8, "repeat_penalty": 1.1}
            }, timeout=180)
            r.raise_for_status()
            raw = r.json().get("response", "")
            return extract_json(raw)

        script = retry(_generate, retries=3, label="ollama_script")
        if not script:
            log.log(agent, "script_failed", topic, "error")
            return None

        # Normalizar estructura
        if "escenas" not in script:
            narr = script.get("narracion", "")
            sentences = [s.strip() for s in narr.split('.') if len(s.strip()) > 30]
            chunk = max(1, len(sentences) // 5)
            script["escenas"] = [
                {"num": i+1, "texto": '. '.join(sentences[i*chunk:(i+1)*chunk]),
                 "prompt_imagen": f"cinematic visualization {topic} scene {i+1} dark atmosphere",
                 "prompt_video": "slow cinematic pan, particles, atmospheric",
                 "emocion": ["neutral","curious","excited","curious","confident"][i]}
                for i in range(min(5, len(sentences)//chunk or 1))
            ]

        script["id"] = hashlib.md5(f"{topic}{time.time()}".encode()).hexdigest()[:8]
        script["author"] = agent
        script["created_at"] = datetime.now().isoformat()

        path = CONFIG["scripts_dir"] / f"script_{script['id']}.json"
        path.write_text(json.dumps(script, ensure_ascii=False, indent=2))
        log.log(agent, "script_created", {"id": script["id"], "topic": topic,
                                           "escenas": len(script.get("escenas", []))})
        return script


# ──────────────────────────────────────────────────────────────────────────────
# PASO 2: IMAGEN — ComfyUI en RTX 5090
# ──────────────────────────────────────────────────────────────────────────────

# NOTA: FLUX checkpoint NO instalado en DADITOGAMER (solo tiene Hyper-FLUX LoRA).
# La generación de imágenes se hace localmente en Spark con PIL+ffmpeg.
# No se usa ComfyUI para imágenes — solo para video (Wan2.2 I2V).
FLUX_WORKFLOW = None  # Explícitamente None — no existe en DADITOGAMER


# ──────────────────────────────────────────────────────────────────────────────
# PASO 2b: IMÁGENES STOCK — Unsplash / Pexels (fuente primaria)
# ──────────────────────────────────────────────────────────────────────────────

class StockImageFetcher:
    """
    Descarga imágenes relevantes de Unsplash o Pexels.
    Prioridad: Unsplash (si hay key) → Pexels (si hay key) → None (fallback local)
    """

    # Keywords por emoción para mejorar la búsqueda
    EMOTION_KEYWORDS = {
        "neutral":    "",
        "curious":    "discovery science",
        "excited":    "dynamic energy",
        "confident":  "success professional",
        "sad":        "contemplative quiet",
    }

    def _extract_keywords(self, prompt: str, emotion: str = "neutral") -> str:
        """Extrae 3-4 keywords del prompt para búsqueda de imágenes."""
        # Eliminar palabras comunes en español
        stop_words = {"el","la","los","las","un","una","de","del","en","que",
                      "es","son","se","con","por","para","como","más","muy",
                      "esto","esta","estos","estas","entre","sobre","cada",
                      "cuando","donde","mientras","durante","puede","podría"}
        words = [w.strip(".,;:!?()\"'") for w in prompt.lower().split()]
        keywords = [w for w in words if len(w) > 4 and w not in stop_words][:4]
        emotion_kw = self.EMOTION_KEYWORDS.get(emotion, "")
        query = " ".join(keywords)
        if emotion_kw:
            query = f"{query} {emotion_kw}"
        return query.strip() or "technology future"

    def fetch_unsplash(self, query: str, dest: Path) -> Optional[Path]:
        """Descarga imagen de Unsplash API."""
        key = CONFIG["unsplash_access_key"]
        if not key:
            return None
        try:
            w, h = CONFIG["stock_image_width"], CONFIG["stock_image_height"]
            r = requests.get(
                "https://api.unsplash.com/photos/random",
                params={"query": query, "orientation": "landscape", "w": w, "h": h},
                headers={"Authorization": f"Client-ID {key}"},
                timeout=10
            )
            if r.status_code != 200:
                return None
            data = r.json()
            img_url = data["urls"].get("regular") or data["urls"].get("full")
            if not img_url:
                return None
            # Forzar tamaño
            img_url = f"{img_url}&w={w}&h={h}&fit=crop"
            resp = requests.get(img_url, timeout=30, stream=True)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            log.log("IMG", "unsplash_ok", {"query": query, "path": str(dest),
                                            "credit": data.get("user", {}).get("name", "unknown")})
            return dest
        except Exception as e:
            log.log("IMG", "unsplash_error", str(e), "warning")
            return None

    def fetch_pexels(self, query: str, dest: Path) -> Optional[Path]:
        """Descarga imagen de Pexels API."""
        key = CONFIG["pexels_api_key"]
        if not key:
            return None
        try:
            w, h = CONFIG["stock_image_width"], CONFIG["stock_image_height"]
            r = requests.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "per_page": 5, "orientation": "landscape"},
                headers={"Authorization": key},
                timeout=10
            )
            if r.status_code != 200 or not r.json().get("photos"):
                return None
            photo = r.json()["photos"][0]
            img_url = photo["src"].get("large") or photo["src"].get("original")
            if not img_url:
                return None
            resp = requests.get(img_url, timeout=30, stream=True)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            log.log("IMG", "pexels_ok", {"query": query, "path": str(dest),
                                          "credit": photo.get("photographer", "unknown")})
            return dest
        except Exception as e:
            log.log("IMG", "pexels_error", str(e), "warning")
            return None

    def fetch(self, prompt: str, scene_id: str, emotion: str = "neutral") -> Optional[Path]:
        """
        Intenta obtener imagen stock para la escena.
        Retorna Path si exitoso, None si no hay keys o falla.
        """
        query = self._extract_keywords(prompt, emotion)
        dest = CONFIG["temp_dir"] / f"stock_{scene_id}.jpg"

        # Prioridad: Unsplash → Pexels
        result = self.fetch_unsplash(query, dest)
        if result:
            return result
        result = self.fetch_pexels(query, dest)
        if result:
            return result
        return None

    def is_configured(self) -> bool:
        return bool(CONFIG["unsplash_access_key"] or CONFIG["pexels_api_key"])


class ImageGenerator:
    """
    Generador de imágenes LOCAL en Spark usando PIL + ffmpeg.
    No depende de ComfyUI ni FLUX. Genera fondo temático 848x480.
    Sirve como input para Wan2.2 I2V en RTX 5090.
    """

    # Paletas de colores por emoción/tema
    THEME_COLORS = {
        "neutral":    [("0d1117", "1c2a3a"), ("0a0a1a", "1a1a3a")],
        "curious":    [("0d1a2e", "0a3a5c"), ("071a2e", "0d3a5a")],
        "excited":    [("1a0a2e", "3a0a5c"), ("2e0a1a", "5c0a2e")],
        "confident":  [("0a1a0d", "0a3a1a"), ("0d2a0d", "1a4a1a")],
        "sad":        [("1a1a1a", "0d0d2a"), ("0d0d0d", "1a1a2e")],
    }

    def generate_local(self, prompt: str, scene_id: str, emotion: str = "neutral",
                       width: int = 848, height: int = 480) -> Optional[Path]:
        """
        Genera imagen temática localmente usando ffmpeg (gradiente + texto overlay).
        Retorna Path de imagen PNG.
        """
        import re as _re
        out = CONFIG["temp_dir"] / f"img_{scene_id}.png"

        palette = self.THEME_COLORS.get(emotion, self.THEME_COLORS["neutral"])
        c_from, c_to = random.choice(palette)

        # Extraer palabras clave del prompt para el overlay
        words = _re.sub(r'[^a-zA-Z0-9 áéíóúñ]', '', prompt)[:40].strip()
        words_esc = words.replace("'", "").replace(":", "")

        FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        font_arg = f"fontfile={FONT}:" if Path(FONT).exists() else ""

        # Gradiente diagonal con texto sutil
        vf = (
            f"gradients=size={width}x{height}:c0=0x{c_from}:c1=0x{c_to}:x0=0:y0=0:"
            f"x1={width}:y1={height}:nb_frames=1:duration=1:type=linear"
        )

        # Try gradients filter (lavfi), fallback to solid color
        def _gen_gradient():
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", vf,
                "-frames:v", "1", str(out)
            ], check=True, capture_output=True)
            return out

        def _gen_solid():
            # Solid color with drawtext overlay
            vf2 = (
                f"drawtext={font_arg}text='{words_esc}':"
                f"fontcolor=0x334455:fontsize=28:alpha=0.4:"
                f"x=(w-text_w)/2:y=(h-text_h)/2"
            ) if words_esc else "null"
            cmd = ["ffmpeg", "-y", "-f", "lavfi",
                   "-i", f"color=c=0x{c_from}:size={width}x{height}:duration=1",
                   "-vf", vf2, "-frames:v", "1", str(out)]
            subprocess.run(cmd, check=True, capture_output=True)
            return out

        result = None
        try:
            result = _gen_gradient()
        except Exception:
            result = retry(_gen_solid, retries=2, label=f"img_gen_{scene_id}")

        if result and result.exists():
            log.log("IMG", "generated_local", {"scene": scene_id, "path": str(result), "emotion": emotion})
            return result

        log.log("IMG", "local_failed", scene_id, "error")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# PASO 3: VIDEO — Wan 2.2 I2V vía ComfyUI
# ──────────────────────────────────────────────────────────────────────────────

# ─── Wan 2.2 I2V — API Format (real, listo para usar) ─────────────────────────
# Modelos: Q5_K_M (baja VRAM) + lightx2v 4-step distilled LoRA (~30s/video)
# Analizado de: D:\ComfyUI_windows\Wan2.2_I2V_14B_V4.json
# Requiere en DADITOGAMER (ya confirmados como instalados):
#   diffusion_models/Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf    (10GB)
#   text_encoders/umt5_xxl_fp16.safetensors                    (10.5GB)
#   loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors (1.14GB)
#   vae/wan_2.1_vae.safetensors  (debe estar instalado junto con WanVideoWrapper)
# INJECT points: image=INJECT_IMAGE, text=INJECT_PROMPT, seed=INJECT_SEED,
#                length=INJECT_LENGTH (frames), prefix=INJECT_PREFIX

WAN22_WORKFLOW = {
    # === Model Loaders ===
    "1": {
        "class_type": "UnetLoaderGGUF",
        "inputs": {"unet_name": "Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf"}
    },
    "2": {
        "class_type": "CLIPLoader",
        "inputs": {
            "clip_name": "umt5_xxl_fp16.safetensors",
            "type": "wan",
            "device": "cpu"   # sm_120 fix: CLIP encoder en CPU, UNet GGUF en GPU
        }
    },
    "3": {
        "class_type": "VAELoader",
        "inputs": {"vae_name": "Wan2_1_VAE_fp32.safetensors"}
    },
    # === LightX2V 4-step distilled LoRA (inference rápida) ===
    "4": {
        "class_type": "LoraLoader",
        "inputs": {
            "model": ["1", 0],
            "clip": ["2", 0],
            "lora_name": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
            "strength_model": 1.0,
            "strength_clip": 1.0
        }
    },
    # === Text Prompts ===
    "5": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "INJECT_POSITIVE_PROMPT",
            "clip": ["4", 1]
        }
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "deformed, blurry, low quality, text, watermark, signature",
            "clip": ["4", 1]
        }
    },
    # === Input Image ===
    "7": {
        "class_type": "LoadImage",
        "inputs": {"image": "INJECT_IMAGE_NAME", "upload": "image"}
    },
    # === Wan2.2 Image-to-Video ===
    "8": {
        "class_type": "WanImageToVideo",
        "inputs": {
            "positive": ["5", 0],
            "negative": ["6", 0],
            "vae": ["3", 0],
            "start_image": ["7", 0],
            "width": 848,
            "height": 480,
            "length": 49,   # ~3s @ 16fps (INJECT_LENGTH para cambiar)
            "batch_size": 1
        }
    },
    # === Sampling (4-step) ===
    "9": {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model": ["4", 0],
            "add_noise": "enable",
            "noise_seed": 42,          # INJECT_SEED
            "steps": 4,
            "cfg": 1.0,
            "sampler_name": "euler",
            "scheduler": "sgm_uniform",
            "positive": ["8", 0],
            "negative": ["8", 1],
            "latent_image": ["8", 2],
            "start_at_step": 0,
            "end_at_step": 10000,
            "return_with_leftover_noise": "disable"
        }
    },
    # === Decode ===
    "10": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["9", 0],
            "vae": ["3", 0]
        }
    },
    # === Save Video (VideoHelperSuite) ===
    "11": {
        "class_type": "VHS_VideoCombine",
        "inputs": {
            "images": ["10", 0],
            "frame_rate": 16,
            "loop_count": 0,
            "filename_prefix": "INJECT_PREFIX",
            "format": "video/h264-mp4",
            "pix_fmt": "yuv420p",
            "crf": 19,
            "save_metadata": False,
            "trim_to_audio": False,
            "pingpong": False,
            "save_output": True
        }
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# PASO 3b: VIDEO S2V — Wan 2.2 S2V (presenter con lip-sync)
# ──────────────────────────────────────────────────────────────────────────────
# Modelos GGUF disponibles en DADITOGAMER (confirmados):
#   diffusion_models/Wan2.2-S2V-14B-Q8_0.gguf
#   audio_encoders/wav2vec2-large-xlsr-53-spanish.safetensors  ← español!
#   audio_encoders/wav2vec2_large_english_fp16.safetensors
#   vae/wan2.2_vae.safetensors
# INJECT: audio=INJECT_AUDIO_NAME, image=INJECT_IMAGE_NAME,
#         positive=INJECT_POSITIVE, prefix=INJECT_PREFIX, length=INJECT_LENGTH

# ──────────────────────────────────────────────────────────────────────────────
# PASO 3c: VIDEO T2V — Wan 2.2 T2V (Texto directo → Video, sin imagen)
# ──────────────────────────────────────────────────────────────────────────────
# Modelos GGUF disponibles:
#   diffusion_models/Wan2.2-T2V-A14B-HighNoise-Q8_0.gguf
#   loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors
# INJECT: positive=INJECT_POSITIVE, length=INJECT_LENGTH, prefix=INJECT_PREFIX

WAN22_T2V_WORKFLOW = {
    "1": {
        "class_type": "UnetLoaderGGUF",
        "inputs": {"unet_name": "Wan2.2-T2V-A14B-HighNoise-Q8_0.gguf"}
    },
    "2": {
        "class_type": "CLIPLoader",
        "inputs": {"clip_name": "umt5_xxl_fp16.safetensors", "type": "wan", "device": "cpu"}  # sm_120 fix
    },
    "3": {
        "class_type": "VAELoader",
        "inputs": {"vae_name": "wan2.2_vae.safetensors"}
    },
    "4": {
        "class_type": "LoraLoader",
        "inputs": {
            "model": ["1", 0], "clip": ["2", 0],
            "lora_name": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
            "strength_model": 1.0, "strength_clip": 1.0
        }
    },
    "5": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "INJECT_POSITIVE", "clip": ["4", 1]}
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "deformed, blurry, low quality, text, watermark", "clip": ["4", 1]}
    },
    # T2V usa EmptyHunyuanLatentVideo (sin imagen de entrada)
    "7": {
        "class_type": "EmptyHunyuanLatentVideo",
        "inputs": {"width": 848, "height": 480, "length": 49, "batch_size": 1}
    },
    "8": {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model": ["4", 0], "add_noise": "enable", "noise_seed": 42,
            "steps": 4, "cfg": 1.0, "sampler_name": "euler",
            "scheduler": "sgm_uniform",
            "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
            "start_at_step": 0, "end_at_step": 10000,
            "return_with_leftover_noise": "disable"
        }
    },
    "9": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["8", 0], "vae": ["3", 0]}
    },
    "10": {
        "class_type": "VHS_VideoCombine",
        "inputs": {
            "images": ["9", 0], "frame_rate": 16, "loop_count": 0,
            "filename_prefix": "INJECT_PREFIX", "format": "video/h264-mp4",
            "pix_fmt": "yuv420p", "crf": 19, "save_metadata": False,
            "pingpong": False, "save_output": True
        }
    }
}

WAN22_S2V_WORKFLOW = {
    # === Model Loaders ===
    "1": {
        "class_type": "UnetLoaderGGUF",
        "inputs": {"unet_name": "Wan2.2-S2V-14B-Q8_0.gguf"}
    },
    "2": {
        "class_type": "CLIPLoader",
        "inputs": {
            "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "type": "wan",
            "device": "cpu"   # sm_120 fix
        }
    },
    "3": {
        "class_type": "VAELoader",
        "inputs": {"vae_name": "wan2.2_vae.safetensors"}
    },
    # === Audio: load + encode con wav2vec2 español ===
    "4": {
        "class_type": "AudioEncoderLoader",
        "inputs": {"audio_encoder_name": "wav2vec2-large-xlsr-53-spanish.safetensors"}
    },
    "5": {
        "class_type": "LoadAudio",
        "inputs": {"audio": "INJECT_AUDIO_NAME"}
    },
    "6": {
        "class_type": "AudioEncoderEncode",
        "inputs": {
            "audio_encoder": ["4", 0],
            "audio": ["5", 0]
        }
    },
    # === Presenter image ===
    "7": {
        "class_type": "LoadImage",
        "inputs": {"image": "INJECT_IMAGE_NAME", "upload": "image"}
    },
    # === Text prompts ===
    "8": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "INJECT_POSITIVE", "clip": ["2", 0]}
    },
    "9": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "deformed, distorted, blurry, low quality, watermark, text overlay",
            "clip": ["2", 0]
        }
    },
    # === S2V latent ===
    "10": {
        "class_type": "WanSoundImageToVideo",
        "inputs": {
            "positive":            ["8", 0],
            "negative":            ["9", 0],
            "vae":                 ["3", 0],
            "width":               848,
            "height":              480,
            "length":              77,        # ~4.8s @ 16fps — INJECT_LENGTH
            "batch_size":          1,
            "audio_encoder_output": ["6", 0],
            "ref_image":           ["7", 0]
        }
    },
    # === Sampling (20 steps, no Lightning LoRA para S2V) ===
    "11": {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model":                    ["1", 0],
            "add_noise":                "enable",
            "noise_seed":               42,
            "steps":                    20,
            "cfg":                      6.0,
            "sampler_name":             "euler_a",
            "scheduler":                "simple",
            "positive":                 ["10", 0],
            "negative":                 ["10", 1],
            "latent_image":             ["10", 2],
            "start_at_step":            0,
            "end_at_step":              10000,
            "return_with_leftover_noise": "disable"
        }
    },
    # === Decode ===
    "12": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["11", 0],
            "vae":     ["3", 0]
        }
    },
    # === Save video ===
    "13": {
        "class_type": "VHS_VideoCombine",
        "inputs": {
            "images":          ["12", 0],
            "frame_rate":      16,
            "loop_count":      0,
            "filename_prefix": "INJECT_PREFIX",
            "format":          "video/h264-mp4",
            "pix_fmt":         "yuv420p",
            "crf":             19,
            "save_metadata":   False,
            "pingpong":        False,
            "save_output":     True
        }
    }
}


def convert_audio_to_wav(src: Path, dest: Path) -> Optional[Path]:
    """Convierte audio (MP3/cualquier formato) a WAV 16kHz mono para wav2vec2."""
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(src),
            "-ar", "16000",   # 16kHz — requerido por wav2vec2
            "-ac", "1",       # mono
            "-c:a", "pcm_s16le",
            str(dest)
        ], check=True, capture_output=True, timeout=60)
        return dest
    except Exception as e:
        log.log("AUDIO", "wav_convert_error", str(e), "warning")
        return None


class VideoGenerator:
    """
    Wan 2.2 I2V y S2V via ComfyUI API en RTX 5090.
    - I2V: imagen → video cinematográfico (b-roll)
    - S2V: imagen presenter + audio → video con lip-sync
    """

    def __init__(self):
        self._base_url = None

    @property
    def base_url(self) -> Optional[str]:
        if not self._base_url:
            self._base_url = get_rtx5090_url()
        return self._base_url

    def is_available(self) -> bool:
        return self.base_url is not None and WAN22_WORKFLOW is not None

    def upload_image(self, local_path: Path) -> Optional[str]:
        """Sube imagen al input de ComfyUI via /upload/image (sin SSH)."""
        base = self.base_url
        if not base:
            return None
        def _upload():
            with open(local_path, 'rb') as f:
                r = requests.post(
                    f"{base}/upload/image",
                    files={"image": (local_path.name, f, "image/png")},
                    data={"type": "input", "overwrite": "true"},
                    timeout=60
                )
                r.raise_for_status()
                return r.json().get("name")
        return retry(_upload, retries=3, label="img_upload")

    def upload_audio(self, local_path: Path) -> Optional[str]:
        """Sube audio al input de ComfyUI via /upload/audio."""
        base = self.base_url
        if not base:
            return None
        def _upload():
            with open(local_path, 'rb') as f:
                mime = "audio/wav" if local_path.suffix.lower() == ".wav" else "audio/mpeg"
                r = requests.post(
                    f"{base}/upload/audio",
                    files={"audio": (local_path.name, f, mime)},
                    data={"type": "input", "overwrite": "true"},
                    timeout=60
                )
                r.raise_for_status()
                return r.json().get("name")
        return retry(_upload, retries=3, label="audio_upload")

    def generate_s2v(self, image_path: Path, audio_path: Path,
                     prompt: str = "", duration_sec: float = 4.8,
                     scene_id: str = "") -> Optional[str]:
        """
        Genera video con lip-sync usando Wan2.2 S2V.
        image_path: imagen del presenter (JPG/PNG)
        audio_path: audio de la narración (WAV 16kHz mono — usar convert_audio_to_wav)
        Retorna prompt_id para polling.
        """
        if not self.is_available():
            return None

        # Subir imagen y audio a ComfyUI
        remote_img = self.upload_image(image_path)
        remote_audio = self.upload_audio(audio_path)
        if not remote_img or not remote_audio:
            log.log("S2V", "upload_failed",
                    {"img": bool(remote_img), "audio": bool(remote_audio)}, "error")
            return None

        import copy
        wf = copy.deepcopy(WAN22_S2V_WORKFLOW)

        # Frames: 77 por chunk (~4.8s), múltiplo de 4+1
        frames = max(17, int(duration_sec * 16))
        frames = ((frames - 1) // 4) * 4 + 1
        frames = min(frames, 77)  # S2V: max 77 frames por generación sin extend

        wf["5"]["inputs"]["audio"]       = remote_audio
        wf["7"]["inputs"]["image"]       = remote_img
        wf["8"]["inputs"]["text"]        = prompt or "presenter speaking naturally, realistic lip sync, high quality cinematic"
        wf["10"]["inputs"]["length"]     = frames
        wf["11"]["inputs"]["noise_seed"] = random.randint(1, 2**32 - 1)
        wf["13"]["inputs"]["filename_prefix"] = f"seal_s2v_{scene_id}"

        base = self.base_url
        def _submit():
            r = requests.post(f"{base}/prompt", json={"prompt": wf}, timeout=30)
            r.raise_for_status()
            return r.json().get("prompt_id")

        pid = retry(_submit, retries=3, label="s2v_submit")
        if pid:
            log.log("S2V", "submitted", {"prompt_id": pid, "scene": scene_id,
                                          "frames": frames, "audio": remote_audio})
        return pid

    def generate_t2v(self, prompt: str, duration_sec: int = 3,
                     scene_id: str = "") -> Optional[str]:
        """
        Genera video desde texto (T2V) con Wan2.2 — sin imagen de entrada.
        Útil cuando no hay imagen disponible o para intro/outro abstracto.
        Retorna prompt_id para polling.
        """
        if not self.is_available():
            return None

        import copy
        wf = copy.deepcopy(WAN22_T2V_WORKFLOW)

        frames = max(17, int(duration_sec * 16))
        frames = ((frames - 1) // 4) * 4 + 1

        wf["5"]["inputs"]["text"]          = prompt
        wf["7"]["inputs"]["length"]        = frames
        wf["8"]["inputs"]["noise_seed"]    = random.randint(1, 2**32 - 1)
        wf["10"]["inputs"]["filename_prefix"] = f"seal_t2v_{scene_id}"

        base = self.base_url
        def _submit():
            r = requests.post(f"{base}/prompt", json={"prompt": wf}, timeout=30)
            r.raise_for_status()
            return r.json().get("prompt_id")

        pid = retry(_submit, retries=3, label="t2v_submit")
        if pid:
            log.log("T2V", "submitted", {"prompt_id": pid, "scene": scene_id, "frames": frames})
        return pid

    def generate(self, image_path: Path, motion_prompt: str,
                 duration_sec: int = 3, scene_id: str = "") -> Optional[str]:
        """
        Genera video I2V con Wan2.2 en RTX 5090.
        Retorna prompt_id para polling.

        duration_sec: duración deseada en segundos (se convierte a frames @16fps)
        """
        if not self.is_available():
            return None

        remote_name = self.upload_image(image_path)
        if not remote_name:
            log.log("VID", "upload_failed", str(image_path), "error")
            return None

        import copy
        wf = copy.deepcopy(WAN22_WORKFLOW)

        # INJECT: valores dinámicos en nodos específicos
        # Node 5 (positive CLIPTextEncode): text = motion_prompt
        wf["5"]["inputs"]["text"] = motion_prompt

        # Node 7 (LoadImage): image = uploaded filename
        wf["7"]["inputs"]["image"] = remote_name

        # Node 8 (WanImageToVideo): length = frames (@16fps)
        frames = max(17, int(duration_sec * 16))  # mínimo 17 frames (1s), múltiplo de 4+1
        # Wan2.2 requiere length = 4k+1 (17, 33, 49, 65, 81...)
        frames = ((frames - 1) // 4) * 4 + 1
        wf["8"]["inputs"]["length"] = frames

        # Node 9 (KSamplerAdvanced): seed aleatorio
        wf["9"]["inputs"]["noise_seed"] = random.randint(1, 2**32 - 1)

        # Node 11 (VHS_VideoCombine): filename_prefix
        wf["11"]["inputs"]["filename_prefix"] = f"seal_vid_{scene_id}"

        base = self.base_url
        def _submit():
            r = requests.post(f"{base}/prompt", json={"prompt": wf}, timeout=30)
            r.raise_for_status()
            return r.json().get("prompt_id")

        pid = retry(_submit, retries=3, label="wan22_submit")
        if pid:
            log.log("VID", "submitted", {"prompt_id": pid, "scene": scene_id,
                                          "frames": frames, "image": remote_name})
        return pid

    def poll_and_download(self, prompt_id: str, dest: Path) -> Optional[Path]:
        """Espera y descarga el video generado."""
        base = self.base_url
        start = time.time()
        timeout = CONFIG["comfyui_timeout_sec"]
        while time.time() - start < timeout:
            try:
                r = requests.get(f"{base}/history/{prompt_id}", timeout=10)
                if r.status_code == 200:
                    hist = r.json()
                    if prompt_id in hist:
                        for node_out in hist[prompt_id].get("outputs", {}).values():
                            videos = node_out.get("videos", []) or node_out.get("gifs", [])
                            if videos:
                                fname = videos[0]["filename"]
                                dl_url = f"{base}/view?filename={fname}&type=output"
                                resp = requests.get(dl_url, timeout=120, stream=True)
                                resp.raise_for_status()
                                dest.write_bytes(resp.content)
                                log.log("VID", "downloaded", str(dest))
                                return dest
            except Exception as e:
                log.log("VID", "poll_error", str(e), "warning")
            time.sleep(8)
        log.log("VID", "timeout", {"prompt_id": prompt_id}, "error")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# PASO 4: AUDIO/TTS — edge-tts (funcional) + Chatterbox (upgrade path)
# ──────────────────────────────────────────────────────────────────────────────

VOICE_MAP = {
    "JARVIS":    "voice_jarvis",
    "ADA":       "voice_ada",
    "narrador":  "voice_narrador",
}
EMOTION_RATE = {   # edge-tts: ajuste de velocidad según emoción
    "neutral":    "+0%",
    "curious":    "+5%",
    "excited":    "+12%",
    "confident":  "+3%",
    "sad":        "-10%",
}

class VoiceGenerator:

    def generate_edge_tts(self, text: str, agent: str = "narrador",
                          emotion: str = "neutral", output_path: Optional[Path] = None) -> Optional[Path]:
        """Genera audio con edge-tts (Microsoft TTS, gratuito, funciona ahora)."""
        if output_path is None:
            output_path = CONFIG["audio_dir"] / f"voice_{agent}_{int(time.time())}.mp3"

        voice_key = VOICE_MAP.get(agent, "voice_narrador")
        voice = CONFIG[voice_key]
        rate = EMOTION_RATE.get(emotion, "+0%")

        def _gen():
            subprocess.run([
                CONFIG["edge_tts_bin"],
                "--voice", voice,
                "--text", text,
                "--rate", rate,
                "--write-media", str(output_path)
            ], check=True, capture_output=True, timeout=120)
            return output_path

        result = retry(_gen, retries=3, label="edge_tts")
        if result:
            log.log(agent, "tts_ok", {"chars": len(text), "emotion": emotion, "path": str(output_path)})
        return result

    def generate_chatterbox(self, text: str, agent: str = "narrador",
                            emotion: str = "neutral", voice_ref: Optional[Path] = None,
                            output_path: Optional[Path] = None) -> Optional[Path]:
        """
        Genera audio con Chatterbox (clonación de voz local).
        Instalar: pip install chatterbox-tts
        Requiere: audio de referencia de 10+ segundos en CONFIG["voices_dir"]
        """
        if output_path is None:
            output_path = CONFIG["audio_dir"] / f"voice_{agent}_{int(time.time())}.wav"

        # Buscar audio de referencia
        if voice_ref is None:
            ref_candidates = list(CONFIG["voices_dir"].glob(f"{agent.lower()}*.wav"))
            if not ref_candidates:
                log.log(agent, "chatterbox_no_ref",
                        f"Sin audio de referencia en {CONFIG['voices_dir']}", "warning")
                return self.generate_edge_tts(text, agent, emotion, output_path.with_suffix('.mp3'))
            voice_ref = ref_candidates[0]

        try:
            from chatterbox.tts import ChatterboxTTS
            import torchaudio
            model = ChatterboxTTS.from_pretrained(device="cuda")
            exaggeration = 0.4 if emotion == "neutral" else (0.9 if emotion == "excited" else 0.65)
            wav = model.generate(text=text,
                                 audio_prompt_path=str(voice_ref),
                                 exaggeration=exaggeration)
            torchaudio.save(str(output_path), wav, model.sr)
            log.log(agent, "chatterbox_ok", {"chars": len(text), "ref": voice_ref.name})
            return output_path
        except ImportError:
            log.log(agent, "chatterbox_missing",
                    "pip install chatterbox-tts en seal-spark venv", "warning")
            return self.generate_edge_tts(text, agent, emotion, output_path.with_suffix('.mp3'))
        except Exception as e:
            log.log(agent, "chatterbox_error", str(e), "error")
            return self.generate_edge_tts(text, agent, emotion, output_path.with_suffix('.mp3'))

    def generate(self, text: str, agent: str = "narrador",
                 emotion: str = "neutral", prefer_cloning: bool = False) -> Optional[Path]:
        """Entry point: usa clonación si está disponible, edge-tts como fallback."""
        if prefer_cloning:
            return self.generate_chatterbox(text, agent, emotion)
        return self.generate_edge_tts(text, agent, emotion)


# ──────────────────────────────────────────────────────────────────────────────
# PASO 5: COMBINAR — ffmpeg en Spark
# ──────────────────────────────────────────────────────────────────────────────

class MediaCombiner:

    def combine_av(self, video_path: Path, audio_path: Path,
                   output_path: Optional[Path] = None) -> Optional[Path]:
        """Une video + audio."""
        if output_path is None:
            output_path = CONFIG["temp_dir"] / f"combined_{int(time.time())}.mp4"
        def _run():
            subprocess.run(["ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path),
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                            "-shortest", str(output_path)],
                           check=True, capture_output=True)
            return output_path
        return retry(_run, retries=2, label="ffmpeg_av")

    def concat_scenes(self, scene_videos: list, output_path: Optional[Path] = None) -> Optional[Path]:
        """Concatena escenas."""
        if not scene_videos:
            return None
        if output_path is None:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_path = CONFIG["output_dir"] / f"soul_video_{ts}.mp4"

        list_file = CONFIG["temp_dir"] / "concat_list.txt"
        list_file.write_text("\n".join(f"file '{v}'" for v in scene_videos))

        def _run():
            subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                            "-i", str(list_file), "-c", "copy", str(output_path)],
                           check=True, capture_output=True)
            return output_path
        return retry(_run, retries=2, label="ffmpeg_concat")

    def ken_burns_image(self, image_path: Path, audio_path: Path,
                        output_path: Optional[Path] = None) -> Optional[Path]:
        """
        Modo fallback: video Ken Burns desde imagen estática + audio.
        Funciona sin RTX 5090.
        """
        if output_path is None:
            output_path = CONFIG["temp_dir"] / f"kb_{image_path.stem}.mp4"

        probe = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                                "-show_streams", str(audio_path)],
                               capture_output=True, text=True)
        duration = float(json.loads(probe.stdout)["streams"][0]["duration"])
        frames = int(duration * 25)

        vf = (f"scale=8000:-1,"
              f"zoompan=z='min(zoom+0.0015,1.5)':x='iw/2-(iw/zoom/2)':"
              f"y='ih/2-(ih/zoom/2)':d={frames}:s=1920x1080,fps=25")

        def _run():
            subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
                            "-i", str(audio_path),
                            "-vf", vf,
                            "-c:v", "libx264", "-c:a", "aac",
                            "-pix_fmt", "yuv420p", "-shortest",
                            str(output_path)],
                           check=True, capture_output=True)
            return output_path
        return retry(_run, retries=2, label="ken_burns")

    def create_scene_card(self, title: str, subtitle: str,
                          bg_color: str = "050d1a") -> Optional[Path]:
        """Crea imagen de slide con texto (fallback cuando no hay ComfyUI)."""
        FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        import re as _re

        def esc(s):
            return _re.sub(r"['\[\]:]", "", s)[:55]

        out = CONFIG["temp_dir"] / f"card_{hashlib.md5(title.encode()).hexdigest()[:6]}.png"
        vf = (f"drawtext=fontfile={FONT}:text='{esc(title)}':fontcolor=0xffd700:fontsize=52:"
              f"x=(w-text_w)/2:y=440:shadowcolor=black:shadowx=3:shadowy=3,"
              f"drawtext=fontfile={FONT_REG}:text='{esc(subtitle)}':fontcolor=white:fontsize=34:"
              f"x=(w-text_w)/2:y=540:shadowcolor=black:shadowx=2:shadowy=2:"
              f"box=1:boxcolor=0x00000077:boxborderw=10")
        def _run():
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                            "-i", f"color=c=0x{bg_color}:size=1920x1080:duration=1",
                            "-vf", vf, "-frames:v", "1", str(out)],
                           check=True, capture_output=True)
            return out
        return retry(_run, retries=2, label="scene_card")


# ──────────────────────────────────────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ──────────────────────────────────────────────────────────────────────────────

class SoulMediaPipeline:

    def __init__(self):
        self.writer  = ScriptWriter()
        self.stock   = StockImageFetcher()  # Imágenes stock Unsplash/Pexels (fuente primaria)
        self.img_gen = ImageGenerator()    # Genera imágenes localmente en Spark (PIL/ffmpeg)
        self.vid_gen = VideoGenerator()    # Wan2.2 I2V en RTX 5090 (ComfyUI API)
        self.voice   = VoiceGenerator()
        self.combine = MediaCombiner()

    def check_limits(self) -> bool:
        today = datetime.now().strftime("%Y%m%d")
        today_videos = list(CONFIG["output_dir"].glob(f"soul_video_{today}*.mp4"))
        if len(today_videos) >= CONFIG["max_videos_per_day"]:
            log.log("LIMITS", "daily_max", len(today_videos), "warning")
            return False
        total_gb = sum(f.stat().st_size for f in CONFIG["output_dir"].rglob("*") if f.is_file()) / 1e9
        if total_gb >= CONFIG["max_storage_gb"]:
            log.log("LIMITS", "storage_max", f"{total_gb:.1f}GB", "warning")
            return False
        return True

    def _produce_presenter_scene(self, scene: dict, script_id: str,
                                  presenter_image: Path,
                                  prefer_cloning: bool = False) -> Optional[Path]:
        """
        Produce una escena de PRESENTER con lip-sync real usando Wan2.2 S2V.

        Flujo:
          1. Genera audio TTS para el texto de la escena
          2. Convierte audio a WAV 16kHz mono (requerido por wav2vec2)
          3. S2V en RTX 5090: imagen presenter + audio → video con lip-sync
          4. Si S2V falla → fallback Ken Burns con la imagen presenter
        """
        sid = f"{script_id}_p{scene['num']}"
        emotion = scene.get("emocion", "neutral")

        # Paso 1: Generar audio
        audio_path = self.voice.generate(
            scene["texto"], agent="narrador",
            emotion=emotion, prefer_cloning=prefer_cloning)
        if not audio_path:
            log.log("PRESENTER", "audio_failed", sid, "error")
            return None

        # Paso 2: Convertir a WAV 16kHz mono para wav2vec2
        wav_path = CONFIG["temp_dir"] / f"s2v_{sid}.wav"
        wav_file = convert_audio_to_wav(audio_path, wav_path)
        if not wav_file:
            log.log("PRESENTER", "wav_convert_failed", sid, "warning")
            # Fallback: usar Ken Burns con imagen presenter
            return self.combine.ken_burns_image(presenter_image, audio_path)

        # Paso 3: S2V en RTX 5090
        if self.vid_gen.is_available():
            prompt = (f"presenter speaking naturally, realistic lip movement, "
                      f"high quality portrait, cinematic lighting")
            # Calcular duración del audio
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-print_format", "json",
                     "-show_streams", str(audio_path)],
                    capture_output=True, text=True)
                duration = float(json.loads(probe.stdout)["streams"][0]["duration"])
            except Exception:
                duration = 4.8  # default

            pid = self.vid_gen.generate_s2v(
                presenter_image, wav_file, prompt=prompt,
                duration_sec=duration, scene_id=sid)

            if pid:
                video_path = CONFIG["temp_dir"] / f"s2v_{sid}.mp4"
                video_file = self.vid_gen.poll_and_download(pid, video_path)
                if video_file:
                    # S2V ya tiene el timing del audio, combinar audio original
                    log.log("PRESENTER", "s2v_ok", {"scene": sid, "video": str(video_file)})
                    return self.combine.combine_av(video_file, audio_path)

        # Fallback: Ken Burns con imagen presenter
        log.log("PRESENTER", "ken_burns_fallback", sid, "warning")
        return self.combine.ken_burns_image(presenter_image, audio_path)

    def _produce_scene_with_5090(self, scene: dict, script_id: str,
                                  prefer_cloning: bool = False) -> Optional[Path]:
        """
        Produce una escena usando imagen local (Spark/PIL) + Wan2.2 I2V (RTX 5090).

        Flujo:
          1. Spark genera imagen temática localmente (PIL+ffmpeg, instantáneo)
          2. PARALELO: audio (edge-tts en Spark) + upload imagen a ComfyUI
          3. RTX 5090 anima imagen con Wan2.2 I2V (~30s)
          4. Combinar video + audio con ffmpeg
          5. Fallback a Ken Burns si Wan2.2 no responde
        """
        sid = f"{script_id}_s{scene['num']}"
        emotion = scene.get("emocion", "neutral")

        # Paso 1a: intentar imagen stock (Unsplash/Pexels) — fuente primaria
        image_file = self.stock.fetch(scene["prompt_imagen"], sid, emotion=emotion)

        # Paso 1b: fallback a imagen local PIL+ffmpeg
        if not image_file:
            image_file = self.img_gen.generate_local(
                scene["prompt_imagen"], sid, emotion=emotion)

        if not image_file:
            # Último fallback: scene card con texto
            image_file = self.combine.create_scene_card(
                f"Escena {scene['num']}", scene["texto"][:50])
        if not image_file:
            return None

        # Paso 2: PARALELO — audio (Spark) + subida imagen a ComfyUI (red)
        audio_path = [None]
        vid_pid = [None]

        def _gen_audio():
            audio_path[0] = self.voice.generate(
                scene["texto"], agent="narrador",
                emotion=emotion, prefer_cloning=prefer_cloning)

        def _gen_video():
            if self.vid_gen.is_available():
                vid_pid[0] = self.vid_gen.generate(
                    image_file, scene.get("prompt_video", "slow cinematic motion"),
                    duration_sec=4, scene_id=sid)

        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_aud = ex.submit(_gen_audio)
            fut_vid = ex.submit(_gen_video)
            fut_aud.result()
            fut_vid.result()

        if not audio_path[0]:
            log.log("SCENE", "audio_failed", sid, "error")
            return None

        # Paso 3: Esperar video de Wan2.2
        if vid_pid[0]:
            video_path = CONFIG["temp_dir"] / f"wan_{sid}.mp4"
            video_file = self.vid_gen.poll_and_download(vid_pid[0], video_path)
            if video_file:
                log.log("SCENE", "wan22_ok", {"scene": sid, "video": str(video_file)})
                return self.combine.combine_av(video_file, audio_path[0])

        # Paso 4: Fallback Ken Burns (imagen local ya generada)
        log.log("SCENE", "ken_burns_fallback", sid, "warning")
        return self.combine.ken_burns_image(image_file, audio_path[0])

    def _produce_scene_fallback(self, scene: dict, script_id: str,
                                 prefer_cloning: bool = False) -> Optional[Path]:
        """Fallback completo: slide card + audio + Ken Burns (sin RTX 5090)."""
        sid = f"{script_id}_s{scene['num']}"
        colors = ["050d1a","1a0033","002211","1a1a00","001a33","050d1a"]
        color = colors[(scene["num"]-1) % len(colors)]

        card = self.combine.create_scene_card(
            f"DATO {scene['num']}", scene["texto"][:55], bg_color=color)
        if not card:
            return None

        audio = self.voice.generate(scene["texto"], agent="narrador",
                                     emotion=scene.get("emocion","neutral"),
                                     prefer_cloning=prefer_cloning)
        if not audio:
            return None

        return self.combine.ken_burns_image(card, audio)

    def produce_video(self, topic: str, template: str = "curiosidades",
                      agent: str = "ADA", prefer_cloning: bool = False,
                      mode: str = "broll") -> Optional[Path]:
        """
        Pipeline completo.
        mode="broll"      — I2V + Unsplash/PIL (rápido, b-roll cinematográfico)
        mode="presenter"  — S2V presenter con lip-sync real
                           Requiere imagen del presenter en CONFIG["voices_dir"]/presenter.jpg
                           Si no existe, descarga una de Unsplash automáticamente
        mode="abstract"   — T2V puro (texto→video sin imagen), ideal para intros/outros abstractos
        """
        if not self.check_limits():
            return None

        log.log(agent, "pipeline_start", {"topic": topic, "mode": mode})
        t0 = time.time()

        # Guión
        script = self.writer.create_script(topic, template, agent)
        if not script:
            return None

        # Modo presenter: preparar imagen base del presenter
        presenter_image = None
        if mode == "presenter":
            # Buscar imagen del presenter guardada
            presenter_candidates = list(CONFIG["voices_dir"].glob("presenter*.jpg")) + \
                                   list(CONFIG["voices_dir"].glob("presenter*.png"))
            if presenter_candidates:
                presenter_image = presenter_candidates[0]
                log.log(agent, "presenter_image_found", str(presenter_image))
            else:
                # Descargar imagen portrait de Unsplash como placeholder
                presenter_dest = CONFIG["voices_dir"] / "presenter_auto.jpg"
                presenter_image = self.stock.fetch_unsplash(
                    "professional presenter speaking camera portrait", presenter_dest)
                if not presenter_image:
                    log.log(agent, "presenter_no_image",
                            "Sin imagen de presenter — usando modo broll", "warning")
                    mode = "broll"

        # img_gen siempre disponible (local). vid_gen depende de RTX 5090 (ComfyUI).
        rtx_available = self.vid_gen.is_available()
        log.log("SYSTEM", "rtx5090_status",
                f"disponible (Wan2.2 S2V+I2V)" if rtx_available else "no disponible (Ken Burns fallback)")

        # Producir escenas
        scene_videos = []
        for scene in script.get("escenas", []):
            log.log(agent, f"scene_{scene['num']}_start", scene["texto"][:80])

            if mode == "presenter" and presenter_image:
                # Modo presenter: S2V con lip-sync real
                sv = self._produce_presenter_scene(
                    scene, script["id"], presenter_image, prefer_cloning)
            elif mode == "abstract":
                # Modo abstract: T2V puro sin imagen
                prompt_id = self.vid_gen.generate_t2v(
                    scene.get("prompt_visual", scene["texto"][:100]),
                    duration_sec=scene.get("duracion", 4),
                    scene_id=f"{script['id']}_s{scene['num']}")
                sv = self.vid_gen.poll_and_download(prompt_id, scene["num"]) if prompt_id else None
                if not sv:
                    sv = self._produce_scene_with_5090(scene, script["id"], prefer_cloning)
            else:
                # Modo broll: I2V cinematográfico (default)
                sv = self._produce_scene_with_5090(scene, script["id"], prefer_cloning)

            if sv:
                scene_videos.append(sv)
                log.log(agent, f"scene_{scene['num']}_ok", str(sv))
            else:
                log.log(agent, f"scene_{scene['num']}_failed", "", "warning")
                # Último recurso: fallback completo (slide card)
                sv2 = self._produce_scene_fallback(scene, script["id"], prefer_cloning)
                if sv2:
                    scene_videos.append(sv2)

        if not scene_videos:
            log.log(agent, "pipeline_no_scenes", topic, "error")
            return None

        # Concatenar
        final = self.combine.concat_scenes(scene_videos)
        elapsed = time.time() - t0

        if final:
            size_mb = final.stat().st_size / 1e6
            log.log(agent, "pipeline_done", {
                "topic": topic, "scenes": len(scene_videos),
                "tiempo_s": round(elapsed), "size_mb": round(size_mb, 1),
                "path": str(final)
            })
            print(f"\n{'='*55}")
            print(f"✅ VIDEO LISTO: {final.name}")
            print(f"   Escenas: {len(scene_videos)} | Tiempo: {round(elapsed)}s | Size: {size_mb:.1f}MB")
            print(f"   Ruta: {final}")
            print(f"{'='*55}\n")

            # Notificar a William vía william_channel
            self._notify_william(str(final), topic, len(scene_videos), elapsed)
        else:
            log.log(agent, "pipeline_concat_failed", topic, "error")

        # Cleanup temp
        for sv in scene_videos:
            try: sv.unlink()
            except: pass

        return final

    def _notify_william(self, video_path: str, topic: str, scenes: int, elapsed: float):
        """Notifica a William cuando un video está listo."""
        try:
            msg = {
                "from": "ADA",
                "to": "william",
                "type": "video_ready",
                "channel": "web_chat",
                "message": f"Video listo: '{topic}' — {scenes} escenas, {round(elapsed)}s. Ruta: {video_path}"
            }
            requests.post("http://localhost:8765/api/agents/send",
                         json=msg, timeout=5)
        except:
            pass  # No es crítico


# ──────────────────────────────────────────────────────────────────────────────
# CHECK DE SISTEMA
# ──────────────────────────────────────────────────────────────────────────────

def system_check():
    print("\n=== SOUL MULTIMEDIA PIPELINE v2.1 — System Check ===\n")

    # Checks críticos (sin ellos no funciona ni el fallback)
    critical = {
        "Ollama (Spark)":  lambda: requests.get("http://localhost:11434/api/tags", timeout=5).status_code == 200,
        "edge-tts":        lambda: Path(CONFIG["edge_tts_bin"]).exists(),
        "ffmpeg":          lambda: subprocess.run(["ffmpeg","-version"], capture_output=True).returncode == 0,
        "DejaVu fonts":    lambda: Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf").exists(),
    }
    # Checks opcionales (mejoran calidad pero no son bloqueantes)
    optional = {
        "RTX 5090 / ComfyUI": lambda: get_rtx5090_url() is not None,
        "Wan2.2 workflow":     lambda: WAN22_WORKFLOW is not None,
        "PIL (local images)":  lambda: __import__("PIL"),
        "Unsplash API":        lambda: bool(CONFIG["unsplash_access_key"]),
        "Pexels API":          lambda: bool(CONFIG["pexels_api_key"]),
        "Chatterbox TTS":      lambda: __import__("chatterbox"),
    }

    all_critical = True
    print("  CRÍTICOS:")
    for name, check in critical.items():
        try:
            ok = check()
            status = "✅ OK" if ok else "❌ FALTA"
            if not ok: all_critical = False
        except Exception as e:
            status = f"❌ {str(e)[:40]}"
            all_critical = False
        print(f"    {status:<25} {name}")

    print("\n  OPCIONALES (mejoran calidad):")
    rtx_ok = False
    for name, check in optional.items():
        try:
            ok = check()
            status = "✅ OK" if ok else "⚠️  No disponible"
            if name == "RTX 5090 / ComfyUI" and ok:
                rtx_ok = True
        except ImportError:
            status = "⚠️  No instalado"
        except Exception as e:
            status = f"⚠️  {str(e)[:40]}"
        print(f"    {status:<25} {name}")

    print()
    if not all_critical:
        print("  ❌ Sistema NO listo — faltan componentes críticos\n")
    elif rtx_ok:
        print("  ✅ MODO COMPLETO: Spark + RTX 5090 (Wan2.2 I2V activo)\n")
    else:
        print("  ⚠️  MODO FALLBACK: Solo Spark (Ken Burns, sin Wan2.2)\n")
        print("     Para activar RTX 5090: abrir launch_rtx5090_fix.bat en DADITOGAMER\n")


# ──────────────────────────────────────────────────────────────────────────────
# ENTRYPOINT
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Soul Multimedia Pipeline")
    parser.add_argument("--check", action="store_true", help="System check")
    parser.add_argument("--topic", type=str, default="curiosidades del universo")
    parser.add_argument("--template", choices=["curiosidades","demo_tecnico"], default="curiosidades")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--cloning", action="store_true", help="Usar clonación de voz (Chatterbox)")
    parser.add_argument("--mode", choices=["broll","presenter","abstract"], default="broll",
                        help="broll=I2V cinematográfico | presenter=S2V lip-sync real")
    args = parser.parse_args()

    if args.check:
        system_check()
        sys.exit(0)

    pipeline = SoulMediaPipeline()
    video = pipeline.produce_video(
        topic=args.topic,
        template=args.template,
        agent=args.agent,
        prefer_cloning=args.cloning,
        mode=args.mode
    )
    if not video:
        print("Pipeline falló. Revisar logs en:", CONFIG["logs_dir"])
        sys.exit(1)
