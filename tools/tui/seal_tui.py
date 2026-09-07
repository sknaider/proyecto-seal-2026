#!/usr/bin/env python3
"""SEAL TUI — lanzador de la terminal propia, conectada al chat real.

POR QUE EXISTE (NEXUS, 1-sep-2026, orden de William "abre hermes soul con el
codigo propio"): `tools/tui/app.py` es nuestra terminal, portada de Hermes y
escrita nativa — 288 lineas, 14 tests verdes, cero dependencias externas.
Tenia `TUIApp.run()` pero NINGUN punto de entrada: no habia forma de abrirla.
Era una de las 6 de 7 piezas absorbidas que nadie llamaba. Esto la enchufa.

SOLO LECTURA a proposito: muestra el canal en vivo, no publica. Un TUI que
escribiera al canal lo haria con la credencial de NEXUS, o sea firmando como
NEXUS lo que escribe otro. Para escribir esta `scripts/seal_send.py`.

    python3 tools/tui/seal_tui.py                 # web_chat
    python3 tools/tui/seal_tui.py user:3:gtl-sistemas
"""
from __future__ import annotations

import json
from datetime import datetime
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.tui.app import TUIApp, TUIMessage  # noqa: E402

CHAT = "http://localhost:8765/api/chat/messages"
INTERVALO_S = 3


def leer(canal: str, limite: int = 30) -> tuple[list[dict], str | None]:
    """Devuelve (mensajes, error). El error NO se traga.

    Antes devolvia [] tanto si el canal estaba vacio como si el chat estaba
    caido: los dos casos se veian IGUAL en pantalla. Lo encontro FABLE
    revisando esto (1-sep-2026). Una ausencia no es evidencia si el sistema
    no podia producir la presencia.
    """
    url = f"{CHAT}?channel={urllib.parse.quote(canal)}&limit={limite}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read().decode()).get("messages", []), None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


def linea(m: dict) -> str:
    # La API devuelve created_at en UTC ("...T00:31:48+00:00"). Rebanar [11:19]
    # pinta la hora de OTRO huso: William abre a las 19:33 y ve 00:xx, que no se
    # ve como un error sino como una pantalla congelada desde la medianoche.
    # Lo encontro FABLE (1-sep-2026). astimezone() sin argumento usa el huso de
    # la maquina, que es el de el.
    ts = (m.get("created_at") or "")[11:19]
    try:
        ts = datetime.fromisoformat(
            (m.get("created_at") or "").replace("Z", "+00:00")
        ).astimezone().strftime("%H:%M:%S")
    except (ValueError, TypeError):
        pass
    quien = (m.get("sender_name") or "?")
    txt = (m.get("content") or "").replace("\n", " ")
    return f"[{ts}] {quien}: {txt}"


def empujar(app: TUIApp, texto: str, rol: str = "agent") -> None:
    """Empuja con hora LOCAL.

    `TUIMessage` pone `datetime.now(timezone.utc)` por defecto y `render()` no
    convierte: la pantalla mostraria 00:33 cuando el reloj de William dice 19:33
    — no se ve como un error, se ve como una pantalla congelada. No toco app.py
    ni su test (fijan el render en UTC a proposito): paso el timestamp ya en el
    huso local. FABLE lo levanto, 1-sep-2026.
    """
    app.transcript.append(
        TUIMessage(role=rol, text=texto, received_at=datetime.now().astimezone())
    )


def main() -> int:
    canal = sys.argv[1] if len(sys.argv) > 1 else "web_chat"
    app = TUIApp(title=f"SEAL · {canal} · solo lectura")

    inicial, error = leer(canal)
    if error:
        empujar(app, f"— SIN CONEXION al chat ({CHAT}) — {error}", "system")
    elif not inicial:
        empujar(
            app,
            f"canal '{canal}' vacio (el chat SI responde). "
            f"Los canales user:* no los sirve este endpoint.",
            "system",
        )
    for m in inicial:
        empujar(app, linea(m))

    vistos = {m.get("id") for m in inicial}

    def seguir() -> None:
        caido = False
        while not app.is_stopped():
            nuevos, err = leer(canal)
            if err and not caido:
                caido = True
                empujar(app, f"— SIN CONEXION al chat — {err}", "system")
            elif not err and caido:
                caido = False
                empujar(app, "— conexion al chat restablecida —", "system")
            for m in nuevos:
                if m.get("id") not in vistos:
                    vistos.add(m.get("id"))
                    empujar(app, linea(m))
            time.sleep(INTERVALO_S)

    hilo = threading.Thread(target=seguir, daemon=True)
    hilo.start()
    try:
        app.run()
    except Exception as exc:  # curses necesita una terminal real
        print(f"no se pudo abrir la terminal: {type(exc).__name__}: {exc}")
        print("corrélo en una terminal interactiva, no por tuberia.")
        return 1
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
