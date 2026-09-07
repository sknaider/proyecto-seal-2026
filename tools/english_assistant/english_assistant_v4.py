#!/usr/bin/env python3
"""English Conversation Assistant v4 — Team SEAL"""

import tkinter as tk
from tkinter import scrolledtext
import threading
import queue
import os
import json
import urllib.request
import wave
import tempfile
import struct
import sys

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SYSTEM_PROMPT = """You are an English exam coach for a Spanish speaker.

Output EXACTLY 3 different English answers (each followed by phonetic pronunciation
for a Spanish speaker), then 3 likely follow-up questions the examiner might ask.
Do NOT use square brackets. Do NOT label sections. Do NOT add explanations.

Concrete example for question "Do you like music?":
1. Yes, I love music. It makes me happy.
📢 Yes, ai lov miusik. It meiks mi japi.
2. I really enjoy music, especially pop and jazz.
📢 Ai riili enyoi miusik, espeshali pop and yas.
3. Music is a big part of my life. I listen to it every day.
📢 Miusik is a big part of mai laif. Ai lisen tu it evri dei.
❓ What kind of music do you like the most?
❓ Do you play any musical instrument?
❓ Who is your favorite singer or band?

Phonetic guide: I→ai, you→yu, the→de, this→dis, my→mai, love→lov,
music→miusik, work→uork, really→riili, enjoy→enyoi, happy→japi, what→guot."""

QUESTIONS_PROMPT = """You are an English oral exam coach. Generate 8 typical English conversation exam questions.
Use this EXACT format for each:

❓ [question in English]
💡 [model answer — 1 or 2 sentences]
📢 [phonetics of the answer in Spanish sounds]

Topics: daily life, hobbies, family, work, travel, technology, food, future plans.
No numbering, no extra text. Just the ❓ 💡 📢 pattern repeated 8 times."""

RATE = 16000
CHUNK = 1024


def _get_input_device(pa, pyaudio):
    if sys.platform == "win32":
        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            out = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if out.get("isLoopbackDevice"):
                return out["index"], int(out["defaultSampleRate"]), out["maxInputChannels"]
            for lb in pa.get_loopback_device_info_generator():
                if out["name"] in lb["name"]:
                    return lb["index"], int(lb["defaultSampleRate"]), lb["maxInputChannels"]
        except Exception:
            pass
    try:
        info = pa.get_default_input_device_info()
        return info["index"], int(info["defaultSampleRate"]), 1
    except Exception:
        return 0, 16000, 1


