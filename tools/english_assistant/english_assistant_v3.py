#!/usr/bin/env python3
"""
English Conversation Assistant v3 — Team SEAL
Gemma 4 (gemma4-r2) + pronunciación fonética en español
Ctrl = push-to-talk sistema audio | always-on-top | streaming | fallback Anthropic
"""

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

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://100.75.201.110:11435")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SYSTEM_PROMPT = """You are an English exam coach. The student needs to respond to what an examiner said.

TASK: Write exactly 7 lines, nothing else:
LINE 1: Best natural 2-sentence answer in English.
LINE 2: Phonetics of Line 1 as it sounds to a Spanish speaker, starting with 📢
LINE 3: ALT1: [shorter alternative answer, 1 sentence]
LINE 4: ALT2: [another alternative answer, 1 sentence, different tone]
LINE 5: ALT3: [a third alternative, more formal or polite tone]
LINE 6: Q1: [likely follow-up question the examiner might ask next]
LINE 7: Q2: [another likely follow-up question]

EXAMPLE OUTPUT:
Yes, I really enjoy music. It helps me relax after a long day.
📢 Ies, ai rioli enioi miusic. It jelps mi rilax after a long dei.
ALT1: Music is a big part of my life, especially pop and jazz.
ALT2: I do like music, though I prefer listening to podcasts more.
ALT3: I truly appreciate music as it brings joy to my daily routine.
Q1: What kind of music do you like?
Q2: Do you play any musical instruments?"""


# ── Audio utilities ───────────────────────────────────────────────────

def _transcribe_wav(path: str) -> str:
    try:
        import speech_recognition as sr
    except ImportError:
        raise RuntimeError("pip install SpeechRecognition")
    r = sr.Recognizer()
    with sr.AudioFile(path) as src:
        audio = r.record(src)
    return r.recognize_google(audio, language="en-US")


def _get_loopback(pa, pyaudio):
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
    return None, 16000, 1


RATE = 16000
CHUNK = 1024


def _record_stream(pa, pyaudio, device_index, rate, channels, stop_flag) -> bytes:
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


def _save_wav(pcm: bytes, path: str):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(pcm)


# ── AI response ───────────────────────────────────────────────────────

def _ollama_stream(question: str, on_chunk, on_done, on_error):
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": f"Examiner said: {question}\n\nWrite exactly 7 lines:",
        "stream": True,
        "options": {"temperature": 0.4, "num_predict": 300},
    }).encode()
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            for raw_line in resp:
                if not raw_line.strip():
                    continue
                chunk = json.loads(raw_line)
                token = chunk.get("response", "")
                if token:
                    on_chunk(token)
                if chunk.get("done"):
                    break
        on_done()
    except Exception as exc:
        on_error(str(exc))


def _anthropic_response(question: str) -> str:
    if not ANTHROPIC_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY no configurada")
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 250,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": question}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())["content"][0]["text"].strip()


# ── App ───────────────────────────────────────────────────────────────

class EnglishAssistantPro:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("English Assistant v4 — SEAL [Gemma 4]")
        self.root.geometry("900x820")
        self.root.configure(bg="#0d1117")
        self.root.resizable(True, True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 1.0)
        self.is_recording = False
        self.q = queue.Queue()
        self._stop_flag = threading.Event()
        self._ptt_active = False
        self._ghost_mode = False

        self._setup_ui()
        self.root.after(100, self._process_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_ui(self):
        hdr = tk.Frame(self.root, bg="#161b22", pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="🎙️  English Assistant v4  [Gemma 4]",
                 font=("Segoe UI", 16, "bold"), bg="#161b22", fg="#58a6ff").pack(side="left", padx=12)
        tk.Label(hdr, text="+ pronunciación",
                 font=("Segoe UI", 10), bg="#161b22", fg="#f0883e").pack(side="left")
        tk.Button(hdr, text="👻 Ghost", font=("Segoe UI", 10),
                  bg="#30363d", fg="#8b949e", relief="flat", padx=8, pady=4,
                  cursor="hand2", command=self.toggle_ghost).pack(side="right", padx=8)
        tk.Label(hdr, text="[Ctrl]=graba  [Esc]=para",
                 font=("Segoe UI", 9), bg="#161b22", fg="#484f58").pack(side="right", padx=4)

        self.status_var = tk.StringVar(value="✅ Mantén CTRL para grabar sistema, suelta para respuesta")
        sf = tk.Frame(self.root, bg="#0d1117", pady=3)
        sf.pack(fill="x")
        self.status_lbl = tk.Label(sf, textvariable=self.status_var, font=("Segoe UI", 10),
                                   bg="#0d1117", fg="#6e7681")
        self.status_lbl.pack()

        self.transcript_frame = tk.Frame(self.root, bg="#0d1117")
        self.transcript_frame.pack(fill="x", padx=16)
        ellos_hdr = tk.Frame(self.transcript_frame, bg="#0d1117")
        ellos_hdr.pack(fill="x")
        tk.Label(ellos_hdr, text="📩 ELLOS:  (escribe aqui o usa CTRL)",
                 font=("Segoe UI", 11, "bold"), bg="#0d1117", fg="#f0883e").pack(side="left", anchor="w")
        tk.Button(ellos_hdr, text="📤 ENVIAR [Enter]", font=("Segoe UI", 11, "bold"),
                  bg="#1f6feb", fg="white", relief="flat", padx=12, pady=4,
                  cursor="hand2", command=self.submit_text).pack(side="right")
        self.transcript_box = scrolledtext.ScrolledText(
            self.transcript_frame, height=3, font=("Segoe UI", 12),
            bg="#161b22", fg="#e6edf3", wrap=tk.WORD, relief="flat", padx=8, pady=6)
        self.transcript_box.pack(fill="x", pady=(2, 8))
        self.transcript_box.bind("<Return>", lambda e: (self.submit_text(), "break")[1])

        self.mid = tk.Frame(self.root, bg="#0d1117")
        mid = self.mid
        mid.pack(fill="both", expand=True, padx=16, pady=(0, 2))
        tk.Label(mid, text="💬 TU RESPUESTA:",
                 font=("Segoe UI", 12, "bold"), bg="#0d1117", fg="#3fb950").pack(anchor="w")
        self.response_box = scrolledtext.ScrolledText(
            mid, height=4, font=("Segoe UI", 16, "bold"),
            bg="#051505", fg="#56d364", wrap=tk.WORD, relief="flat", padx=10, pady=8)
        self.response_box.pack(fill="both", expand=True, pady=(2, 4))
        tk.Label(mid, text="🔊 PRONÚNCIALO ASÍ:",
                 font=("Segoe UI", 11, "bold"), bg="#0d1117", fg="#58a6ff").pack(anchor="w")
        self.pronun_box = scrolledtext.ScrolledText(
            mid, height=2, font=("Segoe UI", 13),
            bg="#0d1f33", fg="#79c0ff", wrap=tk.WORD, relief="flat", padx=10, pady=4)
        self.pronun_box.pack(fill="x", pady=(2, 0))

        self.bot = tk.Frame(self.root, bg="#0d1117")
        bot = self.bot
        bot.pack(fill="x", padx=16, pady=(4, 0))
        tk.Label(bot, text="💡 ALTERNATIVAS:",
                 font=("Segoe UI", 10, "bold"), bg="#0d1117", fg="#d29922").pack(anchor="w")
        self.alt_box = scrolledtext.ScrolledText(
            bot, height=2, font=("Segoe UI", 11),
            bg="#1c1a00", fg="#e3b341", wrap=tk.WORD, relief="flat", padx=10, pady=4)
        self.alt_box.pack(fill="x", pady=(1, 4))
        tk.Label(bot, text="❓ POSIBLES PREGUNTAS:",
                 font=("Segoe UI", 10, "bold"), bg="#0d1117", fg="#bc8cff").pack(anchor="w")
        self.questions_box = scrolledtext.ScrolledText(
            bot, height=2, font=("Segoe UI", 11),
            bg="#1a0d33", fg="#d2a8ff", wrap=tk.WORD, relief="flat", padx=10, pady=4)
        self.questions_box.pack(fill="x", pady=(1, 0))

        bf = tk.Frame(self.root, bg="#161b22", pady=6)
        bf.pack(fill="x")
        self.rec_btn = tk.Button(bf, text="🔴 CTRL=graba",
                                  font=("Segoe UI", 12, "bold"),
                                  bg="#238636", fg="white", relief="flat",
                                  padx=20, pady=10, cursor="hand2",
                                  command=self.start_recording)
        self.rec_btn.pack(side="left", padx=(16, 6))
        self.stop_btn = tk.Button(bf, text="⏹ DETENER",
                                   font=("Segoe UI", 11),
                                   bg="#6e3030", fg="white", relief="flat",
                                   padx=14, pady=10, state="disabled",
                                   cursor="hand2", command=self.stop_recording)
        self.stop_btn.pack(side="left", padx=4)
        tk.Button(bf, text="📋", font=("Segoe UI", 12), bg="#30363d", fg="#e6edf3",
                  relief="flat", padx=10, pady=10, cursor="hand2",
                  command=self.copy_response).pack(side="left", padx=4)
        tk.Button(bf, text="🗑", font=("Segoe UI", 12), bg="#30363d", fg="#e6edf3",
                  relief="flat", padx=10, pady=10, cursor="hand2",
                  command=self.clear_all).pack(side="left", padx=4)

        for key in ("<KeyPress-Control_L>", "<KeyPress-Control_R>"):
            self.root.bind(key, self._ptt_press)
        for key in ("<KeyRelease-Control_L>", "<KeyRelease-Control_R>"):
            self.root.bind(key, self._ptt_release)
        self.root.bind("<Escape>", lambda e: self.stop_recording())

    def toggle_ghost(self):
        self._ghost_mode = not self._ghost_mode
        if self._ghost_mode:
            self.transcript_frame.pack_forget()
            self.status_lbl.pack_forget()
            self.bot.pack_forget()
            self.root.geometry("900x360")
        else:
            self.status_lbl.pack()
            self.transcript_frame.pack(fill="x", padx=16)
            self.bot.pack(fill="x", padx=16, pady=(4, 0))
            self.root.geometry("900x820")

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
        self.rec_btn.config(state="disabled", text="🔴 Grabando...", bg="#b91c1c")
        self.stop_btn.config(state="normal", bg="#e74c3c")
        self.q.put(("status", "🔴 Capturando sistema... suelta CTRL al terminar"))
        threading.Thread(target=self._record_thread, daemon=True).start()

    def stop_recording(self):
        if not self.is_recording:
            return
        self._stop_flag.set()
        self.stop_btn.config(state="disabled", bg="#6e3030")
        self.q.put(("status", "⏳ Procesando..."))

    def _record_thread(self):
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            try:
                import pyaudio
            except ImportError:
                self.q.put(("error", "❌ pip install PyAudioWPatch"))
                self.q.put(("done", None))
                return

        pa = pyaudio.PyAudio()
        try:
            idx, rate, ch = _get_loopback(pa, pyaudio)
            if idx is None:
                self.q.put(("error", "❌ Sin loopback. Activa 'Mezcla estéreo' en Windows."))
                return
            pcm = _record_stream(pa, pyaudio, idx, rate, ch, self._stop_flag)
        except Exception as exc:
            self.q.put(("error", f"❌ Grabación: {exc}"))
            self.q.put(("done", None))
            pa.terminate()
            return
        finally:
            pa.terminate()

        if not pcm:
            self.q.put(("error", "⚠️ No se grabó audio"))
            self.q.put(("done", None))
            return

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        _save_wav(pcm, tmp)

        self.q.put(("status", "⏳ Transcribiendo..."))
        try:
            text = _transcribe_wav(tmp)
        except Exception as exc:
            self.q.put(("error", f"⚠️ STT: {exc}"))
            self.q.put(("done", None))
            return
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

        self.q.put(("transcript", text))
        self.q.put(("status", "💭 Generando respuesta + pronunciación..."))
        self.q.put(("response_clear", None))

        fallback_triggered = [False]
        _buf = [""]

        def _parse_and_queue(full_text):
            self.q.put(("response_clear", None))
            text_clean = full_text.strip()
            if "📢" in text_clean:
                before, after = text_clean.split("📢", 1)
                eng = before.strip()
                rest = after.strip()
                pron, alts, qs = rest, "", ""
                if "ALT1:" in rest:
                    pron, tail = rest.split("ALT1:", 1)
                    pron = pron.strip()
                    if "Q1:" in tail:
                        alts_part, qs_part = tail.split("Q1:", 1)
                        alts = ("ALT1:" + alts_part).strip()
                        qs = ("Q1:" + qs_part).strip()
                    else:
                        alts = ("ALT1:" + tail).strip()
                elif "Q1:" in rest:
                    pron, qs_part = rest.split("Q1:", 1)
                    pron = pron.strip()
                    qs = ("Q1:" + qs_part).strip()
                self.q.put(("stream_chunk", eng))
                self.q.put(("stream_phonetic", pron))
                if alts:
                    self.q.put(("stream_alt", alts))
                if qs:
                    self.q.put(("stream_questions", qs))
            else:
                self.q.put(("stream_chunk", text_clean))

        def on_chunk(token):
            _buf[0] += token
            self.q.put(("status", f"💭 Generando... ({len(_buf[0])} chars)"))

        def on_done():
            _parse_and_queue(_buf[0])
            self.q.put(("status", "✅ Listo — lee la respuesta en voz alta"))
            self.q.put(("done", None))

        def on_error(err):
            if not fallback_triggered[0]:
                fallback_triggered[0] = True
                self.q.put(("status", "⚠️ Ollama falló → Anthropic..."))
                try:
                    resp = _anthropic_response(text)
                    _parse_and_queue(resp)
                    self.q.put(("status", "✅ Listo (Anthropic fallback)"))
                except Exception as e2:
                    self.q.put(("error", f"❌ Anthropic: {e2}"))
                self.q.put(("done", None))

        _ollama_stream(text, on_chunk, on_done, on_error)

    def _process_queue(self):
        try:
            while True:
                event, data = self.q.get_nowait()
                if event == "transcript":
                    self.transcript_box.delete("1.0", tk.END)
                    self.transcript_box.insert(tk.END, data)
                elif event == "response_clear":
                    self.response_box.delete("1.0", tk.END)
                    self.pronun_box.delete("1.0", tk.END)
                    self.alt_box.delete("1.0", tk.END)
                    self.questions_box.delete("1.0", tk.END)
                elif event == "stream_chunk":
                    self.response_box.insert(tk.END, data)
                    self.response_box.see(tk.END)
                elif event == "stream_phonetic":
                    self.pronun_box.insert(tk.END, data)
                    self.pronun_box.see(tk.END)
                elif event == "stream_alt":
                    self.alt_box.insert(tk.END, data)
                    self.alt_box.see(tk.END)
                elif event == "stream_questions":
                    self.questions_box.insert(tk.END, data)
                    self.questions_box.see(tk.END)
                elif event in ("status", "error"):
                    self.status_var.set(data)
                elif event == "done":
                    self.is_recording = False
                    self.rec_btn.config(state="normal", text="🔴 CTRL=graba", bg="#238636")
                    self.stop_btn.config(state="disabled", bg="#6e3030")
        except queue.Empty:
            pass
        self.root.after(50, self._process_queue)

    def submit_text(self):
        text = self.transcript_box.get("1.0", tk.END).strip()
        if not text or self.is_recording:
            return
        self.is_recording = True
        self.rec_btn.config(state="disabled", bg="#6e3030")
        self.q.put(("response_clear", None))
        self.q.put(("status", "💭 Generando respuesta..."))
        threading.Thread(target=self._run_ollama, args=(text,), daemon=True).start()

    def _run_ollama(self, text):
        fallback_triggered = [False]
        _buf = [""]

        def _parse_and_queue(full_text):
            self.q.put(("response_clear", None))
            text_clean = full_text.strip()
            if "📢" in text_clean:
                before, after = text_clean.split("📢", 1)
                eng = before.strip()
                rest = after.strip()
                if "ALT1:" in rest:
                    pron, tail = rest.split("ALT1:", 1)
                    pron = pron.strip()
                    if "Q1:" in tail:
                        alts_part, qs_part = tail.split("Q1:", 1)
                        alts = ("ALT1:" + alts_part).strip()
                        qs = ("Q1:" + qs_part).strip()
                    else:
                        alts = ("ALT1:" + tail).strip()
                        qs = ""
                    self.q.put(("stream_chunk", eng))
                    self.q.put(("stream_phonetic", pron))
                    if alts:
                        self.q.put(("stream_alt", alts))
                    if qs:
                        self.q.put(("stream_questions", qs))
                elif "Q1:" in rest:
                    pron, qs_part = rest.split("Q1:", 1)
                    self.q.put(("stream_chunk", eng))
                    self.q.put(("stream_phonetic", pron.strip()))
                    self.q.put(("stream_questions", ("Q1:" + qs_part).strip()))
                else:
                    self.q.put(("stream_chunk", eng))
                    self.q.put(("stream_phonetic", rest))
            else:
                self.q.put(("stream_chunk", text_clean))

        def on_chunk(token):
            _buf[0] += token
            self.q.put(("status", f"💭 Generando... ({len(_buf[0])} chars)"))

        def on_done():
            _parse_and_queue(_buf[0])
            self.q.put(("status", "✅ Listo — lee la respuesta en voz alta"))
            self.q.put(("done", None))

        def on_error(err):
            if not fallback_triggered[0]:
                fallback_triggered[0] = True
                try:
                    resp = _anthropic_response(text)
                    _parse_and_queue(resp)
                    self.q.put(("status", "✅ Listo (Anthropic fallback)"))
                except Exception as e2:
                    self.q.put(("error", f"❌ {e2}"))
                self.q.put(("done", None))

        _ollama_stream(text, on_chunk, on_done, on_error)

    def copy_response(self):
        text = self.response_box.get("1.0", tk.END).strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_var.set("✅ Copiado")

    def clear_all(self):
        self.transcript_box.delete("1.0", tk.END)
        self.response_box.delete("1.0", tk.END)
        self.pronun_box.delete("1.0", tk.END)
        self.alt_box.delete("1.0", tk.END)
        self.questions_box.delete("1.0", tk.END)
        self.status_var.set("✅ Mantén CTRL para grabar sistema, suelta para respuesta")

    def _on_close(self):
        if not self.is_recording:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = EnglishAssistantPro()
    app.run()
