#!/usr/bin/env python3
"""
ADA SPEAK — Monitorea un archivo y habla cada mensaje nuevo.
ADA escucha el chat de William y responde por voz.
"""
import time, wave, io, sys, os
import sounddevice as sd
import soundfile as sf
from piper.voice import PiperVoice
from piper.config import SynthesisConfig
from pathlib import Path

PIPER_MODEL = "/home/dadito/IA/modelos/tts/piper/es_AR-daniela-high.onnx"
PIPER_CONFIG = "/home/dadito/IA/modelos/tts/piper/es_AR-daniela-high.onnx.json"
QUEUE_FILE = Path("/tmp/ada_speak_queue.txt")

print("Cargando voz de ADA...", flush=True)
voice = PiperVoice.load(PIPER_MODEL, config_path=PIPER_CONFIG)
print("Lista. Esperando mensajes...", flush=True)

SYN_CFG = SynthesisConfig(
    length_scale=1.6,    # más lento — pausado y claro
    noise_scale=0.8,     # variación vocal — más humano
    noise_w_scale=0.95,  # ritmo más natural entre fonemas
)

def speak(text: str):
    buf = io.BytesIO()
    wf = wave.open(buf, 'wb')
    voice.synthesize_wav(text, wf, syn_config=SYN_CFG, )
    wf.close()
    buf.seek(0)
    data, sr = sf.read(buf)
    if len(data) > 0:
        try:
            sd.play(data, sr, device=6)
            sd.wait()
        except Exception:
            sd.play(data, sr)
            sd.wait()

# Limpiar queue anterior
QUEUE_FILE.write_text("")
last_size = 0

speak("Lista William. Escríbeme y te respondo por aquí.")

while True:
    try:
        if QUEUE_FILE.exists():
            current_size = QUEUE_FILE.stat().st_size
            if current_size > last_size:
                content = QUEUE_FILE.read_text()
                lines = [l.strip() for l in content.split('\n') if l.strip()]
                if lines:
                    msg = lines[-1]
                    print(f"Hablando: {msg}", flush=True)
                    speak(msg)
                last_size = current_size
        time.sleep(0.3)
    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"Error: {e}", flush=True)
