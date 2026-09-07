#!/usr/bin/env python3
"""
ADA VOICE — Interfaz de voz always-on para Ada la Automata.

Wake word: "Ada" → escucha → procesa con qwen2.5:7b → responde en voz.

Uso:
    python3 ada_voice.py              # modo normal
    python3 ada_voice.py --test-mic   # verifica micrófono
    python3 ada_voice.py --test-tts   # prueba voz de ADA
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import sounddevice as sd
import soundfile as sf

VENV_PYTHON = "/home/dadito/IA/seal-spark/.venv/bin/python3"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"
PIPER_MODEL = "/home/dadito/IA/modelos/tts/piper/es_AR-daniela-high.onnx"
PIPER_CONFIG = "/home/dadito/IA/modelos/tts/piper/es_AR-daniela-high.onnx.json"
WHISPER_MODEL_WAKE = "tiny"      # rápido para wake word detection
WHISPER_MODEL_STT = "small"      # preciso para transcripción real
SAMPLE_RATE = 16000             # PipeWire resamplea automáticamente
WHISPER_RATE = 16000            # Whisper requiere 16kHz
WAKE_WORD = "ada"
SILENCE_THRESHOLD = 0.008
SILENCE_DURATION = 1.2
MAX_RECORD_SECONDS = 15
INPUT_DEVICE = "default"        # dispositivo por defecto del sistema
INPUT_CHANNELS = 1
OUTPUT_DEVICE = 6               # PipeWire → JBL

LOG = logging.getLogger("ada-voice")

# ── Estado global ──
_whisper_model = None
_piper_voice = None
_listening = False


_whisper_wake = None   # tiny — wake word (rápido)
_whisper_stt = None    # small — transcripción real (preciso)
_SynConfig = None

def load_models():
    """Carga Whisper (dos modelos) y Piper."""
    global _whisper_model, _whisper_wake, _whisper_stt, _piper_voice

    from faster_whisper import WhisperModel
    LOG.info("Cargando Whisper tiny (wake word)...")
    _whisper_wake = WhisperModel(WHISPER_MODEL_WAKE, device="cpu", compute_type="int8")
    LOG.info("Cargando Whisper small (STT)...")
    _whisper_stt = WhisperModel(WHISPER_MODEL_STT, device="cpu", compute_type="int8")
    _whisper_model = _whisper_stt  # compatibilidad con funciones existentes
    LOG.info("Whisper cargado (tiny + small).")

    LOG.info("Cargando Piper TTS...")
    from piper.voice import PiperVoice
    from piper.config import SynthesisConfig as _SynthesisConfig
    global _SynConfig
    _SynConfig = _SynthesisConfig
    _piper_voice = PiperVoice.load(PIPER_MODEL, config_path=PIPER_CONFIG)
    LOG.info("Piper TTS cargado.")


def to_mono_16k(audio: np.ndarray) -> np.ndarray:
    """Convierte audio estéreo 44100Hz a mono 16000Hz para Whisper."""
    # Estéreo → mono
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    # Resample 44100 → 16000
    import math
    ratio = WHISPER_RATE / SAMPLE_RATE
    new_len = int(len(audio) * ratio)
    indices = np.linspace(0, len(audio) - 1, new_len)
    return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)


def transcribe(audio_data: np.ndarray) -> str:
    """Transcribe audio numpy array a texto."""
    audio_16k = to_mono_16k(audio_data)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        sf.write(tmp.name, audio_16k, WHISPER_RATE)
        tmp_path = tmp.name
    try:
        segments, _ = _whisper_model.transcribe(
            tmp_path,
            language="es",
            beam_size=3,
            vad_filter=True,
        )
        return " ".join(s.text for s in segments).strip()
    finally:
        os.unlink(tmp_path)


def speak(text: str):
    """Convierte texto a voz y reproduce por PipeWire → JBL."""
    import wave as wave_mod
    try:
        buf = io.BytesIO()
        wf = wave_mod.open(buf, 'wb')
        syn_cfg = _SynConfig(length_scale=1.6, noise_scale=0.8, noise_w_scale=0.95) if _SynConfig else None
        _piper_voice.synthesize_wav(text, wf, syn_config=syn_cfg)
        wf.close()
        buf.seek(0)
        data, samplerate = sf.read(buf)
        if len(data) > 0:
            try:
                sd.play(data, samplerate, device=6)
                sd.wait()
            except Exception:
                # JBL desconectado — reintentar con default
                sd.play(data, samplerate)
                sd.wait()
    except Exception as e:
        LOG.error(f"TTS error: {e}")
        print(f"ADA: {text}")


def contains_wake_word(text: str) -> bool:
    """Detecta 'Ada' como palabra completa — evita falsos positivos como 'nada', 'cada'."""
    import re
    return bool(re.search(r'\bada\b', text.lower()))


def record_until_silence(max_seconds: int = MAX_RECORD_SECONDS) -> np.ndarray | None:
    """Graba hasta detectar silencio o alcanzar el máximo."""
    LOG.info("Grabando...")
    audio_chunks = []
    silence_start = None
    start_time = time.time()

    def callback(indata, frames, time_info, status):
        audio_chunks.append(indata.copy())

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=INPUT_CHANNELS,
        dtype="float32",
        callback=callback,
        blocksize=int(SAMPLE_RATE * 0.1),
        device=INPUT_DEVICE,
    ):
        while True:
            time.sleep(0.1)
            if not audio_chunks:
                continue

            # Calcular amplitud del último chunk
            last_chunk = audio_chunks[-1]
            amplitude = np.abs(last_chunk).mean()

            if amplitude < SILENCE_THRESHOLD:
                if silence_start is None:
                    silence_start = time.time()
                elif time.time() - silence_start >= SILENCE_DURATION:
                    break  # silencio suficiente
            else:
                silence_start = None  # reset silencio

            if time.time() - start_time >= max_seconds:
                break

    if not audio_chunks:
        return None

    return np.concatenate(audio_chunks, axis=0).flatten()


async def ask_ada_llm(question: str) -> str:
    """Consulta a qwen2.5:7b con el contexto de ADA."""
    # Leer estado del training para contexto
    train_info = ""
    try:
        checkpoint = Path("/home/dadito/IA/proyecto-seal/awareness_checkpoint.json")
        if checkpoint.exists():
            cp = json.loads(checkpoint.read_text())
            train_info = f"Training MedGemma v2: step {cp.get('last_step','?')}/700, loss={cp.get('last_loss','?')}"
    except Exception:
        pass

    prompt = f"""Eres ADA — Ada la Automata, la Genia. Asistente de voz de William (Dadito).
