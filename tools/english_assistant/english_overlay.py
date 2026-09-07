#!/usr/bin/env python3
"""
English Overlay Assistant — Team SEAL v2
Overlay transparente siempre encima + streaming + CTRL push-to-talk
Captura audio sistema (WASAPI loopback)
"""

import tkinter as tk
import threading
import queue
import os
import json
import urllib.request
import wave
import tempfile

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://100.75.201.110:11435")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

SYSTEM_PROMPT = """You are helping a non-native English speaker pass an English oral exam.
They will give you exactly what the examiner said to them in English.
Provide the perfect English response they should say back.
RULES: 2-3 sentences max. Natural conversational tone. Easy to pronounce. Polite.
Output ONLY the English response — no explanations, no Spanish, no labels."""


def _transcribe_wav(wav_path: str) -> str:
    try:
        import speech_recognition as sr
    except ImportError:
        raise RuntimeError("pip install SpeechRecognition")
    r = sr.Recognizer()
    with sr.AudioFile(wav_path) as src:
        audio = r.record(src)
    return r.recognize_google(audio, language="en-US")


def _ollama_stream(question: str, on_token):
    """Stream tokens from Ollama — calls on_token(str) for each chunk."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": f"{SYSTEM_PROMPT}\n\nExaminer said: {question}",
        "stream": True,
        "options": {"temperature": 0.3, "num_predict": 150},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        for line in resp:
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            token = chunk.get("response", "")
            if token:
                on_token(token)
            if chunk.get("done"):
                break


class OverlayAssistant:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("EA")
        self.root.geometry("700x280+100+50")
        self.root.configure(bg="#0d1117")
        self.root.resizable(True, True)

        # Always on top + semi-transparent
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-alpha", 0.92)

        self.is_recording = False
        self.q = queue.Queue()
        self._stop_flag = threading.Event()
        self._ptt_active = False

        self._setup_ui()
        self.root.after(80, self._process_queue)

    def _setup_ui(self):
        # Compact header bar (draggable)
        hdr = tk.Frame(self.root, bg="#161b22", pady=4, cursor="fleur")
        hdr.pack(fill="x")
        hdr.bind("<ButtonPress-1>", self._drag_start)
        hdr.bind("<B1-Motion>", self._drag_motion)

        self.status_lbl = tk.Label(
            hdr, text="🔊 CTRL = grabar speakers",
            font=("Segoe UI", 10, "bold"), bg="#161b22", fg="#8b949e")
        self.status_lbl.pack(side="left", padx=10)

        # Minimize / close buttons
        tk.Button(hdr, text="—", font=("Segoe UI", 9), bg="#21262d", fg="#e6edf3",
                  relief="flat", padx=6, pady=1, cursor="hand2",
                  command=self.root.iconify).pack(side="right", padx=2)
        tk.Button(hdr, text="✕", font=("Segoe UI", 9), bg="#21262d", fg="#e6edf3",
                  relief="flat", padx=6, pady=1, cursor="hand2",
                  command=self.root.destroy).pack(side="right", padx=2)

        # Transcript (small)
        self.transcript_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.transcript_var,
                 font=("Segoe UI", 10), bg="#0d1117", fg="#f0883e",
                 anchor="w", wraplength=680).pack(fill="x", padx=10, pady=(6, 0))

        # Response (big + streaming)
        self.response_text = tk.Text(
            self.root, height=6, font=("Segoe UI", 17, "bold"),
            bg="#0a2d0a", fg="#56d364", insertbackground="#56d364",
            wrap=tk.WORD, relief="flat", padx=12, pady=10,
            state="disabled")
        self.response_text.pack(fill="both", expand=True, padx=8, pady=6)

        # PTT bindings — Ctrl key
        self.root.bind("<KeyPress-Control_L>", self._ptt_press)
        self.root.bind("<KeyRelease-Control_L>", self._ptt_release)
        self.root.bind("<KeyPress-Control_R>", self._ptt_press)
        self.root.bind("<KeyRelease-Control_R>", self._ptt_release)
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.focus_force()

    # ── Drag to move window ───────────────────────────────────────────

    def _drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _drag_motion(self, event):
        x = self.root.winfo_x() + (event.x - self._drag_x)
        y = self.root.winfo_y() + (event.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    # ── Push-to-talk ──────────────────────────────────────────────────

    def _ptt_press(self, event):
        if self._ptt_active or self.is_recording:
            return
        self._ptt_active = True
        self.start_recording()

    def _ptt_release(self, event):
        if not self._ptt_active:
            return
        self._ptt_active = False
        self.stop_recording()

    def start_recording(self):
        if self.is_recording:
            return
        self.is_recording = True
        self._stop_flag.clear()
        self._set_status("🔴 Grabando speakers...", "#e74c3c")
        self._clear_response()
        threading.Thread(target=self._record_thread, daemon=True).start()

    def stop_recording(self):
        self._stop_flag.set()
        self._set_status("⏳ Procesando...", "#f0883e")

    def _record_thread(self):
        try:
            try:
                import pyaudiowpatch as pyaudio
            except ImportError:
                import pyaudio

            pa = pyaudio.PyAudio()
            device_index, rate, channels = self._get_loopback(pa, pyaudio)
            if device_index is None:
                pa.terminate()
                self.q.put(("status", ("❌ Sin loopback WASAPI", "#e74c3c")))
                self.q.put(("done", None))
                return

            CHUNK = 1024
            stream = pa.open(
                format=pyaudio.paInt16, channels=channels, rate=rate,
                input=True, input_device_index=device_index, frames_per_buffer=CHUNK)

            frames = []
            while not self._stop_flag.is_set():
                try:
                    frames.append(stream.read(CHUNK, exception_on_overflow=False))
                except Exception:
                    break

            stream.stop_stream()
            stream.close()
            pa.terminate()

        except Exception as exc:
            self.q.put(("status", (f"❌ {exc}", "#e74c3c")))
            self.q.put(("done", None))
            return

        if not frames:
            self.q.put(("status", ("⚠️ Sin audio", "#f0883e")))
            self.q.put(("done", None))
            return

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        try:
            with wave.open(tmp, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(b"".join(frames))

            self.q.put(("status", ("🔍 Transcribiendo...", "#58a6ff")))
            try:
                text = _transcribe_wav(tmp)
            except Exception as exc:
                self.q.put(("status", (f"⚠️ {exc}", "#f0883e")))
                self.q.put(("done", None))
                return

            self.q.put(("transcript", text))
            self.q.put(("status", ("💭 Generando respuesta...", "#58a6ff")))

            try:
                _ollama_stream(text, lambda token: self.q.put(("token", token)))
            except Exception as exc:
                self.q.put(("status", (f"❌ Ollama: {exc}", "#e74c3c")))
                self.q.put(("done", None))
                return

            self.q.put(("status", ("✅ Listo — lee en voz alta | CTRL = nueva captura", "#3fb950")))
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass
        self.q.put(("done", None))

    @staticmethod
    def _get_loopback(pa, pyaudio):
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

    # ── Queue / UI ────────────────────────────────────────────────────

    def _process_queue(self):
        try:
            while True:
                event, data = self.q.get_nowait()
                if event == "status":
                    self._set_status(*data)
                elif event == "transcript":
                    self.transcript_var.set(f"Ellos: {data}")
                elif event == "token":
                    self._append_token(data)
                elif event == "done":
                    self.is_recording = False
        except Exception:
            pass
        self.root.after(80, self._process_queue)

    def _set_status(self, text, color="#8b949e"):
        self.status_lbl.config(text=text, fg=color)

    def _clear_response(self):
        self.response_text.config(state="normal")
        self.response_text.delete("1.0", tk.END)
        self.response_text.config(state="disabled")
        self.transcript_var.set("")

    def _append_token(self, token):
        self.response_text.config(state="normal")
        self.response_text.insert(tk.END, token)
        self.response_text.see(tk.END)
        self.response_text.config(state="disabled")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = OverlayAssistant()
    app.run()
