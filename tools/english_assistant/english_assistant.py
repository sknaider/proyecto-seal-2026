#!/usr/bin/env python3
"""
English Conversation Assistant — Team SEAL v3
Captura voz con PyAudio (mic o sistema) → Google STT → respuesta Ollama/DGX
"""

import tkinter as tk
from tkinter import scrolledtext
import threading
import queue
import sys
import os
import json
import urllib.request
import wave
import tempfile
import struct

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://100.75.201.110:11435")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

SYSTEM_PROMPT = """You are helping a non-native English speaker pass an English oral exam.
They will give you exactly what the examiner said to them in English.
Provide the perfect English response AND its Spanish phonetic pronunciation.

OUTPUT FORMAT (use exactly this, no deviations):
RESPUESTA: [2-3 sentence English response here]
PRONUNCIACIÓN: [phonetic pronunciation in Spanish syllables, e.g. "Dat saunds dificolt. Uant tu tok abaut guai?"]

RULES: Natural conversational tone. Easy to pronounce. Polite. Spanish phonetics must match the English response exactly."""


def _parse_response(raw: str):
    """Split 'RESPUESTA: ... PRONUNCIACIÓN: ...' into (english, phonetic)."""
    respuesta = raw
    pronunciacion = ""
    for line in raw.splitlines():
        if line.startswith("RESPUESTA:"):
            respuesta = line[len("RESPUESTA:"):].strip()
        elif line.startswith("PRONUNCIACIÓN:") or line.startswith("PRONUNCIACION:"):
            pronunciacion = line.split(":", 1)[1].strip()
    return respuesta, pronunciacion


def _transcribe_wav(wav_path: str) -> str:
    """Google STT via speech_recognition."""
    try:
        import speech_recognition as sr
    except ImportError:
        raise RuntimeError("pip install SpeechRecognition")
    r = sr.Recognizer()
    with sr.AudioFile(wav_path) as src:
        audio = r.record(src)
    return r.recognize_google(audio, language="en-US")


def _ollama_response(question: str) -> str:
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": f"{SYSTEM_PROMPT}\n\nExaminer said: {question}",
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 150},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read())["response"].strip()


