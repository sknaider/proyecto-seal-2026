#!/usr/bin/env python3
"""Vigilante del juez: sigue messages/william_channel.jsonl (la fuente que alimenta
a todos los monitores; no se trunca cuando reinician unidades) y emite una línea
por cada mensaje nuevo del canal `fable-juez` o del DM dm:fable:william que NO sea
de FABLE. Sin buffers (flush por línea). Detecta truncado/rotación y reabre.

Uso (dentro de la sesión FABLE JUEZ, como Monitor persistente):
    python3 -u fable/fable_juez_watch.py
"""
import json, os, sys, time

PATH = "/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl"
CHANNELS = {"fable-juez", "dm:fable:william"}


def follow(path: str):
    f = None; ino = None; pos = 0
    while True:
        try:
            st = os.stat(path)
        except FileNotFoundError:
            time.sleep(1); continue
        if f is None or st.st_ino != ino:
            if f: f.close()
            f = open(path, "r", encoding="utf-8", errors="replace"); f.seek(0, 2); ino = st.st_ino; pos = f.tell()
        if st.st_size < pos:            # truncado: volver al inicio
            f.seek(0); pos = 0
        line = f.readline()
        if not line:
            time.sleep(0.5); continue
        pos = f.tell()
        yield line


def main() -> int:
    print("[fable-juez watch] escuchando fable-juez y dm:fable:william", flush=True)
    for line in follow(PATH):
        try:
            d = json.loads(line)
        except Exception:
            continue
        ch = str(d.get("channel", "")).lower()
        if ch not in CHANNELS:
            continue
        if str(d.get("from", "")).upper() == "FABLE":
            continue
        msg = (d.get("message") or d.get("content") or "").replace("\n", " ")
        tag = "[FABLE JUEZ]" if ch == "fable-juez" else "[DM]"
        print(f"{tag} {str(d.get('timestamp',''))[11:19]} de {d.get('from')} id={d.get('id')} :: {msg[:600]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
