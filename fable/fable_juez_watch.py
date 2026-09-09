#!/usr/bin/env python3
"""Vigilante del juez: sigue messages/william_channel.jsonl (la fuente que alimenta
a todos los monitores; no se trunca cuando reinician unidades) y emite una línea
por cada mensaje nuevo del canal `fable-juez` o del DM dm:fable:william que NO sea
de FABLE. Sin buffers (flush por línea). Detecta truncado/rotación y reabre.

Uso (dentro de la sesión FABLE JUEZ, como Monitor persistente):
    python3 -u fable/fable_juez_watch.py
"""
import json, os, sys, time

CLAVE_DM = "/home/dadito/IA/proyecto-seal/messages/.dm_encryption_key"
_fernet = None


def _descifrador():
    """Fernet perezoso, o None si no hay clave. NUNCA devuelve ni imprime la clave."""
    global _fernet
    if _fernet is None:
        try:
            from cryptography.fernet import Fernet
            _fernet = Fernet(open(CLAVE_DM, "rb").read().strip())
        except Exception:
            _fernet = False          # False = probado y no disponible; no reintenta cada línea
    return _fernet or None


def descifra(evento: dict) -> dict:
    """Devuelve el evento con `message` en claro si venía cifrado.

    HUECO QUE ESTE ARREGLO CIERRA (FABLE lo midió el 8-sep, sobre mi propio
    arreglo del prefijo): los DM se guardan CIFRADOS en el jsonl
    (`chat_server.py:454`, `_encrypt_for_jsonl`). Con el prefijo corregido, un DM
    **pasaba el filtro y llegaba como `gAAAAABq…`** — el juez lo recibía y seguía
    sin poder leerlo. **Pasar el filtro no es lo mismo que poder leerse**, y mis
    22 brazos no ejercían ni un DM cifrado, así que ninguno lo vio.

    Falla ABIERTO a propósito: si la clave no está o no descifra, se devuelve el
    evento con una marca legible en vez de romper el vigilante. Un juez sin
    vigilante es peor que un juez con un mensaje ilegible, y la marca dice cuál
    de los dos casos es.
    """
    if not evento.get("_encrypted"):
        return evento
    f = _descifrador()
    salida = dict(evento)
    if f is None:
        salida["message"] = "[DM cifrado — no hay clave para descifrarlo aquí]"
        return salida
    try:
        salida["message"] = f.decrypt(str(evento.get("message", "")).encode("ascii")).decode("utf-8")
        salida.pop("_encrypted", None)
    except Exception:
        salida["message"] = "[DM cifrado — la clave no coincide]"
    return salida


PATH = "/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl"

# CANAL FIJO del juez. Los DM se aceptan por PREFIJO, no por lista.
CANAL_JUEZ = "fable-juez"
PREFIJO_DM = "dm:fable:"


def escucha(canal: str) -> bool:
    """¿Este canal le corresponde al juez?

    ARREGLO 8-sep-2026 (NEXUS, por orden de William: «cablea y repara las
    conexiones de FABLE»). Antes era una lista literal:

        CHANNELS = {"fable-juez", "dm:fable:william"}

    o sea que FABLE sólo veía los DM de William. Medido ese día: el caso que le
    mandé yo (`dm:fable:nexus`, 5 mensajes) y los archivos de JARVIS
    (`dm:fable:jarvis`, 2) **estaban guardados en la base y su sesión nunca los
    vio**. Los tres veredictos que William reclamaba no faltaban por lentitud
    del juez: no le habían llegado.

    El criterio correcto no es enumerar quién puede escribirle —esa lista se
    queda vieja en cuanto entra un agente— sino el PREFIJO: **un DM dirigido a
    FABLE le corresponde a FABLE, venga de quien venga.**
    """
    c = (canal or "").strip().lower()
    return c == CANAL_JUEZ or c.startswith(PREFIJO_DM)


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
    # El anuncio se DERIVA de la regla: si mañana cambia el criterio y esta
    # línea siguiera fija, mentiría sobre lo que el proceso hace.
    print(f"[fable-juez watch] escuchando {CANAL_JUEZ} y {PREFIJO_DM}* "
          f"(cualquier agente, no solo William)", flush=True)
    for line in follow(PATH):
        try:
            d = json.loads(line)
        except Exception:
            continue
        ch = str(d.get("channel", "")).lower()
        if not escucha(ch):
            continue
        if str(d.get("from", "")).upper() == "FABLE":
            continue
        d = descifra(d)
        msg = (d.get("message") or d.get("content") or "").replace("\n", " ")
        tag = "[FABLE JUEZ]" if ch == CANAL_JUEZ else "[DM]"
        print(f"{tag} {str(d.get('timestamp',''))[11:19]} de {d.get('from')} id={d.get('id')} :: {msg[:600]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