class EnglishAssistant:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("English Assistant — SEAL v3")
        self.root.geometry("940x740")
        self.root.configure(bg="#0d1117")
        self.root.resizable(True, True)

        self.is_recording = False
        self.q = queue.Queue()
        self._stop_flag = threading.Event()
        self.mode = tk.StringVar(value="system")
        self.show_pronunciation = tk.BooleanVar(value=False)

        self._setup_ui()
        self.root.after(100, self._process_queue)

    def _setup_ui(self):
        # Header
        hdr = tk.Frame(self.root, bg="#161b22", pady=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="🎙️  English Conversation Assistant",
                 font=("Segoe UI", 20, "bold"), bg="#161b22", fg="#58a6ff").pack()
        tk.Label(hdr, text="Captura voz → respuesta perfecta en inglés (DGX Spark AI)",
                 font=("Segoe UI", 11), bg="#161b22", fg="#8b949e").pack()

        # Mode
        mf = tk.Frame(self.root, bg="#21262d", pady=10)
        mf.pack(fill="x")
        tk.Label(mf, text="Fuente:", font=("Segoe UI", 11, "bold"),
                 bg="#21262d", fg="#8b949e").pack(side="left", padx=(20, 10))
        tk.Radiobutton(mf, text="🎤 Micrófono",
                       variable=self.mode, value="mic",
                       font=("Segoe UI", 12), bg="#21262d", fg="#e6edf3",
                       selectcolor="#1f6feb", activebackground="#21262d").pack(side="left", padx=10)
        tk.Radiobutton(mf, text="🔊 Audio del sistema (speakers / videollamada)",
                       variable=self.mode, value="system",
                       font=("Segoe UI", 12), bg="#21262d", fg="#e6edf3",
                       selectcolor="#1f6feb", activebackground="#21262d").pack(side="left", padx=10)

        # Status
        self.status_var = tk.StringVar(value="✅ Listo — Mantén CTRL para grabar audio, suelta para procesar")
        sf = tk.Frame(self.root, bg="#0d1117", pady=5)
        sf.pack(fill="x")
        tk.Label(sf, textvariable=self.status_var, font=("Segoe UI", 11),
                 bg="#0d1117", fg="#aaaaaa").pack()

        # Content
        body = tk.Frame(self.root, bg="#0d1117")
        body.pack(fill="both", expand=True, padx=20, pady=8)

        tk.Label(body, text="📩  ELLOS DIJERON:",
                 font=("Segoe UI", 12, "bold"), bg="#0d1117", fg="#f0883e").pack(anchor="w")
        self.transcript_box = scrolledtext.ScrolledText(
            body, height=4, font=("Segoe UI", 13),
            bg="#161b22", fg="#e6edf3", insertbackground="white",
            wrap=tk.WORD, relief="flat", padx=10, pady=8)
        self.transcript_box.pack(fill="x", pady=(2, 10))

        tk.Label(body, text="💬  TU RESPUESTA — léela en voz alta:",
                 font=("Segoe UI", 14, "bold"), bg="#0d1117", fg="#3fb950").pack(anchor="w")
        self.response_box = scrolledtext.ScrolledText(
            body, height=6, font=("Segoe UI", 19, "bold"),
            bg="#0a2d0a", fg="#56d364", insertbackground="white",
            wrap=tk.WORD, relief="flat", padx=15, pady=12)
        self.response_box.pack(fill="x", pady=(2, 6))

        tk.Label(body, text="🔊  PRONUNCIACIÓN (léelo así):",
                 font=("Segoe UI", 12, "bold"), bg="#0d1117", fg="#58a6ff").pack(anchor="w")
        self.pronunciation_box = scrolledtext.ScrolledText(
            body, height=3, font=("Segoe UI", 15),
            bg="#0d1f33", fg="#79c0ff", insertbackground="white",
            wrap=tk.WORD, relief="flat", padx=15, pady=10)
        self.pronunciation_box.pack(fill="both", expand=True, pady=(2, 0))

        # Buttons
        bf = tk.Frame(self.root, bg="#161b22", pady=12)
        bf.pack(fill="x")

        self.rec_btn = tk.Button(
            bf, text="🔊  MANTÉN CTRL para grabar",
            font=("Segoe UI", 14, "bold"),
            bg="#238636", fg="white", relief="flat",
            padx=28, pady=13, cursor="hand2",
            command=self.start_recording)
        self.rec_btn.pack(side="left", padx=(20, 6))

        self.stop_btn = tk.Button(
            bf, text="⏹️  DETENER",
            font=("Segoe UI", 13, "bold"),
            bg="#6e3030", fg="white", relief="flat",
            padx=18, pady=13, cursor="hand2",
            state="disabled", command=self.stop_recording)
        self.stop_btn.pack(side="left", padx=6)

        tk.Button(bf, text="📋 Copiar", font=("Segoe UI", 12),
                  bg="#30363d", fg="#e6edf3", relief="flat",
                  padx=14, pady=13, cursor="hand2",
                  command=self.copy_response).pack(side="left", padx=6)

        tk.Button(bf, text="🗑️ Limpiar", font=("Segoe UI", 12),
                  bg="#30363d", fg="#e6edf3", relief="flat",
                  padx=14, pady=13, cursor="hand2",
                  command=self.clear_all).pack(side="left", padx=6)

        # Push-to-talk: mantén CTRL presionado → graba sistema; suelta → procesa
        self._ptt_active = False
        self.root.bind("<KeyPress-Control_L>", self._ptt_press)
        self.root.bind("<KeyRelease-Control_L>", self._ptt_release)
        self.root.bind("<KeyPress-Control_R>", self._ptt_press)
        self.root.bind("<KeyRelease-Control_R>", self._ptt_release)
        self.root.bind("<Escape>", lambda e: self.stop_recording())

    def toggle(self):
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def _ptt_press(self, event):
        if self._ptt_active or self.is_recording:
            return  # ignorar key-repeat
        self._ptt_active = True
        self.start_recording()

    def _ptt_release(self, event):
        if not self._ptt_active:
            return
        self._ptt_active = False
        self.stop_recording()

    # ── Recording ─────────────────────────────────────────────────────

    def start_recording(self):
        if self.is_recording:
            return
        self.is_recording = True
        self._stop_flag.clear()
        self.rec_btn.config(state="disabled", text="🔴  Grabando...", bg="#b91c1c")
        self.stop_btn.config(state="normal", bg="#e74c3c")
        mode = self.mode.get()
        self.q.put(("status", "🔊 Grabando audio sistema (speakers)... suelta CTRL para procesar"))
        threading.Thread(target=self._record_thread, args=(mode,), daemon=True).start()

    def stop_recording(self):
        self._stop_flag.set()
        self.stop_btn.config(state="disabled", bg="#6e3030")
        self.q.put(("status", "⏳  Procesando audio..."))

    def _record_thread(self, mode: str):
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            try:
                import pyaudio
            except ImportError:
                self.q.put(("error", "❌  Instala: pip install PyAudioWPatch"))
                self.q.put(("done", None))
                return

        pa = pyaudio.PyAudio()
        try:
            if mode == "system":
                device_index, rate, channels = self._get_loopback_device(pa, pyaudio)
                if device_index is None:
                    self.q.put(("error", "❌  Sin loopback. Activa 'Mezcla estéreo' en Panel de Sonido → Grabación"))
                    return
            else:
                # Default microphone
                device_index = None
                rate = 16000
                channels = 1

            CHUNK = 1024
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=CHUNK,
            )

            frames = []
            while not self._stop_flag.is_set():
                frames.append(stream.read(CHUNK, exception_on_overflow=False))

            stream.stop_stream()
            stream.close()

        except Exception as exc:
            self.q.put(("error", f"❌  Error grabación: {exc}"))
            self.q.put(("done", None))
            pa.terminate()
            return
        finally:
            pa.terminate()

        if not frames:
            self.q.put(("error", "⚠️  No se grabó nada"))
            self.q.put(("done", None))
            return

        # Write WAV
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        with wave.open(tmp, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(b"".join(frames))

        # Transcribe
        self.q.put(("status", "⏳  Transcribiendo con Google..."))
        try:
            text = _transcribe_wav(tmp)
        except Exception as exc:
            self.q.put(("error", f"⚠️  No se entendió: {exc}"))
            os.unlink(tmp)
            self.q.put(("done", None))
            return
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

        self.q.put(("transcript", text))
        self.q.put(("status", "💭  Generando respuesta (DGX Spark qwen2.5)..."))

        try:
            response = _ollama_response(text)
        except Exception as exc:
            self.q.put(("error", f"❌  Error Ollama: {exc}"))
            self.q.put(("done", None))
            return

        self.q.put(("response", response))
        self.q.put(("status", "✅  Listo — lee la respuesta en voz alta"))
        self.q.put(("done", None))

    @staticmethod
    def _get_loopback_device(pa, pyaudio):
        try:
            wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_out = pa.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            if default_out.get("isLoopbackDevice"):
                return (default_out["index"],
                        int(default_out["defaultSampleRate"]),
                        default_out["maxInputChannels"])
            for lb in pa.get_loopback_device_info_generator():
                if default_out["name"] in lb["name"]:
                    return (lb["index"],
                            int(lb["defaultSampleRate"]),
                            lb["maxInputChannels"])
        except Exception:
            pass
        return None, None, None

    # ── UI helpers ────────────────────────────────────────────────────

    def _process_queue(self):
        try:
            while True:
                event, data = self.q.get_nowait()
                if event == "transcript":
                    self.transcript_box.delete("1.0", tk.END)
                    self.transcript_box.insert(tk.END, data)
                elif event == "response":
                    respuesta, pronunciacion = _parse_response(data)
                    self.response_box.delete("1.0", tk.END)
                    self.response_box.insert(tk.END, respuesta)
                    self.pronunciation_box.delete("1.0", tk.END)
                    self.pronunciation_box.insert(tk.END, pronunciacion)
                elif event in ("status", "error"):
                    self.status_var.set(data)
                elif event == "done":
                    self.is_recording = False
                    self.rec_btn.config(
                        state="normal",
                        text="🔊  MANTÉN CTRL para grabar",
                        bg="#238636")
                    self.stop_btn.config(state="disabled", bg="#6e3030")
        except queue.Empty:
            pass
        self.root.after(100, self._process_queue)

    def copy_response(self):
        text = self.response_box.get("1.0", tk.END).strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_var.set("✅  Copiado al portapapeles")

    def clear_all(self):
        self.transcript_box.delete("1.0", tk.END)
        self.response_box.delete("1.0", tk.END)
        self.pronunciation_box.delete("1.0", tk.END)
        self.status_var.set("✅ Listo — Mantén CTRL para grabar audio, suelta para procesar")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = EnglishAssistant()
    app.run()