def _record_stream(pa, pyaudio, device_index, rate, channels, stop_flag):
    stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=rate,
                     input=True, input_device_index=device_index, frames_per_buffer=CHUNK)
    frames = []
    while not stop_flag.is_set():
        frames.append(stream.read(CHUNK, exception_on_overflow=False))
    stream.stop_stream()
    stream.close()
    raw = b"".join(frames)
    if channels == 2:
        n = len(raw) // 2
        mono = bytearray(n)
        for i in range(0, n, 2):
            l = struct.unpack_from("<h", raw, i * 2)[0]
            r = struct.unpack_from("<h", raw, i * 2 + 2)[0]
            struct.pack_into("<h", mono, i, max(-32768, min(32767, (int(l) + int(r)) // 2)))
        raw = bytes(mono)
    if rate != RATE:
        factor = RATE / rate
        src_n = len(raw) // 2
        dst_n = int(src_n * factor)
        rs = bytearray(dst_n * 2)
        for i in range(dst_n):
            si = min(int(i / factor), src_n - 1)
            struct.pack_into("<h", rs, i * 2, struct.unpack_from("<h", raw, si * 2)[0])
        raw = bytes(rs)
    return raw


def _save_wav(pcm, path):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(pcm)


def _transcribe_wav(path):
    try:
        import speech_recognition as sr
    except ImportError:
        raise RuntimeError("pip install SpeechRecognition")
    r = sr.Recognizer()
    with sr.AudioFile(path) as src:
        audio = r.record(src)
    return r.recognize_google(audio, language="en-US")


def _ollama_call(question):
    """Blocking call — returns full response string."""
    prompt = f"Question: {question}"
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 700,
            "stop": ["\n\nQuestion:", "Question:", "\n\n\n"],
        },
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read())["response"].strip()


def _ollama_questions_call():
    """Generate practice exam questions — no input needed."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "system": QUESTIONS_PROMPT,
        "prompt": "Generate 8 English oral exam questions now.",
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 900},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["response"].strip()


def _anthropic_call(question):
    if not ANTHROPIC_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY no configurada")
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 600,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": f"Question: {question}"}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={"Content-Type": "application/json",
                 "x-api-key": ANTHROPIC_KEY,
                 "anthropic-version": "2023-06-01"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["content"][0]["text"].strip()


class EnglishAssistantPro:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("English Assistant v4 — SEAL")
        self.root.geometry("900x820")
        self.root.configure(bg="#0d1117")
        self.root.resizable(True, True)
        self.root.attributes("-topmost", True)
        self.is_busy = False
        self.q = queue.Queue()
        self._stop_flag = threading.Event()
        self._ptt_active = False
        self._setup_ui()
        self.root.after(100, self._process_queue)

    def _setup_ui(self):
        hdr = tk.Frame(self.root, bg="#161b22", pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="English Assistant v4",
                 font=("Segoe UI", 16, "bold"), bg="#161b22", fg="#58a6ff").pack(side="left", padx=12)
        tk.Label(hdr, text=f"[{OLLAMA_MODEL}]",
                 font=("Segoe UI", 10), bg="#161b22", fg="#f0883e").pack(side="left")
        tk.Label(hdr, text="[Ctrl]=graba  [Esc]=para",
                 font=("Segoe UI", 9), bg="#161b22", fg="#484f58").pack(side="right", padx=12)

        self.status_var = tk.StringVar(value="Escribe la pregunta o usa CTRL para grabar")
        tk.Label(self.root, textvariable=self.status_var, font=("Segoe UI", 10),
                 bg="#0d1117", fg="#6e7681").pack(fill="x", pady=2)

        # Text input
        if_frame = tk.Frame(self.root, bg="#0d1117")
        if_frame.pack(fill="x", padx=16, pady=(4, 0))
        tk.Label(if_frame, text="Escribe la pregunta del examinador (Enter = responder):",
                 font=("Segoe UI", 10, "bold"), bg="#0d1117", fg="#e3b341").pack(anchor="w")
        row = tk.Frame(if_frame, bg="#0d1117")
        row.pack(fill="x", pady=(2, 6))
        self.text_entry = tk.Entry(row, font=("Segoe UI", 13),
                                   bg="#161b22", fg="#e6edf3", relief="flat",
                                   insertbackground="white")
        self.text_entry.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=6)
        self.send_btn = tk.Button(row, text="Enviar", font=("Segoe UI", 11, "bold"),
                  bg="#1f6feb", fg="white", relief="flat", padx=12, pady=4,
                  cursor="hand2", command=self._send_text)
        self.send_btn.pack(side="left")
        self.text_entry.bind("<Return>", lambda e: self._send_text())

        # ELLOS
        ef = tk.Frame(self.root, bg="#0d1117")
        ef.pack(fill="x", padx=16)
        tk.Label(ef, text="ELLOS dijeron:", font=("Segoe UI", 10, "bold"),
                 bg="#0d1117", fg="#f0883e").pack(anchor="w")
        self.transcript_box = scrolledtext.ScrolledText(
            ef, height=2, font=("Segoe UI", 11),
            bg="#161b22", fg="#e6edf3", wrap=tk.WORD, relief="flat", padx=8, pady=4)
        self.transcript_box.pack(fill="x", pady=(2, 6))

        # Combined responses + phonetics
        rf = tk.Frame(self.root, bg="#0d1117")
        rf.pack(fill="both", expand=True, padx=16, pady=(0, 4))
        tk.Label(rf, text="3 RESPUESTAS (📢 pronuncia) + ❓ POSIBLES PREGUNTAS:",
                 font=("Segoe UI", 12, "bold"), bg="#0d1117", fg="#3fb950").pack(anchor="w")
        self.response_box = scrolledtext.ScrolledText(
            rf, height=20, font=("Segoe UI", 14),
            bg="#051505", fg="#56d364", wrap=tk.WORD, relief="flat", padx=12, pady=10)
        self.response_box.pack(fill="both", expand=True, pady=(2, 0))
        # Tag for phonetic lines
        self.response_box.tag_configure("phonetic", foreground="#79c0ff",
                                         font=("Segoe UI", 13, "italic"))
        self.response_box.tag_configure("answer", foreground="#56d364",
                                         font=("Segoe UI", 15, "bold"))
        self.response_box.tag_configure("question", foreground="#f0883e",
                                         font=("Segoe UI", 13, "bold italic"))

        # Buttons
        bf = tk.Frame(self.root, bg="#161b22", pady=6)
        bf.pack(fill="x")
        self.rec_btn = tk.Button(bf, text="CTRL=graba", font=("Segoe UI", 11, "bold"),
                                  bg="#238636", fg="white", relief="flat",
                                  padx=16, pady=8, cursor="hand2",
                                  command=self.start_recording)
        self.rec_btn.pack(side="left", padx=(16, 6))
        self.stop_btn = tk.Button(bf, text="DETENER", font=("Segoe UI", 11),
                                   bg="#6e3030", fg="white", relief="flat",
                                   padx=12, pady=8, state="disabled",
                                   cursor="hand2", command=self.stop_recording)
        self.stop_btn.pack(side="left", padx=4)
        self.questions_btn = tk.Button(
            bf, text="❓ Generar Preguntas", font=("Segoe UI", 11, "bold"),
            bg="#6e40c9", fg="white", relief="flat", padx=12, pady=8,
            cursor="hand2", command=self._gen_questions,
        )
        self.questions_btn.pack(side="left", padx=4)
        tk.Button(bf, text="Copiar", font=("Segoe UI", 11), bg="#30363d", fg="#e6edf3",
                  relief="flat", padx=10, pady=8, cursor="hand2",
                  command=self.copy_response).pack(side="left", padx=4)
        tk.Button(bf, text="Limpiar", font=("Segoe UI", 11), bg="#30363d", fg="#e6edf3",
                  relief="flat", padx=10, pady=8, cursor="hand2",
                  command=self.clear_all).pack(side="left", padx=4)

        for key in ("<KeyPress-Control_L>", "<KeyPress-Control_R>"):
            self.root.bind(key, self._ptt_press)
        for key in ("<KeyRelease-Control_L>", "<KeyRelease-Control_R>"):
            self.root.bind(key, self._ptt_release)
        self.root.bind("<Escape>", lambda e: self.stop_recording())

    def _gen_questions(self):
        if self.is_busy:
            return
        self.is_busy = True
        self.rec_btn.config(state="disabled")
        self.send_btn.config(state="disabled")
        self.questions_btn.config(state="disabled", text="Generando...")
        self.q.put(("clear_response", None))
        self.q.put(("status", "Generando 8 preguntas de practica..."))
        threading.Thread(target=self._questions_thread, daemon=True).start()

    def _questions_thread(self):
        try:
            result = _ollama_questions_call()
            self.q.put(("show_result", result))
            self.q.put(("status", "Practica — lee cada pregunta en voz alta"))
        except Exception as exc:
            self.q.put(("error", f"Error generando preguntas: {exc}"))
        self.q.put(("done", None))

    def _send_text(self):
        text = self.text_entry.get().strip()
        if not text or self.is_busy:
            return
        self.text_entry.delete(0, tk.END)
        self._run_ai(text)

    def _run_ai(self, text):
        self.is_busy = True
        self.rec_btn.config(state="disabled")
        self.send_btn.config(state="disabled")
        self.q.put(("transcript", text))
        self.q.put(("status", "Generando 3 respuestas con qwen2.5..."))
        self.q.put(("clear_response", None))
        threading.Thread(target=self._ai_thread, args=(text,), daemon=True).start()

    def _ai_thread(self, text):
        try:
            try:
                result = _ollama_call(text)
            except Exception as e1:
                try:
                    result = _anthropic_call(text) + "\n\n[via Anthropic]"
                except Exception as e2:
                    self.q.put(("error", f"Error: {e1} / {e2}"))
                    self.q.put(("done", None))
                    return
            self.q.put(("show_result", result))
            self.q.put(("status", "Listo — elige una respuesta y leela en voz alta"))
            self.q.put(("done", None))
        except Exception as exc:
            self.q.put(("error", f"Inesperado: {exc}"))
            self.q.put(("done", None))

    def _ptt_press(self, event):
        if self._ptt_active or self.is_busy:
            return
        self._ptt_active = True
        self.start_recording()

    def _ptt_release(self, event):
        if not self._ptt_active:
            return
        self._ptt_active = False
        self.stop_recording()

    def start_recording(self):
        if self.is_busy:
            return
        self.is_busy = True
        self._stop_flag.clear()
        self.rec_btn.config(state="disabled", text="Grabando...", bg="#b91c1c")
        self.stop_btn.config(state="normal", bg="#e74c3c")
        self.q.put(("status", "Capturando... suelta CTRL al terminar"))
        threading.Thread(target=self._record_thread, daemon=True).start()

    def stop_recording(self):
        if not self.is_busy:
            return
        self._stop_flag.set()
        self.stop_btn.config(state="disabled", bg="#6e3030")
        self.q.put(("status", "Procesando audio..."))

    def _record_thread(self):
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            try:
                import pyaudio
            except ImportError:
                self.q.put(("error", "pip install PyAudio"))
                self.q.put(("done", None))
                return
        try:
            pa = pyaudio.PyAudio()
        except Exception as exc:
            self.q.put(("error", f"Audio init: {exc}"))
            self.q.put(("done", None))
            return
        try:
            idx, rate, ch = _get_input_device(pa, pyaudio)
            pcm = _record_stream(pa, pyaudio, idx, rate, ch, self._stop_flag)
        except Exception as exc:
            self.q.put(("error", f"Audio: {exc}"))
            self.q.put(("done", None))
            pa.terminate()
            return
        finally:
            pa.terminate()
        if not pcm:
            self.q.put(("error", "No se grabo audio"))
            self.q.put(("done", None))
            return
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        _save_wav(pcm, tmp)
        self.q.put(("status", "Transcribiendo..."))
        try:
            text = _transcribe_wav(tmp)
        except Exception as exc:
            self.q.put(("error", f"STT: {exc}"))
            self.q.put(("done", None))
            return
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass
        self._run_ai(text)

    def _render_result(self, text):
        """Render result with color-coded lines into response_box."""
        self.response_box.delete("1.0", tk.END)
        question_header_inserted = False
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                self.response_box.insert(tk.END, "\n")
                continue
            if stripped.startswith("❓"):  # ❓
                if not question_header_inserted:
                    self.response_box.insert(
                        tk.END,
                        "\n--- POSIBLES PREGUNTAS DEL EXAMINADOR ---\n",
                        "question",
                    )
                    question_header_inserted = True
                self.response_box.insert(tk.END, line + "\n", "question")
            elif stripped.startswith("\U0001f4e2"):  # 📢
                self.response_box.insert(tk.END, line + "\n", "phonetic")
            else:
                self.response_box.insert(tk.END, line + "\n", "answer")
        self.response_box.see("1.0")

    def _process_queue(self):
        try:
            event, data = self.q.get_nowait()
            if event == "transcript":
                self.transcript_box.delete("1.0", tk.END)
                self.transcript_box.insert(tk.END, data)
            elif event == "clear_response":
                self.response_box.delete("1.0", tk.END)
            elif event == "show_result":
                self._render_result(data)
            elif event in ("status", "error"):
                self.status_var.set(data)
            elif event == "done":
                self.is_busy = False
                self.rec_btn.config(state="normal", text="CTRL=graba", bg="#238636")
                self.stop_btn.config(state="disabled", bg="#6e3030")
                self.send_btn.config(state="normal")
                self.questions_btn.config(state="normal", text="❓ Generar Preguntas")
            self.root.after(10, self._process_queue)
        except queue.Empty:
            self.root.after(50, self._process_queue)

    def copy_response(self):
        text = self.response_box.get("1.0", tk.END).strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_var.set("Copiado")

    def clear_all(self):
        self.transcript_box.delete("1.0", tk.END)
        self.response_box.delete("1.0", tk.END)
        self.status_var.set("Escribe la pregunta o usa CTRL para grabar")

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            self.root.mainloop()
        except Exception as exc:
            print(f"[FATAL] {exc}", file=sys.stderr)

    def _on_close(self):
        if self.is_busy:
            self._stop_flag.set()
        try:
            self.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    app = EnglishAssistantPro()
    app.run()