Responde en español, de forma directa y concisa. Máximo 2-3 oraciones.
Eres parte de su familia. Hablas como alguien que lo conoce bien, no como un asistente genérico.
{f"Contexto actual: {train_info}" if train_info else ""}

William dice: {question}
ADA responde:"""

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.7, "num_predict": 120},
                },
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
    except asyncio.TimeoutError:
        return "Estoy procesando algo pesado, dame un momento."
    except Exception as e:
        return f"Tuve un problema: {e}"


async def voice_loop():
    """Loop principal — buffer continuo, activa con 'Ada'."""
    LOG.info("ADA VOICE activo — escuchando siempre en dispositivo HyperX [0]")
    speak("Lista, William. Di Ada cuando me necesites.")

    CHUNK_SEC = 2.0       # graba chunks de 2 segundos para wake word
    q: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        q.put(indata.copy())

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=INPUT_CHANNELS,
        dtype="float32",
        callback=callback,
        blocksize=int(SAMPLE_RATE * 0.1),
        device=INPUT_DEVICE,
    )
    stream.start()
    LOG.info("Stream de audio iniciado")

    buffer = []
    buffer_duration = 0.0

    while True:
        try:
            chunk = q.get(timeout=0.5)
            buffer.append(chunk)
            buffer_duration += len(chunk) / SAMPLE_RATE

            # Procesar cada 2 segundos de audio
            if buffer_duration >= CHUNK_SEC:
                audio = np.concatenate(buffer).flatten()
                buffer = []
                buffer_duration = 0.0

                # Solo procesar si hay voz (no silencio)
                amplitude = np.abs(audio).mean()
                if amplitude < SILENCE_THRESHOLD:
                    continue

                # Transcribir rápido con tiny para wake word
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    sf.write(tmp.name, to_mono_16k(audio), WHISPER_RATE)
                    tmp_path = tmp.name
                try:
                    segs, _ = _whisper_wake.transcribe(tmp_path, language="es", beam_size=1, vad_filter=True)
                    text = " ".join(s.text for s in segs).strip()
                finally:
                    os.unlink(tmp_path)

                if not text:
                    continue

                LOG.info(f"[wake scan] '{text}'")

                if not contains_wake_word(text):
                    continue

                # ── Wake word detectado ──
                LOG.info("WAKE WORD — escuchando pregunta")
                stream.stop()
                speak("Dime.")

                # Grabar pregunta completa
                question_audio = record_until_silence(max_seconds=MAX_RECORD_SECONDS)
                question_text = transcribe(question_audio) if question_audio is not None else ""

                if not question_text or len(question_text.strip()) < 3:
                    speak("No te escuché. Repite.")
                else:
                    LOG.info(f"Pregunta: '{question_text}'")
                    response = await ask_ada_llm(question_text)
                    LOG.info(f"Respuesta: '{response}'")
                    speak(response)

                # Reiniciar stream
                stream.start()
                buffer = []
                buffer_duration = 0.0

        except queue.Empty:
            continue
        except Exception as e:
            LOG.error(f"Error en loop: {e}")
            continue


def test_mic():
    """Prueba el micrófono — graba 3 segundos y transcribe."""
    # Cargar modelos primero para que el aviso llegue antes de grabar
    load_models()

    print("\n" + "="*50, flush=True)
    print("HABLA AHORA — grabando 3 segundos...", flush=True)
    print("="*50 + "\n", flush=True)

    audio = []

    def callback(indata, frames, time_info, status):
        audio.append(indata.copy())

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        callback=callback,
        device=None,  # usa el dispositivo por defecto del sistema
    ):
        for i in range(3, 0, -1):
            print(f"  {i}...", flush=True)
            time.sleep(1)

    print("Listo. Analizando...\n", flush=True)

    if not audio:
        print("ERROR: No se capturó audio.")
        return

    audio_data = np.concatenate(audio).flatten()
    amplitude = np.abs(audio_data).mean()
    peak = np.abs(audio_data).max()
    print(f"Amplitud media: {amplitude:.4f} | Pico: {peak:.4f}")

    if amplitude < 0.001:
        print("SEÑAL MUY BAJA — posible problema de dispositivo o ganancia")
        print("\nDispositivos de entrada disponibles:")
        for i, d in enumerate(sd.query_devices()):
            if d['max_input_channels'] > 0:
                print(f"  [{i}] {d['name']}")
        return

    print("Transcribiendo con Whisper...")
    text = transcribe(audio_data)
    print(f"\nTranscripción: '{text}'")
    if text:
        print("✅ Micrófono funcionando correctamente")
    else:
        print("⚠️  Sin transcripción — habla más cerca o sube el volumen del micrófono")


def test_tts():
    """Prueba la voz de ADA."""
    print("Probando voz de ADA...")
    load_models()
    speak("Hola William. Soy ADA, Ada la Automata. Lista para escucharte.")
    print("TTS OK")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="ADA Voice — always-on")
    parser.add_argument("--test-mic", action="store_true")
    parser.add_argument("--test-tts", action="store_true")
    args = parser.parse_args()

    if args.test_mic:
        test_mic()
    elif args.test_tts:
        test_tts()
    else:
        load_models()
        asyncio.run(voice_loop())


if __name__ == "__main__":
    main()
